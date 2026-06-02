"""
core/stock_footage_fallback.py
==============================
Fallback module to fetch stock footage (e.g., from Pexels) when the primary 
AI video generation API (Zhipu/Kling) fails or times out.
This ensures the pipeline never crashes and always outputs a video.
"""

import os
import requests
import random
from pathlib import Path
from loguru import logger
from config.settings import STORAGE_TEMP_DIR, VIDEO_WIDTH, VIDEO_HEIGHT

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

def fetch_fallback_video(keyword: str, save_path: Path, min_duration: float = 5.0) -> Path:
    """
    Attempts to fetch a free stock video from Pexels based on the keyword.
    If it fails or no API key is provided, generates a default scary fallback video via FFmpeg.
    """
    logger.info(f"Triggering Stock Footage Fallback for keyword: '{keyword}'")
    
    if PEXELS_API_KEY:
        try:
            url = f"https://api.pexels.com/videos/search?query={keyword}&per_page=15&orientation=portrait"
            headers = {"Authorization": PEXELS_API_KEY}
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                videos = data.get("videos", [])
                if videos:
                    # Pick a random video to avoid repetition
                    vid = random.choice(videos)
                    # Find the best quality video file (HD)
                    best_file = max(vid.get("video_files", []), key=lambda x: x.get("width", 0))
                    link = best_file.get("link")
                    if link:
                        logger.info(f"Downloading Pexels fallback video from {link}")
                        vid_resp = requests.get(link, stream=True, timeout=30)
                        vid_resp.raise_for_status()
                        with open(save_path, "wb") as f:
                            for chunk in vid_resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                        return save_path
        except Exception as e:
            logger.warning(f"Pexels fallback failed: {e}. Falling back to generative synthetic video.")

    # Synthetic Fallback: Generate a creepy static or noise pattern using FFmpeg
    import subprocess
    logger.info("Generating synthetic fallback video via FFmpeg.")
    try:
        # Generate a creepy red-tinted noise pattern (simulating a broken TV / paranormal static)
        # We use simple grain/noise.
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"nullsrc=s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:d={min_duration}:r=30",
            "-vf", "geq=r='random(1)*255':g='random(1)*50':b='random(1)*50',hue=s=0.2",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            str(save_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return save_path
    except Exception as e:
        logger.error(f"Failed to generate synthetic fallback video: {e}")
        raise e
