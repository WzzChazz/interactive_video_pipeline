import re

with open("dashboard/app.py", "r") as f:
    content = f.read()

# 1. Restore _read_log_tail that was accidentally removed
read_log_code = """
def _read_log_tail(n: int = 200) -> list[str]:
    log_dir = _PROJECT_ROOT / "storage" / "logs"
    if not log_dir.exists():
        return []
    files = sorted(log_dir.glob("pipeline_*.log"), reverse=True)
    if not files:
        return []
    try:
        lines = files[0].read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception as e:
        return [f"[Error reading log: {e}]"]

# ── Frontend ───────────────────────────────────────────────
"""
content = content.replace("# Legacy _read_log_tail removed because we now use WebSockets.\n\n\n# ── Frontend ───────────────────────────────────────────────", read_log_code)

# 2. Add import for Celery task at the top
celery_import = """from database.models import Episode, EpisodeStatus, SceneAsset
from core.celery_app import run_pipeline_task"""
content = content.replace("from database.models import Episode, EpisodeStatus, SceneAsset", celery_import)

# 3. Replace all background_tasks.add_task(_pipeline_task, None) with run_pipeline_task.delay()
# There are several blocks like this:
'''
    global _pipeline_running
    with _pipeline_lock:
        if not _pipeline_running:
            _pipeline_running = True
            background_tasks.add_task(_pipeline_task, None)
'''
# I will use a regex to replace this entire block with a call to celery. 
# But wait, we need to know the theme_key. The endpoints usually have `eid`. 
# We can get theme_key from the episode object we just queried.
# For reject_scene, reject_all_images, approve_videos, reject_video: we have `ep` object!

def replace_pipeline_lock(match):
    return "    run_pipeline_task.delay(ep.theme_key)\n"

pattern = re.compile(r'\s*global _pipeline_running\s+with _pipeline_lock:\s+if not _pipeline_running:\s+_pipeline_running = True\s+background_tasks\.add_task\(_pipeline_task, None\)', re.MULTILINE)
content = pattern.sub(replace_pipeline_lock, content)

# 4. Refactor rerun_episode
pattern_rerun = re.compile(r'\s*global _pipeline_running\s+with _pipeline_lock:\s+if not _pipeline_running:\s+_pipeline_running = True\s+background_tasks\.add_task\(_pipeline_task, None\)')
content = pattern_rerun.sub(replace_pipeline_lock, content)

# Wait, rerun_episode actually didn't have the _pipeline_running block in the snippet earlier? Ah, it might. 
# Let's just fix run_pipeline_now
run_pipeline_now_code = """
@app.post("/api/pipeline/run")
def run_pipeline_now(req: PipelineRunRequest):
    theme = "hospital_horror" # default
    if req.branch_override:
        from main import _get_or_create_current_episode
        ep = _get_or_create_current_episode()
        theme = ep.theme_key
        with get_session() as session:
            e = session.get(Episode, ep.id)
            if e:
                e.chosen_branch = req.branch_override
                session.commit()
    
    run_pipeline_task.delay(theme)
    return {"ok": True, "message": "Pipeline task sent to Celery queue."}
"""

content = re.sub(r'@app\.post\("/api/pipeline/run"\).*?def _pipeline_task.*?_pipeline_running = False', run_pipeline_now_code, content, flags=re.DOTALL)

# Remove the globals
content = re.sub(r'_pipeline_running = False\n_pipeline_lock\s*=\s*threading\.Lock\(\)\n', '', content)

# pipeline_status: we don't need _pipeline_running
content = content.replace("return {\"running\": _pipeline_running or db_running}", "return {\"running\": db_running}")

with open("dashboard/app.py", "w") as f:
    f.write(content)
