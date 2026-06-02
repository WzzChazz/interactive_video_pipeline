import json
import subprocess
from pathlib import Path
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from core.ffmpeg_renderer import FFmpegRenderer

def re_render():
    with get_session() as session:
        ep = session.query(Episode).order_by(Episode.id.desc()).first()
        script = json.loads(ep.script_json)
        
        renderer = FFmpegRenderer(work_dir=f"./storage/temp/{ep.episode_tag}/render")
        final_video_paths = []
        
        for s in script.get('scenes', []):
            idx = s.get('scene_index')
            text = s.get('dialogue', '')
            
            bypassed_video = f"./storage/temp/{ep.episode_tag}/video/lipsync_scene_{idx:02d}.mp4"
            mixed_audio_path = f"./storage/temp/{ep.episode_tag}/audio/mixed_scene_{idx:02d}.aac"
            final_scene_path = f"./storage/temp/{ep.episode_tag}/render/final_scene_{idx:02d}.mp4"
            
            renderer.render_scene(
                video_path=bypassed_video,
                mixed_audio_path=mixed_audio_path,
                dialogue_text=text,
                output_path=final_scene_path
            )
            final_video_paths.append(final_scene_path)
            
        logger.success("Re-rendered all scenes.")
        list_txt = Path(f"./storage/temp/{ep.episode_tag}/new_pipeline_concat.txt")
        with open(list_txt, "w") as f:
            for p in final_video_paths:
                f.write(f"file '{Path(p).absolute()}'\n")
                
        final_out = Path(f"/Users/mac/Desktop/{ep.episode_tag}_LIPSYNC_FINAL.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_txt), "-c", "copy", str(final_out)], check=True)
        logger.success(f"Done! Saved to {final_out}")

if __name__ == "__main__":
    re_render()
