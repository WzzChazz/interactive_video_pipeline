import json
import os
from pathlib import Path
from database.db_session import get_session
from database.models import Episode
from core.ffmpeg_compiler import compile_video

with get_session() as session:
    ep = session.query(Episode).filter(Episode.episode_number == 19).first()
    script_data = json.loads(ep.script_json)

base_dir = Path("storage/temp/S01E019")
clip_manifest = {}
audio_manifest = {}

for i, scene in enumerate(script_data.get("scenes", [])):
    scene_id = i + 1
    clip_path = (base_dir / "clips" / f"scene_{scene_id:02d}.mp4").resolve()
    voice_path = (base_dir / "audio" / f"scene_{scene_id:02d}_voice.mp3").resolve()
    sfx_path = (base_dir / "audio" / f"scene_{scene_id:02d}_sfx.mp3").resolve()
    
    if clip_path.exists():
        clip_manifest[scene_id] = str(clip_path)
    
    audio_map = {}
    if voice_path.exists():
        audio_map["voice"] = str(voice_path)
    if sfx_path.exists():
        audio_map["sfx"] = str(sfx_path)
        
    if audio_map:
        audio_manifest[scene_id] = audio_map

print("Clips:", clip_manifest)
print("Audio:", audio_manifest)

episode_title = script_data.get("episode_title", "悬疑短剧")
banner_text = f"第 {ep.episode_number} 集 | {episode_title}"

compile_video(
    scenes=script_data.get("scenes", []),
    clip_manifest=clip_manifest,
    audio_manifest=audio_manifest,
    episode_tag=ep.episode_tag,
    theme_key=ep.theme_key,
    next_branches=script_data.get("next_branches", {}),
    banner_text=banner_text,
)
print("Done compiling!")
