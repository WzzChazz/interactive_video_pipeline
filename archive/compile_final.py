import json
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from core.ffmpeg_compiler import compile_video

with get_session() as session:
    ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
    if ep:
        script = json.loads(ep.script_json) if isinstance(ep.script_json, str) else ep.script_json
        scenes = script.get("scenes", [])
        
        video_manifest = {}
        audio_manifest = {}
        for i in range(1, 7):
            idx = i
            video_manifest[idx] = f"storage/temp/S01E024/clips/scene_{idx:02d}_lipsync.mp4"
            audio_manifest[idx] = {
                "voice": f"storage/temp/S01E024/audio/scene_{idx:02d}_voice.mp3",
                "sfx": f"storage/temp/S01E024/audio/scene_{idx:02d}_sfx.mp3"
            }
        
        logger.info("Starting final compilation...")
        try:
            final_path = compile_video(scenes, video_manifest, audio_manifest, "S01E024", ep.id)
            logger.success(f"Compilation complete: {final_path}")
        except Exception as e:
            logger.error(f"Compilation failed: {e}")
