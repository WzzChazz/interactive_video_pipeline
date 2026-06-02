import sys
import os
from pathlib import Path
from core.ffmpeg_renderer import FFmpegRenderer

renderer = FFmpegRenderer("storage/temp/S01E028/render")
final_clips = []
for i in range(1, 7):
    idx = f"0{i}"
    video = f"storage/temp/S01E028/video/lipsync_scene_{idx}.mp4"
    audio = f"storage/temp/S01E028/audio/mixed_scene_{idx}.aac"
    out = f"storage/temp/S01E028/render/final_scene_{idx}.mp4"
    # We need the dialogue text for subtitles!
    # Where to get dialogue text? It was passed in.
    # If we don't have it, we might lose subtitles.
    print(f"Cannot easily re-render without subtitles data!")
