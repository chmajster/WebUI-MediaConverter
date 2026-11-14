from __future__ import annotations

import json
import functools
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .files import local_file


NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def locate_binary(configured: str, name: str) -> str | None:
    candidate = configured.strip().strip('"') if configured else ''
    if candidate:
        path = Path(candidate).expanduser()
        if path.is_dir():
            path = path / (name + ('.exe' if os.name == 'nt' else ''))
        return str(path.resolve()) if path.is_file() else None
    return shutil.which(name)


def probe(path: str | Path, ffprobe: str) -> dict[str, Any]:
    source = local_file(str(path))
    if not ffprobe:
        raise ValueError('Nie znaleziono FFprobe. Ustaw jego ścieżkę w ustawieniach.')
    command = [ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe,crypto,data',
               '-show_format', '-show_streams', '-of', 'json', str(source)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=45, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f'Nie udało się uruchomić FFprobe: {exc}') from exc
    if result.returncode:
        message = result.stderr.decode('utf-8', errors='replace').strip()
        raise ValueError(f'Nie udało się odczytać pliku {source.name}: {message[-800:]}')
    try:
        data = json.loads(result.stdout.decode('utf-8', errors='replace'))
    except json.JSONDecodeError as exc:
        raise ValueError('FFprobe zwrócił nieprawidłowe dane.') from exc
    data['path'] = str(source)
    data['size'] = source.stat().st_size
    return data


def streams(media: dict, kind: str) -> list[dict]:
    return [s for s in media.get('streams', []) if s.get('codec_type') == kind]


def duration(media: dict) -> float:
    try:
        return max(0.0, float(media.get('format', {}).get('duration', 0)))
    except (ValueError, TypeError):
        return 0.0


def encoder_names(ffmpeg: str) -> set[str]:
    if not ffmpeg:
        return set()
    try:
        result = subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True,
                                timeout=30, creationflags=NO_WINDOW)
        return set(re.findall(r'^\s*[VAS][A-Z.]{5}\s+(\S+)', result.stdout.decode('utf-8', errors='replace'), re.M))
    except (OSError, subprocess.TimeoutExpired):
        return set()


@functools.lru_cache(maxsize=128)
def encoder_capabilities(ffmpeg: str, encoder: str) -> dict[str, set[str]]:
    try:
        result = subprocess.run([ffmpeg, '-hide_banner', '-h', f'encoder={encoder}'],
                                capture_output=True, timeout=10, creationflags=NO_WINDOW)
        output = result.stdout.decode('utf-8', errors='replace')
        def values(label):
            match = re.search(r'^\s*' + re.escape(label) + r':\s*(.+)$', output, re.M)
            return set(match.group(1).split()) if match else set()
        return {'pixel_formats': values('Supported pixel formats'), 'sample_rates': values('Supported sample rates')}
    except (OSError, subprocess.TimeoutExpired):
        return {'pixel_formats': set(), 'sample_rates': set()}


HARDWARE = {
    'nvidia': {'h264': 'h264_nvenc', 'hevc': 'hevc_nvenc', 'av1': 'av1_nvenc'},
    'intel': {'h264': 'h264_qsv', 'hevc': 'hevc_qsv', 'av1': 'av1_qsv'},
    'amd': {'h264': 'h264_amf', 'hevc': 'hevc_amf', 'av1': 'av1_amf'},
}


def detect_hardware(ffmpeg: str, encoders: set[str] | None = None) -> dict[str, dict[str, bool]]:
    """A listed hardware encoder is available only after an actual one-frame encode."""
    encoders = encoders if encoders is not None else encoder_names(ffmpeg)
    result = {vendor: {} for vendor in HARDWARE}
    for vendor, codecs in HARDWARE.items():
        for codec, encoder in codecs.items():
            if encoder not in encoders:
                result[vendor][codec] = False
                continue
            cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                   'color=c=black:s=128x128:r=1', '-frames:v', '1', '-c:v', encoder,
                   '-f', 'null', '-']
            try:
                test = subprocess.run(cmd, capture_output=True, timeout=12, creationflags=NO_WINDOW)
                result[vendor][codec] = test.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                result[vendor][codec] = False
    return result


def gpu_load() -> str | None:
    command = shutil.which('nvidia-smi')
    if not command:
        return None
    try:
        process = subprocess.run([command, '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                                 capture_output=True, timeout=2, creationflags=NO_WINDOW)
        first = process.stdout.decode('utf-8', errors='replace').strip().splitlines()[0]
        return f'{int(first.strip())}%'
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return None
