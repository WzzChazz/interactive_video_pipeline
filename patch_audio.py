import re

with open("core/audio_gen.py", "r") as f:
    content = f.read()

# Add redis import
if "import redis" not in content:
    content = content.replace("import time", "import time\nimport redis\nimport json")

# Add progress publisher function
pub_func = """
def _publish_progress(episode_tag: str, step_name: str, pct: int):
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.publish("pipeline_progress", json.dumps({
            "active": True, "step_name": step_name, "step": 4, "total": 6, "pct": pct, "episode": episode_tag
        }))
    except:
        pass
"""
if "_publish_progress(" not in content:
    content = content.replace("def _worker", pub_func + "\n\ndef _worker")


# In generate_audio, at the end of loop:
# logger.info("Audio [{}/{}] scene {} done", done, total, s_idx)
# We can add: _publish_progress(episode_tag, f"生成配音 ({done}/6)", int(20 + done / 6 * 15))
audio_log = 'logger.info("Audio [{}/{}] scene {} done", done, total, s_idx)'
audio_pub = audio_log + '\n            _publish_progress(episode.episode_tag, f"生成配音 ({done}/{total})", int(20 + done / total * 15))'
content = content.replace(audio_log, audio_pub)

with open("core/audio_gen.py", "w") as f:
    f.write(content)
