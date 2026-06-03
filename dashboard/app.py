"""
dashboard/app.py
================
FastAPI 后台监控服务。
"""

import asyncio
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from database.db_session import init_db, get_session
from database.models import Episode, EpisodeStatus

app = FastAPI(title="Interactive Video Pipeline Dashboard", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

@app.on_event("startup")
def startup():
    init_db()


# ── Models ─────────────────────────────────────────────────

class BranchOverride(BaseModel):
    branch: str

class PipelineRunRequest(BaseModel):
    branch_override: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────

def _ep_to_dict(ep: Episode) -> dict:
    return {
        "id": ep.id,
        "season_id": ep.season_id,
        "episode_number": ep.episode_number,
        "episode_tag": ep.episode_tag,
        "title": ep.title,
        "status": ep.status.value,
        "chosen_branch": ep.chosen_branch,
        "vote_a_count": ep.vote_a_count,
        "vote_b_count": ep.vote_b_count,
        "video_output_path": ep.video_output_path,
        "douyin_video_url": ep.douyin_video_url,
        "error_stage": ep.error_stage,
        "error_message": ep.error_message,
        "retry_count": ep.retry_count,
        "created_at": ep.created_at.isoformat() + "Z" if ep.created_at else "",
        "updated_at": ep.updated_at.isoformat() + "Z" if ep.updated_at else "",
        "published_at": ep.published_at.isoformat() + "Z" if ep.published_at else None,
        "has_script": bool(ep.script_json),
        "has_assets": bool(ep.asset_manifest_json),
        "has_video": bool(ep.video_output_path and Path(ep.video_output_path).exists()),
    }

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

@app.get("/", response_class=HTMLResponse)
def root():
    p = _STATIC_DIR / "index.html"
    return HTMLResponse(p.read_text(encoding="utf-8") if p.exists() else "<h1>Loading…</h1>")


# ── Stats ──────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    with get_session() as session:
        eps = session.query(Episode).all()
    c = {s.value: 0 for s in EpisodeStatus}
    for ep in eps:
        c[ep.status.value] += 1
    return {
        "total": len(eps),
        "voting": c["VOTING"],
        "pending_review": c.get("PENDING_REVIEW", 0),
        "generating": c["GENERATING"] + c.get("GENERATING_ASSETS", 0),
        "completed": c["COMPLETED"], "published": c["PUBLISHED"],
        "failed": c["FAILED"], "last_updated": datetime.now().isoformat(),
    }


# ── Episodes ───────────────────────────────────────────────

@app.get("/api/episodes")
def list_episodes(page: int = 1, per_page: int = 20,
                  status: Optional[str] = None, season_id: Optional[int] = None):
    with get_session() as session:
        q = session.query(Episode)
        if status:
            try:
                q = q.filter(Episode.status == EpisodeStatus(status))
            except ValueError:
                raise HTTPException(400, f"Invalid status: {status}")
        if season_id:
            q = q.filter(Episode.season_id == season_id)
        total = q.count()
        eps = q.order_by(Episode.episode_number.desc()).offset((page - 1) * per_page).limit(per_page).all()
        items = [_ep_to_dict(ep) for ep in eps]
    return {"total": total, "page": page, "per_page": per_page, "items": items}


@app.get("/api/episodes/{eid}")
def get_episode(eid: int):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")
        data = _ep_to_dict(ep)
        data["script_parsed"] = None
        data["asset_manifest"] = None
        if ep.script_json:
            try:
                data["script_parsed"] = json.loads(ep.script_json)
            except Exception:
                data["script_parsed"] = {"_raw": ep.script_json[:500]}
        if ep.asset_manifest_json:
            try:
                data["asset_manifest"] = json.loads(ep.asset_manifest_json)
            except Exception:
                pass
    return data


# ── Actions ────────────────────────────────────────────────

@app.post("/api/episodes/{eid}/retry")
def retry_episode(eid: int):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")
        if ep.status != EpisodeStatus.FAILED:
            raise HTTPException(400, f"Only FAILED episodes can be retried. Current: {ep.status.value}")
        ep.status = EpisodeStatus.VOTING
        ep.error_stage = None
        ep.error_message = None
        ep.retry_count += 1
        session.commit()
    return {"ok": True, "message": f"Episode {eid} reset to VOTING."}


@app.post("/api/episodes/{eid}/branch")
def override_branch(eid: int, body: BranchOverride):
    if body.branch not in ("A", "B"):
        raise HTTPException(400, "branch must be 'A' or 'B'")
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")
        ep.chosen_branch = body.branch
        if ep.status == EpisodeStatus.FAILED:
            ep.status = EpisodeStatus.VOTING
        session.commit()
    return {"ok": True, "message": f"Branch forced to {body.branch}"}


@app.post("/api/episodes/{eid}/approve-review")
def approve_review(eid: int, background_tasks: BackgroundTasks):
    """人工审核通过，进入生成资产阶段"""
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")
        if ep.status != EpisodeStatus.PENDING_REVIEW:
            raise HTTPException(400, f"Must be PENDING_REVIEW. Current: {ep.status.value}")
        ep.status = EpisodeStatus.GENERATING_ASSETS
        session.commit()
    
    # 自动恢复流水线运行
    global _pipeline_running
    with _pipeline_lock:
        if not _pipeline_running:
            _pipeline_running = True
            background_tasks.add_task(_pipeline_task, None)
            
    return {"ok": True, "message": f"Episode {eid} approved! Assets generation started."}


@app.post("/api/episodes/{eid}/approve-publish")
def approve_publish(eid: int, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")
        if ep.status != EpisodeStatus.COMPLETED:
            raise HTTPException(400, f"Must be COMPLETED. Current: {ep.status.value}")
        if not ep.video_output_path or not Path(ep.video_output_path).exists():
            raise HTTPException(400, "Video file not found on disk.")
    background_tasks.add_task(_publish_task, eid)
    return {"ok": True, "message": f"Publishing episode {eid} in background…"}


def _publish_task(eid: int):
    try:
        with get_session() as session:
            ep = session.get(Episode, eid)
            script_data = json.loads(ep.script_json or "{}")
            video_path  = ep.video_output_path
        from main import stage_publish
        with get_session() as session:
            ep = session.get(Episode, eid)
            stage_publish(video_path, ep, script_data)
    except Exception as e:
        with get_session() as session:
            ep = session.get(Episode, eid)
            if ep:
                ep.status = EpisodeStatus.FAILED
                ep.error_stage = "publish"
                ep.error_message = str(e)
                session.commit()


# ── Pipeline control ───────────────────────────────────────

_pipeline_running = False
_pipeline_lock    = threading.Lock()


@app.get("/api/pipeline/status")
def pipeline_status():
    return {"running": _pipeline_running}


@app.post("/api/pipeline/run")
def run_pipeline_now(req: PipelineRunRequest, background_tasks: BackgroundTasks):
    global _pipeline_running
    with _pipeline_lock:
        if _pipeline_running:
            raise HTTPException(409, "Pipeline is already running.")
        _pipeline_running = True
    background_tasks.add_task(_pipeline_task, req.branch_override)
    return {"ok": True, "message": "Pipeline started in background."}


def _pipeline_task(branch_override: Optional[str]):
    global _pipeline_running
    try:
        from main import run_pipeline, _get_or_create_current_episode
        if branch_override:
            ep = _get_or_create_current_episode()
            with get_session() as session:
                e = session.get(Episode, ep.id)
                if e:
                    e.chosen_branch = branch_override
                    session.commit()
        run_pipeline()
    finally:
        _pipeline_running = False


# ── Logs ───────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs(lines: int = 100):
    return {"lines": _read_log_tail(lines)}


# ── WebSocket log stream ───────────────────────────────────

_ws_clients: set[WebSocket] = set()
_log_line_cursor = 0


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    global _log_line_cursor
    await ws.accept()
    _ws_clients.add(ws)
    # 发送历史
    history = _read_log_tail(150)
    await ws.send_json({"type": "history", "lines": history})
    _log_line_cursor = len(_read_log_tail(9999))
    try:
        while True:
            await asyncio.sleep(2)
            all_lines = _read_log_tail(9999)
            if len(all_lines) > _log_line_cursor:
                new = all_lines[_log_line_cursor:]
                _log_line_cursor = len(all_lines)
                dead = set()
                for c in list(_ws_clients):
                    try:
                        await c.send_json({"type": "append", "lines": new})
                    except Exception:
                        dead.add(c)
                _ws_clients -= dead
    except (WebSocketDisconnect, Exception):
        _ws_clients.discard(ws)


# ── Entrypoint ─────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "dashboard.app:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
        app_dir=str(_PROJECT_ROOT),
    )
