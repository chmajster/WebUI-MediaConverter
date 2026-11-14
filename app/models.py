from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, model_validator


VIDEO_FORMATS = {
    'mp4': ('h264', 'hevc', 'av1', 'mpeg4'),
    'mkv': ('h264', 'hevc', 'av1', 'vp8', 'vp9', 'mpeg4', 'mpeg2video'),
    'avi': ('h264', 'mpeg4', 'mpeg2video'),
    'mov': ('h264', 'hevc', 'mpeg4'),
    'webm': ('vp8', 'vp9', 'av1'),
    'flv': ('h264',),
    'wmv': ('wmv2',),
    'mpeg': ('mpeg2video',),
    'ts': ('h264', 'hevc', 'mpeg2video'),
    'm4v': ('h264', 'hevc', 'mpeg4'),
}
VIDEO_AUDIO = {
    'mp4': ('aac', 'mp3', 'alac'), 'mkv': ('aac', 'mp3', 'opus', 'vorbis', 'flac', 'ac3'),
    'avi': ('mp3', 'ac3', 'pcm_s16le'), 'mov': ('aac', 'alac', 'pcm_s16le'),
    'webm': ('opus', 'vorbis'), 'flv': ('aac', 'mp3'), 'wmv': ('wmav2',),
    'mpeg': ('mp2', 'ac3'), 'ts': ('aac', 'mp2', 'ac3'), 'm4v': ('aac', 'mp3', 'alac'),
}
AUDIO_FORMATS = {
    'mp3': ('libmp3lame', 'mp3'), 'wav': ('pcm_s16le', 'wav'),
    'flac': ('flac', 'flac'), 'aac': ('aac', 'aac'), 'm4a': ('aac', 'm4a'),
    'ogg': ('libvorbis', 'ogg'), 'opus': ('libopus', 'opus'),
    'wma': ('wmav2', 'wma'), 'aiff': ('pcm_s16be', 'aiff'), 'alac': ('alac', 'm4a'),
}
VIDEO_ENCODERS = {'h264': 'libx264', 'hevc': 'libx265', 'av1': 'libaom-av1',
                  'vp8': 'libvpx', 'vp9': 'libvpx-vp9', 'mpeg4': 'mpeg4',
                  'mpeg2video': 'mpeg2video', 'wmv2': 'wmv2'}
AUDIO_ENCODERS = {'aac': 'aac', 'mp3': 'libmp3lame', 'opus': 'libopus',
                  'vorbis': 'libvorbis', 'flac': 'flac', 'ac3': 'ac3',
                  'alac': 'alac', 'pcm_s16le': 'pcm_s16le', 'wmav2': 'wmav2', 'mp2': 'mp2'}
LOSSLESS_AUDIO = {'flac', 'wav', 'aiff', 'alac'}


