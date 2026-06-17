import json
import subprocess
import shutil
import os
from pathlib import Path
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from core.ffmpeg_renderer import FFmpegRenderer
from core.lip_sync_engine import LipSyncEngine

def re_run():
    with get_session() as session:
        ep = session.query(Episode).order_by(Episode.id.desc()).first()
        script = json.loads(ep.script_json)
        
        renderer = FFmpegRenderer(work_dir=f"./storage/temp/{ep.episode_tag}/render")
        lipsync = LipSyncEngine()
        final_video_paths = []
        
        for s in script.get('scenes', []):
            idx = s.get('scene_index')
            text = s.get('dialogue', '')
            
            temp_w2l_out = f"./storage/temp/{ep.episode_tag}/video/lipsync_scene_{idx:02d}.w2l.mp4"
            final_out = f"./storage/temp/{ep.episode_tag}/video/lipsync_scene_{idx:02d}.mp4"
            mixed_audio_path = f"./storage/temp/{ep.episode_tag}/audio/mixed_scene_{idx:02d}.aac"
            final_scene_path = f"./storage/temp/{ep.episode_tag}/render/final_scene_{idx:02d}.mp4"
            
            if not Path(temp_w2l_out).exists():
                logger.error(f"Missing base w2l video for scene {idx}: {temp_w2l_out}")
                continue
            
            # 1. Run CodeFormer on existing w2l file
            logger.info(f"Re-running CodeFormer for Scene {idx} with w=0.3...")
            restored_dir = str(Path(final_out).parent / "restored")
            lipsync.run_codeformer(temp_w2l_out, restored_dir)
            
            # Fetch restored output
            restored_videos = list(Path(restored_dir).rglob("*.mp4"))
            if restored_videos:
                best_video = sorted(restored_videos, key=lambda x: x.stat().st_mtime, reverse=True)[0]
                shutil.move(str(best_video), final_out)
                logger.success(f"[LipSync] 成功输出 CodeFormer (w=0.3) 修复后的高清视频: {final_out}")
                shutil.rmtree(restored_dir, ignore_errors=True)
            else:
                logger.error("[LipSync] CodeFormer 运行完毕但未找到 mp4 文件")
                shutil.copy(temp_w2l_out, final_out)
            
            # 2. FFmpeg Render
            renderer.render_scene(
                video_path=final_out,
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
                
        final_mp4 = Path(f"/Users/mac/Desktop/{ep.episode_tag}_LIPSYNC_FINAL.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_txt), "-c", "copy", str(final_mp4)], check=True)
        logger.success(f"Done! Saved to {final_mp4}")

if __name__ == "__main__":
    re_run()
