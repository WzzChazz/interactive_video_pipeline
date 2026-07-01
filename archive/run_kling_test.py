import sys
import logging
from pathlib import Path

sys.path.append('/Users/mac/project/interactive_video_pipeline')
from dotenv import load_dotenv
load_dotenv()
from core.video_gen import _kling_generate, VideoGenError

logging.basicConfig(level=logging.INFO)

image_path = "/Users/mac/project/interactive_video_pipeline/storage/temp/S01E024/images/scene_01.png"
save_path = Path("/Users/mac/project/interactive_video_pipeline/storage/temp/S01E024/clips/scene_01.mp4")

try:
    print("Testing Kling API with 1 video to check point balance...")
    _kling_generate(image_path, save_path, prompt="Cinematic horror, pitch black, extreme tension")
    print("Success!")
except VideoGenError as e:
    print(f"Kling API Failed: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
