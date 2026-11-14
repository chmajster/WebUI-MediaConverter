from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .files import local_file
from .media import HARDWARE, duration, encoder_capabilities, streams
from .models import (AUDIO_ENCODERS, AUDIO_FORMATS, LOSSLESS_AUDIO, VIDEO_AUDIO,
                     VIDEO_ENCODERS, VIDEO_FORMATS, ConversionSpec)


COPY_AUDIO = {
    'mp3': {'mp3'}, 'wav': {'pcm_s16le', 'pcm_s24le', 'pcm_s32le', 'pcm_f32le'},
    'flac': {'flac'}, 'aac': {'aac'}, 'm4a': {'aac', 'alac', 'mp3'},
    'ogg': {'vorbis', 'opus', 'flac'}, 'opus': {'opus'}, 'wma': {'wmav1', 'wmav2'},
    'aiff': {'pcm_s16be', 'pcm_s24be', 'pcm_s32be'}, 'alac': {'alac'},
}
CODEC_ALIASES = {'libvorbis': 'vorbis', 'libopus': 'opus', 'libmp3lame': 'mp3'}


def selected_stream(media: dict, kind: str, index: int | None, required: bool = True) -> dict | None:
    choices = streams(media, kind)
    if index is None:
        result = choices[0] if choices else None
    else:
        result = next((s for s in choices if s.get('index') == index), None)
    if required and result is None:
        raise ValueError(f'Plik nie zawiera wybranej ścieżki {"wideo" if kind == "video" else "audio" if kind == "audio" else "napisów"}.')
    return result


def audio_filters(spec: ConversionSpec) -> list[str]:
    filters = []
    if spec.normalize:
        filters.append('loudnorm=I=-16:LRA=11:TP=-1.5')
    if spec.volume_db:
        filters.append(f'volume={spec.volume_db}dB')
    speed = spec.speed
    while speed > 2:
        filters.append('atempo=2')
        speed /= 2
    while speed < .5:
        filters.append('atempo=0.5')
        speed /= .5
    if speed != 1:
        filters.append(f'atempo={speed:.6f}')
    return filters


def video_filters(spec: ConversionSpec) -> list[str]:
    filters = []
    if spec.rotate == 90: filters.append('transpose=clock')
    if spec.rotate == 180: filters.extend(['hflip', 'vflip'])
    if spec.rotate == 270: filters.append('transpose=cclock')
    if spec.width and spec.height:
        filters.append(f'scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease:force_divisible_by=2')
    if spec.aspect != 'auto': filters.append(f'setaspect={spec.aspect}')
    if spec.speed != 1: filters.append(f'setpts=PTS/{spec.speed}')
    if spec.fps: filters.append(f'fps={spec.fps}')
    return filters


def encoder_for(spec: ConversionSpec, hardware: dict, available: set[str], force_cpu: bool = False) -> tuple[str, str | None]:
    return select_video_encoder(spec.video_codec, spec.format, spec.device, spec.fallback, hardware, available, force_cpu)


def select_video_encoder(video_codec: str, output_format: str, device: str, fallback: bool,
                         hardware: dict, available: set[str], force_cpu: bool = False) -> tuple[str, str | None]:
    codec = video_codec if video_codec != 'auto' else VIDEO_FORMATS[output_format][0]
    if codec == 'copy':
        return 'copy', None
    if not force_cpu and device != 'cpu':
        vendors = ('nvidia', 'intel', 'amd') if device == 'auto' else (device,)
        for vendor in vendors:
            encoder = HARDWARE.get(vendor, {}).get(codec)
            if encoder and hardware.get(vendor, {}).get(codec) and encoder in available:
                return encoder, None
        if device != 'auto' and not fallback:
            raise ValueError(f'Wybrana akceleracja {device.upper()} nie obsługuje kodeka {codec}.')
        if device != 'auto':
            warning = f'Akceleracja {device.upper()} nie obsługuje kodeka {codec}; użyto CPU.'
        else:
            warning = None
    else:
        warning = 'Sprzętowe kodowanie nie powiodło się; ponawianie na CPU.' if force_cpu else None
    encoder = VIDEO_ENCODERS[codec]
    if encoder not in available:
        raise ValueError(f'Ta wersja FFmpeg nie zawiera wymaganego enkodera {encoder}.')
    return encoder, warning


def check_merge(media: list[dict], operation: str):
    kind = 'video' if operation == 'merge_video' else 'audio'
    for item in media:
        selected_stream(item, kind, None)
    def signature(item):
        relevant = [s for s in item.get('streams', []) if s.get('codec_type') in ('video', 'audio')]
        return [(s.get('codec_type'), s.get('codec_name'), s.get('width'), s.get('height'),
                 s.get('sample_rate'), s.get('channels'), s.get('channel_layout'), s.get('time_base')) for s in relevant]
    if any(signature(item) != signature(media[0]) for item in media[1:]):
        raise ValueError('Łączenie wymaga zgodnych strumieni, kodeków, rozdzielczości, kanałów i podstaw czasu.')


