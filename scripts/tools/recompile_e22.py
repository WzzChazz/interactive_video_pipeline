"""Recompile Episode 22 without the top banner text."""
import json
from database.db_session import get_session
from database.models import Episode
from core.ffmpeg_compiler import compile_video

def recompile_no_banner():
    with get_session() as session:
        ep = session.query(Episode).filter_by(episode_number=22).first()
        if not ep:
            print("Episode 22 not found!")
            return
        
        script_data = json.loads(ep.script_json)
        asset_manifest = json.loads(ep.asset_manifest_json)
        
        clip_manifest = {int(k): v for k, v in asset_manifest.get("clips", {}).items()}
        audio_manifest = {int(k): v for k, v in asset_manifest.get("audio", {}).items()}
        
        output_paths = compile_video(
            scenes=script_data.get("scenes", []),
            clip_manifest=clip_manifest,
            audio_manifest=audio_manifest,
            episode_tag=ep.episode_tag,
            theme_key=ep.theme_key if hasattr(ep, 'theme_key') else "hospital_horror",
            next_branches=script_data.get("next_branches", {}),
            banner_text="",  # <-- 关键：传空字符串，彻底去掉顶部横幅
        )
        
        print(f"Recompile complete! Output: {output_paths}")

if __name__ == "__main__":
    recompile_no_banner()
