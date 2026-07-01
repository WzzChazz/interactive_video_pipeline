import os
from pathlib import Path
from loguru import logger
from database.db_session import get_session
from database.models import Episode, SceneAsset
from core.lip_sync_engine import LipSyncEngine
import os
os.environ["USE_MUSETALK"] = "true"

engine = LipSyncEngine()

for i in range(1, 7):
    idx_str = f"{i:02d}"
    video_path = f"storage/temp/S01E024/clips/scene_{idx_str}.mp4"
    audio_path = f"storage/temp/S01E024/audio/scene_{idx_str}_voice.mp3"
    out_path = f"storage/temp/S01E024/clips/scene_{idx_str}_lipsync.mp4"
    
    if os.path.exists(video_path) and os.path.exists(audio_path):
        logger.info(f"Processing Scene {i}...")
        try:
            engine.generate_talking_head(video_path, audio_path, out_path)
            logger.success(f"Scene {i} lipsync completed: {out_path}")
        except Exception as e:
            logger.error(f"Scene {i} lipsync failed: {e}")
            
