from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope='session')
def binaries():
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('Test wymaga FFmpeg i FFprobe w PATH.')
    return ffmpeg, ffprobe


@pytest.fixture(scope='session')
def sample(tmp_path_factory, binaries):
    root = tmp_path_factory.mktemp('materiały z polskimi znakami')
    movie = root / 'próbka wideo.mp4'
    command = [binaries[0], '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=15',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
               '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(movie)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    return movie
