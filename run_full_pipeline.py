import sys
import os
import json
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

sys.path.append('/Users/mac/project/interactive_video_pipeline')
load_dotenv()

# Override config to use Kling
import config.settings as settings
settings.VIDEO_PROVIDER = "kling"

from core.video_gen import generate_video_clips
from core.pipeline_engine import VideoPipelineEngine
from database.db_session import get_session
from database.models import Episode

def run():
    episode_tag = "S01E024"
    logger.info(f"Starting FULL PIPELINE for {episode_tag} using KLING")

    with get_session() as session:
        ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
        script = json.loads(ep.script_json)
        episode_id = ep.id

    # 1. Map existing 6 images
    image_manifest = {}
    for i in range(1, 7):
        img_path = f"/Users/mac/project/interactive_video_pipeline/storage/temp/{episode_tag}/images/scene_{i:02d}.png"
        if os.path.exists(img_path):
            image_manifest[i] = img_path
        else:
            logger.error(f"Image not found: {img_path}")
            return

    # 2. Generate Videos
    logger.info("--- Step 1: Generating Videos via Kling ---")
    try:
        clip_manifest = generate_video_clips(
            scenes=script.get("scenes", []),
            image_manifest=image_manifest,
            episode_tag=episode_tag,
            episode_id=episode_id
        )
    except Exception as e:
        logger.error(f"Video generation failed: {e}")
        return

    # 3. Execute TTS, LipSync, and FFmpeg Compilation
    logger.info("--- Step 2: Running Pipeline Engine (TTS, SFX, FFmpeg) ---")
    engine = VideoPipelineEngine(script, episode_tag, clip_manifest=clip_manifest)
    final_videos = engine.execute_pipeline()

    logger.success(f"Pipeline completed successfully. Final videos: {final_videos}")

if __name__ == "__main__":
    run()
