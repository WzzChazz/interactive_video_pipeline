import json
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from core.ffmpeg_compiler import compile_video

def backfill_render():
    logger.info("Starting legacy backfill rendering for episodes 14-16...")
    with get_session() as session:
        episodes = session.query(Episode).filter(Episode.episode_number.in_([14, 15, 16])).order_by(Episode.episode_number).all()
        for ep in episodes:
            if not ep.script_json or not ep.asset_manifest_json:
                logger.warning(f"Skipping Episode {ep.episode_number}: Missing script or asset manifest.")
                continue
                
            script_data = json.loads(ep.script_json)
            asset_manifest = json.loads(ep.asset_manifest_json)
            
            # 校验是否翻译完成
            scenes = script_data.get("scenes", [])
            if not scenes or not scenes[0].get("english_dialogue"):
                logger.warning(f"Episode {ep.episode_number} has NO english translations. Run backfill_translator.py first.")
                continue
                
            clip_manifest = {int(k): v for k, v in asset_manifest.get("clips", {}).items()}
            audio_manifest = {int(k): v for k, v in asset_manifest.get("audio", {}).items()}
            
            logger.info(f"Rendering Global version for Episode {ep.episode_number} (Tag: {ep.episode_tag})...")
            
            try:
                # 核心逻辑：只渲染 global_only，节省时间和 CPU，并自动带上倒计时
                output_paths = compile_video(
                    scenes=scenes,
                    clip_manifest=clip_manifest,
                    audio_manifest=audio_manifest,
                    episode_tag=ep.episode_tag,
                    render_mode="global_only"
                )
                
                if "global" in output_paths:
                    ep.video_global_path = output_paths["global"]
                    session.commit()
                    logger.success(f"Episode {ep.episode_number} global video generated: {output_paths['global']}")
            except Exception as e:
                logger.error(f"Failed to render Episode {ep.episode_number}: {e}")
                session.rollback()

if __name__ == "__main__":
    backfill_render()