def build_command(ffmpeg: str, spec: ConversionSpec, media: list[dict], output: Path,
                  available: set[str], hardware: dict, concat_file: Path | None = None,
                  force_cpu: bool = False) -> tuple[list[str], str | None]:
    if not ffmpeg:
        raise ValueError('Nie znaleziono FFmpeg. Ustaw jego ścieżkę w ustawieniach.')
    if not media:
        raise ValueError('Brak danych źródłowych.')
    source = media[0]
    for path in spec.sources:
        local_file(path)
    if duration(source) and spec.start >= duration(source):
        raise ValueError('Początek fragmentu znajduje się poza plikiem.')
    merging = spec.operation in ('merge_video', 'merge_audio')
    if merging:
        check_merge(media, spec.operation)
        if not concat_file:
            raise ValueError('Brak listy plików do połączenia.')
    encoder = None
    warning = None
    if spec.operation in ('video', 'merge_video'):
        encoder, warning = encoder_for(spec, hardware, available, force_cpu)
        if encoder != 'copy' and spec.pixel_format != 'auto':
            supported = encoder_capabilities(ffmpeg, encoder)['pixel_formats']
            if supported and spec.pixel_format not in supported:
                if any(tag in encoder for tag in ('_nvenc', '_qsv', '_amf')) and spec.fallback:
                    encoder, warning = encoder_for(spec, hardware, available, True)
                    supported = encoder_capabilities(ffmpeg, encoder)['pixel_formats']
                if supported and spec.pixel_format not in supported:
                    raise ValueError(f'Enkoder {encoder} nie obsługuje formatu pikseli {spec.pixel_format}.')
    args = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-loglevel', 'warning', '-progress', 'pipe:1', '-nostats']
    if spec.cuda_decode and encoder and '_nvenc' in encoder and not force_cpu:
        args += ['-hwaccel', 'cuda']
    elif spec.cuda_decode and not force_cpu:
        if not spec.fallback:
            raise ValueError('Dekodowanie CUDA wymaga działającego enkodera NVIDIA.')
        warning = 'Dekodowanie CUDA jest niedostępne; użyto dekodowania CPU.'
    args += ['-protocol_whitelist', 'file,pipe,crypto,data']
    if merging:
        args += ['-f', 'concat', '-safe', '0', '-i', str(concat_file)]
    else:
        args += ['-i', str(local_file(spec.sources[0]))]
    extra_audio = spec.external_audio and spec.operation in ('video', 'merge_video')
    extra_subtitle = spec.external_subtitle and spec.operation == 'video'
    if extra_audio:
        args += ['-protocol_whitelist', 'file,pipe,crypto,data', '-i', str(local_file(spec.external_audio))]
    if extra_subtitle:
        args += ['-protocol_whitelist', 'file,pipe,crypto,data', '-i', str(local_file(spec.external_subtitle))]
    if spec.start: args += ['-ss', str(spec.start)]
    if spec.end is not None: args += ['-t', str(spec.end - spec.start)]
    args += ['-map_metadata', '0']
    for key, value in (('title', spec.metadata_title), ('artist', spec.metadata_artist), ('album', spec.metadata_album)):
        if value: args += ['-metadata', f'{key}={value}']
    op = spec.operation
    if op in ('video', 'merge_video', 'gif', 'frames', 'thumbnail'):
        vstream = selected_stream(source, 'video', None)
        args += ['-map', f'0:{vstream["index"]}']
        vf = video_filters(spec)
        if op == 'gif' and not spec.fps: vf.append('fps=12')
        if op == 'frames' and not spec.fps: vf.append('fps=1')
        if vf: args += ['-vf', ','.join(vf)]
        if op in ('frames', 'thumbnail'):
            if op == 'thumbnail': args += ['-frames:v', '1', '-update', '1']
            args += ['-q:v', '2', str(output)]
            return args, warning
        if op == 'gif':
            args += ['-an', '-loop', '0', str(output)]
            return args, warning
        if encoder == 'copy':
            src_codec = vstream.get('codec_name')
            if src_codec not in VIDEO_FORMATS[spec.format]:
                raise ValueError(f'Kontener {spec.format.upper()} nie obsługuje kopiowania kodeka {src_codec}.')
        args += ['-c:v', encoder]
        if encoder != 'copy':
            if spec.video_bitrate:
                args += ['-b:v', f'{spec.video_bitrate}k']
            elif '_nvenc' in encoder:
                args += ['-cq', str(spec.crf), '-b:v', '0']
            elif '_qsv' in encoder:
                args += ['-global_quality', str(spec.crf)]
            elif '_amf' in encoder:
                args += ['-rc', 'cqp', '-qp_i', str(spec.crf), '-qp_p', str(spec.crf)]
            elif encoder in ('libx264', 'libx265'):
                args += ['-crf', str(spec.crf), '-preset', spec.preset]
            elif encoder in ('libvpx', 'libvpx-vp9', 'libaom-av1'):
                args += ['-crf', str(min(63, round(spec.crf * 63 / 51))), '-b:v', '0']
                if encoder == 'libaom-av1': args += ['-cpu-used', '6']
            else:
                args += ['-q:v', str(max(2, min(31, round(spec.crf * 31 / 51))))]
            if spec.pixel_format != 'auto': args += ['-pix_fmt', spec.pixel_format]
            elif spec.format in ('mp4', 'm4v', 'mov'): args += ['-pix_fmt', 'yuv420p']
        if spec.threads: args += ['-threads', str(spec.threads)]
        if spec.audio_codec != 'none':
            audio = selected_stream(source, 'audio', spec.audio_stream, required=spec.audio_stream is not None) if not extra_audio else None
            if extra_audio or audio:
                args += ['-map', '1:a:0' if extra_audio else f'0:{audio["index"]}']
                acodec = spec.audio_codec if spec.audio_codec != 'auto' else VIDEO_AUDIO[spec.format][0]
                if acodec == 'copy' and not extra_audio:
                    if audio.get('codec_name') not in VIDEO_AUDIO[spec.format]:
                        raise ValueError('Kodek źródłowego audio nie jest zgodny z wybranym kontenerem.')
                audio_encoder = 'copy' if acodec == 'copy' else AUDIO_ENCODERS[acodec]
                if audio_encoder != 'copy' and audio_encoder not in available:
                    raise ValueError(f'Ta wersja FFmpeg nie zawiera enkodera {audio_encoder}.')
                validate_sample_rate(ffmpeg, spec, audio_encoder)
                args += ['-c:a', audio_encoder]
                if acodec not in ('copy', 'alac', 'flac', 'pcm_s16le'):
                    args += ['-b:a', f'{spec.audio_bitrate}k']
                add_audio_options(args, spec, acodec)
        if extra_subtitle or spec.subtitle_stream is not None:
            if spec.format not in ('mp4', 'm4v', 'mov', 'mkv'):
                raise ValueError('Napisy są dostępne tylko dla MP4, M4V, MOV i MKV.')
            if extra_subtitle:
                args += ['-map', f'{2 if extra_audio else 1}:s:0']
                subtitle = None
            else:
                subtitle = selected_stream(source, 'subtitle', spec.subtitle_stream)
                args += ['-map', f'0:{subtitle["index"]}']
            if spec.format == 'mkv':
                args += ['-c:s', 'srt' if subtitle and subtitle.get('codec_name') in ('mov_text', 'text') else 'copy']
            elif subtitle and subtitle.get('codec_name') not in ('subrip', 'ass', 'ssa', 'mov_text', 'text'):
                raise ValueError('Wybrana ścieżka napisów nie może zostać zapisana w tym kontenerze.')
            else:
                args += ['-c:s', 'mov_text']
        if spec.format in ('mp4', 'm4v', 'mov'):
            args += ['-movflags', '+faststart']
        if spec.format == 'm4v': args += ['-f', 'mp4']
    else:
        audio = selected_stream(source, 'audio', spec.audio_stream)
        args += ['-map', f'0:{audio["index"]}', '-vn']
        encoder = AUDIO_FORMATS[spec.format][0]
        if spec.audio_codec == 'copy':
            codec = audio.get('codec_name')
            if codec not in COPY_AUDIO[spec.format]:
                raise ValueError(f'Nie można skopiować kodeka {codec} do formatu {spec.format.upper()}.')
            encoder = 'copy'
        if encoder != 'copy' and encoder not in available:
            raise ValueError(f'Ta wersja FFmpeg nie zawiera enkodera {encoder}.')
        validate_sample_rate(ffmpeg, spec, encoder)
        args += ['-c:a', encoder]
        if encoder != 'copy' and spec.format not in LOSSLESS_AUDIO:
            if spec.format == 'mp3' and spec.audio_bitrate > 320:
                raise ValueError('Maksymalny bitrate MP3 wynosi 320 kb/s.')
            args += ['-b:a', f'{spec.audio_bitrate}k']
        add_audio_options(args, spec, encoder)
    args.append(str(output))
    return args, warning


def validate_sample_rate(ffmpeg: str, spec: ConversionSpec, encoder: str):
    if encoder == 'copy' or not spec.sample_rate:
        return
    supported = encoder_capabilities(ffmpeg, encoder)['sample_rates']
    if supported and str(spec.sample_rate) not in supported:
        raise ValueError(f'Enkoder {encoder} nie obsługuje próbkowania {spec.sample_rate} Hz.')


def add_audio_options(args: list[str], spec: ConversionSpec, encoder: str):
    if encoder == 'copy': return
    if spec.sample_rate: args += ['-ar', str(spec.sample_rate)]
    if spec.channels: args += ['-ac', str(spec.channels)]
    filters = audio_filters(spec)
    if filters: args += ['-af', ','.join(filters)]


def display_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == 'nt' else __import__('shlex').join(command)
