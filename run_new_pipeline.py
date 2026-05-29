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
    
    # 构建新版 schema
    schema = []
    for s in script.get('scenes', []):
        schema.append({
            'scene_id': s.get('scene_index'),
            'role': s.get('character', s.get('speaker', '')),
            'text': s.get('dialogue', ''),
            'emotion': s.get('emotion', 'neutral'),
            'sfx': map_sfx(s.get('sfx_prompt', '')),
            'action_timestamp': float(s.get("action_timestamp", 0.5)),
            # 原始生成的视频
            'video_source': f"./storage/temp/{ep.episode_tag}/raw_video.mp4" # 测试简化：使用之前生成好的拼接视频的对应片段，或者只用 raw_video
        })

    # 由于这是测试，我们直接注入 schema
    engine = VideoPipelineEngine({}, ep.episode_tag)
    # 给测试环境替换上一次真实的短切片视频
    for s in schema:
        # 使用旧版已经切好或者未切好的原始文件，为了快速测试，这里用一个测试素材或者降级
        # 由于我们没有按 scene 拆分的短视频，这里直接用 raw_video 的前两秒代替测试
        s['video_source'] = f"/Users/mac/project/interactive_video_pipeline/storage/outputs/S01E028/S01E028_douyin.mp4" 

    engine.pipeline_schema = schema
    
    try:
        paths = engine.execute_pipeline()
        logger.success(f"Final segments generated: {paths}")
        
        # 拼接最终视频
        list_txt = Path("./storage/temp/new_pipeline_concat.txt")
        with open(list_txt, "w") as f:
            for p in paths:
                f.write(f"file '{Path(p).absolute()}'\n")
                
        final_out = Path("/Users/mac/Desktop/S01E028_NEW_PIPELINE.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_txt), "-c", "copy", str(final_out)], check=True)
        logger.success(f"Done! Saved to {final_out}")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
