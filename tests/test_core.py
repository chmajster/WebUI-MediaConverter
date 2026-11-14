from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.engine import build_command, encoder_for
from app.files import local_file, publish
from app.jobs import JobManager
from app.media import detect_hardware, encoder_names, probe, streams
from app.models import ConversionSpec
from app.store import Store
from app.single_instance import SingleInstance


def test_probe_and_paths(sample, binaries):
    info = probe(str(sample), binaries[1])
    assert local_file(str(sample)) == sample.resolve()
    assert info['size'] == sample.stat().st_size
    assert streams(info, 'video')[0]['codec_name'] == 'h264'
    assert streams(info, 'audio')[0]['codec_name'] == 'aac'
    assert float(info['format']['duration']) > 1
    with pytest.raises(ValueError):
        local_file(str(sample.parent / '..' / sample.parent.name / sample.name))


def test_validation_and_command(sample, binaries, tmp_path):
    common = {'sources': [str(sample)], 'output_dir': str(tmp_path)}
    with pytest.raises(ValidationError): ConversionSpec(**common, start=10, end=5)
    with pytest.raises(ValidationError): ConversionSpec(**common, format='webm', video_codec='h264')
    with pytest.raises(ValidationError): ConversionSpec(**common, video_codec='copy', width=1280, height=720)
    info = probe(sample, binaries[1])
    spec = ConversionSpec(**common, format='mp4', video_codec='h264', device='cpu', width=320, height=180, start=.2, end=1.5)
    command, warning = build_command(binaries[0], spec, [info], tmp_path / 'output.mp4', encoder_names(binaries[0]), {})
    assert warning is None
    assert command[command.index('-c:v') + 1] == 'libx264'
    assert '-progress' in command and 'pipe:1' in command and '-vf' in command
    assert str(sample) in command and '-ss' in command


def test_hardware_detection_and_cpu_fallback(sample, binaries, tmp_path):
    spec = ConversionSpec(sources=[str(sample)], output_dir=str(tmp_path), format='mp4', video_codec='h264', device='nvidia')
    encoder, warning = encoder_for(spec, {}, {'libx264'}, False)
    assert encoder == 'libx264' and 'CPU' in warning
    with pytest.raises(ValueError):
        encoder_for(spec.model_copy(update={'fallback': False}), {}, {'libx264'}, False)
    class Result:
        returncode = 0
    with patch('app.media.subprocess.run', return_value=Result()) as run:
        found = detect_hardware(binaries[0], {'h264_nvenc'})
    assert found['nvidia']['h264'] is True
    assert found['intel']['h264'] is False
    assert run.call_count == 1 and 'h264_nvenc' in run.call_args.args[0]


def test_encoder_sample_rate_validation(sample, binaries, tmp_path):
    available = encoder_names(binaries[0])
    if 'libopus' not in available: pytest.skip('Brak libopus.')
    spec = ConversionSpec(sources=[str(sample)], output_dir=str(tmp_path), operation='extract', format='opus', sample_rate=44100)
    with pytest.raises(ValueError, match='próbkowania'):
        build_command(binaries[0], spec, [probe(sample, binaries[1])], tmp_path / 'out.opus', available, {})


def test_single_instance_lock(tmp_path):
    lock = SingleInstance(tmp_path / 'app.lock')
    try:
        with pytest.raises(RuntimeError): SingleInstance(tmp_path / 'app.lock')
    finally:
        lock.close()
    other = SingleInstance(tmp_path / 'app.lock')
    other.close()


def test_publish_never_overwrites_and_streams(tmp_path, monkeypatch):
    source = tmp_path / 'partial'
    source.write_bytes(b'x' * (3 * 1024 * 1024))
    target = tmp_path / 'wynik.mp4'
    target.write_bytes('oryginał'.encode())
    result = publish(source, target)
    assert target.read_bytes() == 'oryginał'.encode() and result != target
    assert result.stat().st_size == 3 * 1024 * 1024
    source.write_bytes(b'y' * (3 * 1024 * 1024))
    monkeypatch.setattr(os, 'link', lambda *_: (_ for _ in ()).throw(OSError('brak hardlink')))
    if os.name == 'nt':
        monkeypatch.setattr(os, 'rename', lambda *_: (_ for _ in ()).throw(OSError('brak rename')))
    from app import files
    actual_copy = files.shutil.copyfileobj
    def checked_copy(src, dst, length=0):
        assert length == 1024 * 1024
        return actual_copy(src, dst, length=length)
    monkeypatch.setattr(files.shutil, 'copyfileobj', checked_copy)
    assert publish(source, tmp_path / 'inny.mp4').stat().st_size == 3 * 1024 * 1024


async def wait_for(manager, jobs, timeout=60):
    async with asyncio.timeout(timeout):
        while any(job['status'] in ('waiting', 'running') for job in jobs):
            await asyncio.sleep(.05)


