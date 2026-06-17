import json
import os
from pathlib import Path
from loguru import logger
from database.db_session import get_session
from database.models import Episode, SceneAsset
from core.video_gen import generate_video_clips
import config.settings
import core.video_gen

config.settings.VIDEO_PROVIDER = "aliyun"
core.video_gen.VIDEO_PROVIDER = "aliyun"

image_manifest = {
    1: "storage/temp/S01E024/images/scene_01.jpg",
    2: "storage/temp/S01E024/images/scene_02.jpg",
    3: "storage/temp/S01E024/images/scene_03.jpg",
    4: "storage/temp/S01E024/images/scene_04.jpg",
    5: "storage/temp/S01E024/images/scene_05.jpg",
    6: "storage/temp/S01E024/images/scene_06.jpg" 
}

with get_session() as session:
    ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
    if ep:
        script_data = json.loads(ep.script_json) if isinstance(ep.script_json, str) else ep.script_json
        scenes = script_data.get("scenes", [])
        
        # Reset video_status so it generates them again
        assets = session.query(SceneAsset).filter_by(episode_id=ep.id).all()
        for a in assets:
            a.video_status = "PENDING"
            a.video_path = None
            clip_path = f"storage/temp/S01E024/clips/scene_{a.scene_index:02d}.mp4"
            if os.path.exists(clip_path):
                os.remove(clip_path)
        session.commit()
        
        logger.info("Starting Aliyun Wanx generation...")
        try:
            results = generate_video_clips(scenes, image_manifest, "S01E024", ep.id)
            logger.success(f"Generation complete: {results}")
        except Exception as e:
            logger.error(f"Failed: {e}")
    else:
        logger.error("Episode not found")
