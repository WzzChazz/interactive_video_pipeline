import json
import subprocess
from pathlib import Path
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from core.pipeline_engine import VideoPipelineEngine

def map_sfx(prompt):
    if not prompt: return []
    return [p.strip().replace(' ', '_').lower() for p in prompt.split(',')][:3]

with get_session() as session:
    ep = session.query(Episode).order_by(Episode.id.desc()).first()
    script = json.loads(ep.script_json)
    assets = json.loads(ep.asset_manifest_json or "{}")
    clip_manifest = assets.get("clips", {})
    
    schema = []
    for s in script.get('scenes', []):
        idx = s.get('scene_index')
        # 取回当时生成好的真实的短切片视频
        real_video_path = clip_manifest.get(str(idx))
        if not real_video_path or not Path(real_video_path).exists():
            logger.error(f"Cannot find video for scene {idx}! Aborting to save APIs.")
            exit(1)
            
        schema.append({
            'scene_id': idx,
            'role': s.get('character', s.get('speaker', '')),
            'text': s.get('dialogue', ''),
            'emotion': s.get('emotion', 'neutral'),
            'sfx': map_sfx(s.get('sfx_prompt', '')),
            'action_timestamp': float(s.get("action_timestamp", 0.5)),
            'video_source': real_video_path
        })

    engine = VideoPipelineEngine({}, ep.episode_tag)
    engine.pipeline_schema = schema
    
    try:
        paths = engine.execute_pipeline()
        logger.success(f"Final segments generated: {paths}")
        
        list_txt = Path(f"./storage/temp/{ep.episode_tag}/new_pipeline_concat.txt")
        list_txt.parent.mkdir(parents=True, exist_ok=True)
        with open(list_txt, "w") as f:
            for p in paths:
                f.write(f"file '{Path(p).absolute()}'\n")
                
        final_out = Path(f"/Users/mac/Desktop/{ep.episode_tag}_LIPSYNC_FINAL.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_txt), "-c", "copy", str(final_out)], check=True)
        logger.success(f"Done! Saved to {final_out}")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