class ConversionSpec(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=200)
    operation: Literal['video', 'audio', 'extract', 'gif', 'merge_video', 'merge_audio', 'frames', 'thumbnail'] = 'video'
    format: str = 'mp4'
    output_dir: str
    video_codec: str = 'auto'
    audio_codec: str = 'auto'
    device: Literal['auto', 'cpu', 'nvidia', 'intel', 'amd'] = 'auto'
    cuda_decode: bool = False
    fallback: bool = True
    crf: int = Field(default=23, ge=0, le=51)
    video_bitrate: int | None = Field(default=None, ge=50, le=200000)
    audio_bitrate: int = Field(default=192, ge=16, le=1536)
    preset: Literal['ultrafast', 'fast', 'medium', 'slow', 'veryslow'] = 'medium'
    pixel_format: Literal['auto', 'yuv420p', 'yuv422p', 'yuv444p', 'yuv420p10le'] = 'auto'
    width: int | None = Field(default=None, ge=16, le=7680)
    height: int | None = Field(default=None, ge=16, le=4320)
    fps: float | None = Field(default=None, ge=0.1, le=240)
    aspect: Literal['auto', '16:9', '4:3', '1:1', '9:16'] = 'auto'
    sample_rate: Literal[0, 8000, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 96000] = 0
    channels: Literal[0, 1, 2] = 0
    normalize: bool = False
    volume_db: float = Field(default=0, ge=-60, le=20)
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)
    speed: float = Field(default=1, ge=0.25, le=4)
    rotate: Literal[0, 90, 180, 270] = 0
    audio_stream: int | None = Field(default=None, ge=0)
    subtitle_stream: int | None = Field(default=None, ge=0)
    external_audio: str | None = None
    external_subtitle: str | None = None
    metadata_title: str = Field(default='', max_length=250)
    metadata_artist: str = Field(default='', max_length=250)
    metadata_album: str = Field(default='', max_length=250)
    threads: int = Field(default=0, ge=0, le=64)
    keep_partial: bool = False

    @model_validator(mode='after')
    def validate_options(self):
        self.format = self.format.lower()
        if self.end is not None and self.end <= self.start:
            raise ValueError('Koniec fragmentu musi być później niż początek.')
        if self.width is not None and self.width % 2 or self.height is not None and self.height % 2:
            raise ValueError('Wymiary obrazu muszą być parzyste.')
        if (self.width is None) != (self.height is None):
            raise ValueError('Podaj zarówno szerokość, jak i wysokość.')
        if self.operation in ('video', 'merge_video'):
            if self.format not in VIDEO_FORMATS:
                raise ValueError('Nieobsługiwany format wideo.')
            if self.video_codec not in ('auto', 'copy') and self.video_codec not in VIDEO_FORMATS[self.format]:
                raise ValueError('Wybrany kodek wideo nie jest obsługiwany przez ten kontener.')
            if self.audio_codec not in ('auto', 'copy', 'none') and self.audio_codec not in VIDEO_AUDIO[self.format]:
                raise ValueError('Wybrany kodek audio nie jest obsługiwany przez ten kontener.')
        elif self.operation in ('audio', 'extract', 'merge_audio') and self.format not in AUDIO_FORMATS:
            raise ValueError('Nieobsługiwany format audio.')
        if self.operation in ('merge_video', 'merge_audio') and len(self.sources) < 2:
            raise ValueError('Łączenie wymaga co najmniej dwóch plików.')
        if self.operation not in ('merge_video', 'merge_audio') and len(self.sources) != 1:
            raise ValueError('Jedno zadanie musi dotyczyć jednego pliku.')
        video_filters = self.width or self.fps or self.aspect != 'auto' or self.rotate or self.speed != 1
        audio_filters = self.normalize or self.volume_db != 0 or self.speed != 1 or self.sample_rate or self.channels
        if self.video_codec == 'copy' and video_filters:
            raise ValueError('Kopiowanie wideo nie pozwala stosować filtrów obrazu ani zmiany prędkości.')
        if self.video_codec == 'copy' and (self.pixel_format != 'auto' or self.video_bitrate):
            raise ValueError('Kopiowanie wideo nie pozwala zmieniać formatu pikseli ani bitrate.')
        if self.audio_codec == 'copy' and audio_filters:
            raise ValueError('Kopiowanie audio nie pozwala stosować filtrów ani zmiany prędkości.')
        if self.cuda_decode and (self.operation not in ('video', 'merge_video') or self.device not in ('auto', 'nvidia')):
            raise ValueError('Dekodowanie CUDA wymaga kodowania NVIDIA lub trybu automatycznego.')
        if self.audio_codec == 'copy' and self.operation in ('audio', 'extract', 'merge_audio') and self.format in LOSSLESS_AUDIO:
            # Container/codec compatibility is checked against the actual source by the engine.
            pass
        if self.operation in ('merge_video', 'merge_audio') and (self.start or self.end or self.external_audio or self.external_subtitle):
            raise ValueError('Łączenie nie obsługuje przycinania ani dodatkowych plików w jednym zadaniu.')
        return self

    @property
    def output_extension(self) -> str:
        if self.operation == 'gif': return 'gif'
        if self.operation == 'frames': return 'zip'
        if self.operation == 'thumbnail': return 'jpg'
        if self.operation in ('audio', 'extract', 'merge_audio'): return AUDIO_FORMATS[self.format][1]
        return self.format


class AppSettings(BaseModel):
    output_dir: str = ''
    ffmpeg: str = ''
    ffprobe: str = ''
    concurrency: int = Field(default=2, ge=1, le=8)
    theme: Literal['dark', 'light'] = 'dark'
    keep_partial: bool = False