@pytest.mark.asyncio
async def test_real_conversions_queue_and_frames(sample, binaries, tmp_path):
    store = Store(tmp_path / 'state.sqlite')
    manager = JobManager(store, tmp_path / 'data', *binaries, encoder_names(binaries[0]), {})
    manager.settings.concurrency = 2
    common = {'sources': [str(sample)], 'output_dir': str(tmp_path), 'device': 'cpu'}
    specs = [ConversionSpec(**common, operation='video', format='mkv', video_codec='h264', audio_codec='aac'),
             ConversionSpec(**common, operation='audio', format='mp3'),
             ConversionSpec(**common, operation='extract', format='m4a', audio_codec='copy'),
             ConversionSpec(**common, operation='gif', format='mp4'),
             ConversionSpec(**common, operation='thumbnail', format='mp4'),
             ConversionSpec(**common, operation='frames', format='mp4', fps=2),
             ConversionSpec(**{**common, 'sources':[str(sample), str(sample)]}, operation='merge_video', format='mp4', video_codec='copy', audio_codec='copy'),
             ConversionSpec(**{**common, 'sources':[str(sample), str(sample)]}, operation='merge_audio', format='mp3')]
    jobs = [await manager.add(spec) for spec in specs]
    await wait_for(manager, jobs)
    errors = [(j['spec']['operation'], j['error'], j['logs']) for j in jobs if j['status'] != 'completed']
    assert not errors, errors
    assert all(j['progress'] == 100 and Path(j['output']).is_file() for j in jobs)
    assert streams(probe(jobs[0]['output'], binaries[1]), 'video')
    assert streams(probe(jobs[1]['output'], binaries[1]), 'audio')[0]['codec_name'] == 'mp3'
    assert streams(probe(jobs[2]['output'], binaries[1]), 'audio')[0]['codec_name'] == 'aac'
    with zipfile.ZipFile(jobs[5]['output']) as archive:
        assert len(archive.namelist()) >= 2
    assert float(probe(jobs[6]['output'], binaries[1])['format']['duration']) > 3
    assert sample.is_file()
    await manager.shutdown()


def test_remote_playlist_does_not_access_network(binaries, tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(.3)
        playlist = tmp_path / 'external.m3u8'
        playlist.write_text(f'#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:{listener.getsockname()[1]}/segment.ts\n', encoding='utf-8')
        with pytest.raises(ValueError): probe(playlist, binaries[1])
        with pytest.raises(TimeoutError): listener.accept()


@pytest.mark.asyncio
async def test_cancel_and_restart_recovery(sample, binaries, tmp_path):
    store = Store(tmp_path / 'state.sqlite')
    manager = JobManager(store, tmp_path / 'data', *binaries, encoder_names(binaries[0]), {})
    spec = ConversionSpec(sources=[str(sample)], output_dir=str(tmp_path), format='mp4', device='cpu',
                          video_codec='h264', width=1920, height=1080, preset='veryslow', threads=1)
    job = await manager.add(spec)
    async with asyncio.timeout(20):
        while job['id'] not in manager.processes:
            await asyncio.sleep(.005)
    manager.cancel(job['id'])
    await wait_for(manager, [job])
    assert job['status'] == 'cancelled'
    assert not list(tmp_path.glob('*.częściowy.mp4'))
    assert sample.is_file()
    await manager.shutdown()
    orphan = dict(job, id='orphan', status='running')
    store.save_job(orphan)
    restored = Store(tmp_path / 'state.sqlite')
    assert next(j for j in restored.jobs() if j['id'] == 'orphan')['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_hardware_failure_retries_real_cpu(sample, binaries, tmp_path):
    available = encoder_names(binaries[0])
    if 'h264_nvenc' not in available: pytest.skip('FFmpeg nie zawiera h264_nvenc.')
    store = Store(tmp_path / 'state.sqlite')
    manager = JobManager(store, tmp_path / 'data', *binaries, available, {'nvidia': {'h264': True}})
    spec = ConversionSpec(sources=[str(sample)], output_dir=str(tmp_path), format='mp4',
                          video_codec='h264', device='nvidia', fallback=True)
    original = manager._process
    calls = []
    async def failing_gpu(job, command, started):
        calls.append(command)
        if 'h264_nvenc' in command: return 1
        return await original(job, command, started)
    manager._process = failing_gpu
    job = await manager.add(spec)
    await wait_for(manager, [job])
    assert job['status'] == 'completed', job['error']
    assert 'h264_nvenc' in calls[0] and 'libx264' in calls[1]
    assert 'CPU' in job['logs']
    await manager.shutdown()


@pytest.mark.asyncio
async def test_available_hardware_encode(sample, binaries, tmp_path):
    available = encoder_names(binaries[0])
    hardware = await asyncio.to_thread(detect_hardware, binaries[0], available)
    vendor = next((name for name in ('nvidia', 'intel', 'amd') if hardware.get(name, {}).get('h264')), None)
    if not vendor: pytest.skip('Brak działającego sprzętowego enkodera H.264.')
    store = Store(tmp_path / 'state.sqlite')
    manager = JobManager(store, tmp_path / 'data', *binaries, available, hardware)
    spec = ConversionSpec(sources=[str(sample)], output_dir=str(tmp_path), format='mp4',
                          video_codec='h264', device=vendor, fallback=False, width=256, height=256)
    job = await manager.add(spec)
    await wait_for(manager, [job])
    assert job['status'] == 'completed', job.get('logs')
    assert streams(probe(job['output'], binaries[1]), 'video')[0]['codec_name'] == 'h264'
    await manager.shutdown()
