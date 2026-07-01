import re

with open("core/ffmpeg_compiler.py", "r") as f:
    content = f.read()

# Add redis import
if "import redis" not in content:
    content = content.replace("import subprocess", "import subprocess\nimport redis\nimport json")

# Add progress publisher function
pub_func = """
def _publish_progress(episode_tag: str, step_name: str, pct: int):
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.publish("pipeline_progress", json.dumps({
            "active": True, "step_name": step_name, "step": 5, "total": 6, "pct": pct, "episode": episode_tag
        }))
    except:
        pass
"""
if "_publish_progress(" not in content:
    content = content.replace("def _run_ffmpeg", pub_func + "\n\ndef _run_ffmpeg")


# In compile_video, inside _process_scene_lipsync
# We have:
# logger.info("Applying LipSync + CodeFormer to Scene {}...", s_idx)
# We can add: _publish_progress(episode_tag, f"唇形增强 (片段 {s_idx}/6)", 45 + s_idx * 4)

lipsync_log = 'logger.info("Applying LipSync + CodeFormer to Scene {}...", s_idx)'
lipsync_pub = lipsync_log + '\n                    _publish_progress(episode_tag, f"唇形增强 (片段 {s_idx}/6)", 45 + s_idx * 4)'
content = content.replace(lipsync_log, lipsync_pub)

# In ffmpeg concat steps
# logger.info("[Stage 5/6] Muxing video and audio tracks...")
mux_log = 'logger.info("[Stage 5/6] Muxing video and audio tracks...")'
mux_pub = mux_log + '\n    _publish_progress(episode_tag, "混合音轨中", 80)'
content = content.replace(mux_log, mux_pub)

mux_main_log = 'logger.info("[Stage 5/6] Burning subtitles and final audio mix (mux_main)...")'
mux_main_pub = mux_main_log + '\n    _publish_progress(episode_tag, "烧录字幕合片", 85)'
content = content.replace(mux_main_log, mux_main_pub)

concat_final_log = 'logger.info("[Stage 5/6] Concatenating main video with endcard...")'
concat_final_pub = concat_final_log + '\n    _publish_progress(episode_tag, "拼接片尾卡", 95)'
content = content.replace(concat_final_log, concat_final_pub)


with open("core/ffmpeg_compiler.py", "w") as f:
    f.write(content)
