"""
dashboard/app.py
================
FastAPI 后台监控服务。

接口列表：
  GET  /api/stats              总览统计
  GET  /api/episodes           集列表（分页）
  GET  /api/episodes/{id}      集详情（含解析后的 script_json）
  POST /api/episodes/{id}/retry   重试失败集
  POST /api/episodes/{id}/branch  手动强制分支（覆盖投票结果）
  POST /api/pipeline/run       立即触发完整流水线
  GET  /api/logs               最近日志行（REST）
  WS   /ws/logs                实时日志流（WebSocket）
  GET  /                       前端页面
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from database.db_session import init_db, get_session
from database.models import Episode, EpisodeStatus

# ──────────────────────────────────────────────────────────
app = FastAPI(title="Interactive Video Pipeline Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# 启动时建表
@app.on_event("startup")
def startup():
    init_db()


# ──────────────────────────────────────────────────────────
# Pydantic 响应模型
# ──────────────────────────────────────────────────────────

class EpisodeSummary(BaseModel):
    id: int
    season_id: int
    episode_number: int
    episode_tag: str
    title: Optional[str]
    status: str
    chosen_branch: Optional[str]
    vote_a_count: int
    vote_b_count: int
    video_output_path: Optional[str]
    douyin_video_url: Optional[str]
    error_stage: Optional[str]
    error_message: Optional[str]
    retry_count: int
    created_at: str
    updated_at: str
    published_at: Optional[str]
    has_script: bool
    has_assets: bool
    has_video: bool

class EpisodeDetail(EpisodeSummary):
    script_parsed: Optional[dict]
    asset_manifest: Optional[dict]

class StatsResponse(BaseModel):
    total: int
    voting: int
    generating: int
    completed: int
    published: int
    failed: int
    last_updated: str

class BranchOverride(BaseModel):
    branch: str   # "A" or "B"

class PipelineRunRequest(BaseModel):
    branch_override: Optional[str] = None  # 可选强制分支


# ──────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────

def _ep_to_summary(ep: Episode) -> dict:
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
        "created_at": ep.created_at.isoformat() if ep.created_at else "",
        "updated_at": ep.updated_at.isoformat() if ep.updated_at else "",
        "published_at": ep.published_at.isoformat() if ep.published_at else None,
        "has_script": bool(ep.script_json),
        "has_assets": bool(ep.asset_manifest_json),
        "has_video": bool(ep.video_output_path),
    }


# ──────────────────────────────────────────────────────────
# 前端入口
# ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    index_path = _STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard loading...</h1>")


# ──────────────────────────────────────────────────────────
# API: 统计
# ──────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    with get_session() as session:
        all_eps = session.query(Episode).all()
    counts = {s.value: 0 for s in EpisodeStatus}
    for ep in all_eps:
        counts[ep.status.value] += 1
    return {
        "total": len(all_eps),
        "voting":     counts.get("VOTING", 0),
        "generating": counts.get("GENERATING", 0),
        "completed":  counts.get("COMPLETED", 0),
        "published":  counts.get("PUBLISHED", 0),
        "failed":     counts.get("FAILED", 0),
        "last_updated": datetime.now().isoformat(),
    }


# ──────────────────────────────────────────────────────────
# API: 集列表
# ──────────────────────────────────────────────────────────

@app.get("/api/episodes")
def list_episodes(
    page: int = 1,
    per_page: int = 20,
    status: Optional[str] = None,
    season_id: Optional[int] = None,
):
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
        eps = (
            q.order_by(Episode.episode_number.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        items = [_ep_to_summary(ep) for ep in eps]
    return {"total": total, "page": page, "per_page": per_page, "items": items}


# ──────────────────────────────────────────────────────────
# API: 集详情
# ──────────────────────────────────────────────────────────

@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: int):
    with get_session() as session:
        ep = session.get(Episode, episode_id)
        if not ep:
            raise HTTPException(404, "Episode not found")
        data = _ep_to_summary(ep)
        # 解析 JSON 字段
        data["script_parsed"] = None
        data["asset_manifest"] = None
        if ep.script_json:
            try:
                data["script_parsed"] = json.loads(ep.script_json)
            except Exception:
                data["script_parsed"] = {"error": "JSON parse failed", "raw": ep.script_json[:500]}
        if ep.asset_manifest_json:
            try:
                data["asset_manifest"] = json.loads(ep.asset_manifest_json)
            except Exception:
                pass
    return data


# ──────────────────────────────────────────────────────────
# API: 重试失败集
# ──────────────────────────────────────────────────────────

@app.post("/api/episodes/{episode_id}/retry")
def retry_episode(episode_id: int):
    with get_session() as session:
        ep = session.get(Episode, episode_id)
        if not ep:
            raise HTTPException(404, "Episode not found")
        if ep.status not in (EpisodeStatus.FAILED,):
            raise HTTPException(400, f"Only FAILED episodes can be retried. Current: {ep.status.value}")
        ep.status        = EpisodeStatus.VOTING
        ep.error_stage   = None
        ep.error_message = None
        ep.retry_count  += 1
    return {"message": f"Episode {episode_id} reset to VOTING for retry.", "retry_count": ep.retry_count}


# ──────────────────────────────────────────────────────────
# API: 手动强制分支
# ──────────────────────────────────────────────────────────

@app.post("/api/episodes/{episode_id}/branch")
def override_branch(episode_id: int, body: BranchOverride):
    if body.branch not in ("A", "B"):
        raise HTTPException(400, "branch must be 'A' or 'B'")
    with get_session() as session:
        ep = session.get(Episode, episode_id)
        if not ep:
            raise HTTPException(404, "Episode not found")
        ep.chosen_branch = body.branch
        # 若处于 FAILED，顺便重置为 VOTING 方便重跑
        if ep.status == EpisodeStatus.FAILED:
            ep.status = EpisodeStatus.VOTING
    return {"message": f"Branch overridden to {body.branch} for episode {episode_id}."}


# ──────────────────────────────────────────────────────────
# API: 手动批准发布（COMPLETED → 触发发布）
# ──────────────────────────────────────────────────────────

@app.post("/api/episodes/{episode_id}/approve-publish")
def approve_publish(episode_id: int, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, episode_id)
        if not ep:
            raise HTTPException(404, "Episode not found")
        if ep.status != EpisodeStatus.COMPLETED:
            raise HTTPException(400, f"Episode must be COMPLETED to publish. Current: {ep.status.value}")
        video_path = ep.video_output_path
        if not video_path or not Path(video_path).exists():
            raise HTTPException(400, f"Video file not found: {video_path}")

    background_tasks.add_task(_run_publish_task, episode_id)
    return {"message": f"Publishing episode {episode_id} in background..."}


def _run_publish_task(episode_id: int):
    """后台任务：仅执行发布步骤。"""
    try:
        with get_session() as session:
            ep = session.get(Episode, episode_id)
            script_data = json.loads(ep.script_json or "{}")
            video_path  = ep.video_output_path

        from main import stage_publish
        with get_session() as session:
            ep = session.get(Episode, episode_id)
            stage_publish(video_path, ep, script_data)
    except Exception as e:
        with get_session() as session:
            ep = session.get(Episode, episode_id)
            if ep:
                ep.status = EpisodeStatus.FAILED
                ep.error_stage = "publish"
                ep.error_message = str(e)


# ──────────────────────────────────────────────────────────
# API: 手动触发完整流水线
# ──────────────────────────────────────────────────────────

_pipeline_running = False
_pipeline_lock    = threading.Lock()


@app.post("/api/pipeline/run")
def run_pipeline_now(req: PipelineRunRequest, background_tasks: BackgroundTasks):
    global _pipeline_running
    with _pipeline_lock:
        if _pipeline_running:
            raise HTTPException(409, "Pipeline is already running.")
        _pipeline_running = True
    background_tasks.add_task(_run_pipeline_task, req.branch_override)
    return {"message": "Pipeline started in background."}


@app.get("/api/pipeline/status")
def pipeline_status():
    return {"running": _pipeline_running}


def _run_pipeline_task(branch_override: Optional[str]):
    global _pipeline_running
    try:
        from main import run_pipeline, _get_or_create_current_episode
        # 若有强制分支，先写入 DB
        if branch_override:
            ep = _get_or_create_current_episode()
            with get_session() as session:
                e = session.get(Episode, ep.id)
                if e:
                    e.chosen_branch = branch_override
        run_pipeline()
    finally:
        _pipeline_running = False


# ──────────────────────────────────────────────────────────
# API: 日志（REST）
# ──────────────────────────────────────────────────────────

def _read_log_tail(n_lines: int = 200) -> list[str]:
    """读取最新日志文件的最后 n 行。"""
    log_dir = _PROJECT_ROOT / "storage" / "logs"
    if not log_dir.exists():
        return ["[No log files found]"]
    log_files = sorted(log_dir.glob("pipeline_*.log"), reverse=True)
    if not log_files:
        return ["[No log files found]"]
    latest = log_files[0]
    try:
        lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n_lines:]
    except Exception as e:
        return [f"[Error reading log: {e}]"]


@app.get("/api/logs")
def get_logs(lines: int = 100):
    return {"lines": _read_log_tail(lines)}


# ──────────────────────────────────────────────────────────
# WebSocket: 实时日志流
# ──────────────────────────────────────────────────────────

class LogBroadcaster:
    """管理所有 WebSocket 连接，定时广播新日志行。"""
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._last_line_count = 0

    def add(self, ws: WebSocket):
        self._clients.add(ws)

    def remove(self, ws: WebSocket):
        self._clients.discard(ws)

    async def broadcast_new_lines(self):
        """检查日志文件是否有新行，有则广播给所有客户端。"""
        all_lines = _read_log_tail(500)
        if len(all_lines) > self._last_line_count:
            new_lines = all_lines[self._last_line_count:]
            self._last_line_count = len(all_lines)
            dead = set()
            for ws in list(self._clients):
                try:
                    await ws.send_json({"lines": new_lines})
                except Exception:
                    dead.add(ws)
            self._clients -= dead


_broadcaster = LogBroadcaster()


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    _broadcaster.add(websocket)
    # 立即发送最近 100 行历史
    await websocket.send_json({"lines": _read_log_tail(100), "type": "history"})
    try:
        while True:
            await asyncio.sleep(2)
            await _broadcaster.broadcast_new_lines()
    except (WebSocketDisconnect, Exception):
        _broadcaster.remove(websocket)


# ──────────────────────────────────────────────────────────
# 启动入口
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "dashboard.app:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
        app_dir=str(_PROJECT_ROOT),
    )
