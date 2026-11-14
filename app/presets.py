"""Built-in profiles map to the same validated parameters as the editor."""

VIDEO_PRESETS = {
    'Oryginalna jakość (kopiowanie)': {'video_codec': 'copy', 'audio_codec': 'copy'},
    'Maksymalna jakość': {'video_codec': 'auto', 'audio_codec': 'auto', 'crf': 16, 'preset': 'slow'},
    'Zrównoważony': {'video_codec': 'auto', 'audio_codec': 'auto', 'crf': 23, 'preset': 'medium'},
    'Najmniejszy rozmiar': {'video_codec': 'auto', 'audio_codec': 'auto', 'crf': 30, 'preset': 'slow'},
    '4K': {'video_codec': 'auto', 'audio_codec': 'auto', 'width': 3840, 'height': 2160},
    '1440p': {'video_codec': 'auto', 'audio_codec': 'auto', 'width': 2560, 'height': 1440},
    '1080p': {'video_codec': 'auto', 'audio_codec': 'auto', 'width': 1920, 'height': 1080},
    '720p': {'video_codec': 'auto', 'audio_codec': 'auto', 'width': 1280, 'height': 720},
    '480p': {'video_codec': 'auto', 'audio_codec': 'auto', 'width': 854, 'height': 480},
    'Starsze urządzenia': {'format': 'mp4', 'video_codec': 'h264', 'audio_codec': 'aac', 'pixel_format': 'yuv420p'},
    'Web / streaming': {'format': 'mp4', 'video_codec': 'h264', 'audio_codec': 'aac', 'crf': 23, 'pixel_format': 'yuv420p'},
}
AUDIO_PRESETS = {
    'MP3 128 kb/s': {'format': 'mp3', 'audio_codec': 'auto', 'audio_bitrate': 128},
    'MP3 192 kb/s': {'format': 'mp3', 'audio_codec': 'auto', 'audio_bitrate': 192},
    'MP3 320 kb/s': {'format': 'mp3', 'audio_codec': 'auto', 'audio_bitrate': 320},
    'FLAC bezstratny': {'format': 'flac', 'audio_codec': 'auto'},
    'WAV PCM': {'format': 'wav', 'audio_codec': 'auto'},
    'OPUS 96 kb/s': {'format': 'opus', 'audio_codec': 'auto', 'audio_bitrate': 96},
    'AAC 256 kb/s': {'format': 'm4a', 'audio_codec': 'auto', 'audio_bitrate': 256},
}
