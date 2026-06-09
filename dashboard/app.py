"""
dashboard/app.py
================
FastAPI 后台监控服务。
"""

import asyncio
import json
import os
import re
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
from database.models import Episode, EpisodeStatus, SceneAsset
from core.celery_app import run_pipeline_task

app = FastAPI(title="Interactive Video Pipeline Dashboard", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

_MEDIA_DIR = _PROJECT_ROOT / "storage" / "temp"
if _MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=str(_MEDIA_DIR)), name="media")

_OUTPUTS_DIR = _PROJECT_ROOT / "storage" / "outputs"
if _OUTPUTS_DIR.exists():
    app.mount("/outputs", StaticFiles(directory=str(_OUTPUTS_DIR)), name="outputs")

_ENV_PATH = _PROJECT_ROOT / ".env"

@app.on_event("startup")
def startup():
    init_db()


# ── Models ─────────────────────────────────────────────────

class BranchOverride(BaseModel):
    branch: str

class PipelineRunRequest(BaseModel):
    branch_override: Optional[str] = None

class RejectRequest(BaseModel):
    scene_index: int
    feedback: str

class GlobalRejectRequest(BaseModel):
    feedback: str

class PublishRequest(BaseModel):
    platforms: list[str] = ["douyin", "kuaishou"]

class SettingsUpdateRequest(BaseModel):
    video_provider: Optional[str] = None
    use_lipsync: Optional[bool] = None

class RerunRequest(BaseModel):
    stage: str  # "images" | "videos" | "audio" | "script"


# ── Cost estimation ────────────────────────────────────────

_COST_PER_CLIP = {
    "aliyun":  0.70,   # Wan 2.7 720P ≈ ¥0.14/s * 5s
    "zhipu":   0.10,   # Flash 极速版
    "kling":   1.50,   # 可灵 5s 标准
    "hailuo":  2.00,   # 海螺
    "local":   0.00,   # 本地 FFmpeg
}

def _estimate_cost(ep: Episode) -> float:
    try:
        env_text = _ENV_PATH.read_text(encoding="utf-8")
        m = re.search(r"VIDEO_PROVIDER\s*=\s*(\S+)", env_text)
        provider = m.group(1) if m else "local"
    except Exception:
        provider = "local"
    clips = 6  # 默认 6 幕
    if ep.script_json:
        try:
            sc = json.loads(ep.script_json)
            clips = len(sc.get("scenes", [])) or clips
        except Exception:
            pass
    return round(_COST_PER_CLIP.get(provider, 0) * clips, 2)


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
        "estimated_cost": _estimate_cost(ep),
    }

import redis
from websockets.exceptions import ConnectionClosedOK

redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)


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
        "pending_review": c.get("PENDING_REVIEW", 0) + c.get("PENDING_IMAGE_REVIEW", 0) + c.get("PENDING_VIDEO_REVIEW", 0) + c.get("PENDING_PUBLISH", 0),
        "generating": c["GENERATING_SCRIPT"] + c.get("GENERATING_IMAGES", 0) + c.get("GENERATING_VIDEOS", 0) + c.get("GENERATING_AUDIO_AND_COMPILE", 0),
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


# ── Settings API ───────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    """读取 .env 中的关键设置"""
    settings = {"video_provider": "kling", "use_lipsync": False}
    try:
        env_text = _ENV_PATH.read_text(encoding="utf-8")
        m = re.search(r"VIDEO_PROVIDER\s*=\s*(\S+)", env_text)
        if m:
            settings["video_provider"] = m.group(1)
        m2 = re.search(r"USE_LIPSYNC\s*=\s*(\S+)", env_text)
        if m2:
            settings["use_lipsync"] = m2.group(1).lower() == "true"
    except Exception as e:
        pass
    return settings


@app.post("/api/settings")
def update_settings(req: SettingsUpdateRequest):
    """热更新 .env 中的关键设置，无需重启"""
    try:
        env_text = _ENV_PATH.read_text(encoding="utf-8")
        if req.video_provider is not None:
            valid = {"kling", "hailuo", "zhipu", "aliyun", "local"}
            if req.video_provider not in valid:
                raise HTTPException(400, f"Invalid provider. Must be one of: {valid}")
            if re.search(r"VIDEO_PROVIDER\s*=", env_text):
                env_text = re.sub(r"VIDEO_PROVIDER\s*=\s*\S+", f"VIDEO_PROVIDER={req.video_provider}", env_text)
            else:
                env_text += f"\nVIDEO_PROVIDER={req.video_provider}\n"
        if req.use_lipsync is not None:
            val = "true" if req.use_lipsync else "false"
            if re.search(r"USE_LIPSYNC\s*=", env_text):
                env_text = re.sub(r"USE_LIPSYNC\s*=\s*\S+", f"USE_LIPSYNC={val}", env_text)
            else:
                env_text += f"\nUSE_LIPSYNC={val}\n"
        _ENV_PATH.write_text(env_text, encoding="utf-8")
        return {"ok": True, "message": "Settings updated. Will apply on next pipeline run."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to update .env: {e}")


# ── Pipeline Progress ──────────────────────────────────────

@app.get("/api/pipeline/progress")
def pipeline_progress():
    """智能推断当前流水线进度：优先读 DB 状态+实际文件，再回退到日志关键字"""

    # 1. 先从数据库找出当前正在生成中的集数
    active_ep = None
    active_status = None
    generating_statuses = [
        EpisodeStatus.GENERATING_SCRIPT,
        EpisodeStatus.GENERATING_IMAGES,
        EpisodeStatus.GENERATING_VIDEOS,
        EpisodeStatus.GENERATING_AUDIO_AND_COMPILE,
    ]
    with get_session() as session:
        for st in generating_statuses:
            ep = session.query(Episode).filter(Episode.status == st).order_by(Episode.updated_at.desc()).first()
            if ep:
                active_ep = ep.episode_tag
                active_status = st
                break

    is_active = active_ep is not None

    # 2. 根据状态 + 实际文件推断步骤
    if active_status == EpisodeStatus.GENERATING_SCRIPT:
        return {"active": True, "step_name": "生成剧本中", "step": 1, "total": 6, "pct": 10, "episode": active_ep}

    if active_status == EpisodeStatus.GENERATING_IMAGES:
        # 数图片目录里已生成的图片数
        img_dir = _PROJECT_ROOT / "storage" / "temp" / (active_ep or "") / "images"
        done = len(list(img_dir.glob("scene_*.png"))) if img_dir.exists() else 0
        total_scenes = 6
        pct = min(95, max(5, int(done / total_scenes * 100)))
        return {"active": True, "step_name": f"生成图片 ({done}/{total_scenes})", "step": 2, "total": 6, "pct": pct, "episode": active_ep}

    if active_status == EpisodeStatus.GENERATING_VIDEOS:
        clip_dir = _PROJECT_ROOT / "storage" / "temp" / (active_ep or "") / "clips"
        done = len(list(clip_dir.glob("scene_*.mp4"))) if clip_dir.exists() else 0
        total_scenes = 6
        pct = min(95, max(5, int(done / total_scenes * 100)))
        return {"active": True, "step_name": f"生成视频 ({done}/{total_scenes})", "step": 3, "total": 6, "pct": pct, "episode": active_ep}

    if active_status == EpisodeStatus.GENERATING_AUDIO_AND_COMPILE:
        ep_tag = active_ep or ""
        temp_dir = _PROJECT_ROOT / "storage" / "temp" / ep_tag
        audio_dir = temp_dir / "audio"
        clip_dir  = temp_dir / "clips"
        out_dir   = _PROJECT_ROOT / "storage" / "outputs" / ep_tag

        # 检查实际文件判断已完成哪些步骤
        audio_done = len(list(audio_dir.glob("*_voice.mp3"))) if audio_dir.exists() else 0
        clips_done = len(list(clip_dir.glob("scene_*.mp4"))) if clip_dir.exists() else 0

        if clips_done >= 6 and audio_done >= 6:
            # 已有所有片段和音频，等待 WebSocket 接管后续的详细进度推送
            return {"active": True, "step_name": "合成音轨与特效中...", "step": 5, "total": 6, "pct": 45, "episode": ep_tag}
        elif audio_done >= 6:
            return {"active": True, "step_name": f"配音完成，等待合片", "step": 5, "total": 6, "pct": 40, "episode": ep_tag}
        elif audio_done > 0:
            return {"active": True, "step_name": f"生成配音 ({audio_done}/6)", "step": 4, "total": 6, "pct": int(20 + audio_done / 6 * 15), "episode": ep_tag}
        else:
            return {"active": True, "step_name": "准备配音中", "step": 4, "total": 6, "pct": 20, "episode": ep_tag}

    # 3. 没有 generating 状态的集数 → idle
    return {"active": False, "step_name": "空闲", "step": 0, "total": 6, "pct": 0, "episode": None}


@app.websocket("/api/ws/progress")
async def websocket_progress(websocket: WebSocket):
    await websocket.accept()
    pubsub = redis_client.pubsub()
    pubsub.subscribe("pipeline_progress")
    try:
        while True:
            # We must use an asyncio-friendly way to wait for messages or a non-blocking get_message
            message = pubsub.get_message(ignore_subscribe_messages=True)
            if message:
                try:
                    data = json.loads(message['data'])
                    await websocket.send_json(data)
                except Exception:
                    pass
            else:
                await asyncio.sleep(0.5)
    except (WebSocketDisconnect, ConnectionClosedOK):
        pubsub.unsubscribe()
        pubsub.close()
    except Exception as e:
        print(f"WebSocket error: {e}")
        pubsub.unsubscribe()
        pubsub.close()


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


@app.post("/api/episodes/{eid}/rerun")
def rerun_episode(eid: int, req: RerunRequest, background_tasks: BackgroundTasks):
    """无论当前状态，强制从指定阶段重跑"""
    stage_map = {
        "script":  EpisodeStatus.GENERATING_SCRIPT,
        "images":  EpisodeStatus.GENERATING_IMAGES,
        "videos":  EpisodeStatus.GENERATING_VIDEOS,
        "audio":   EpisodeStatus.GENERATING_AUDIO_AND_COMPILE,
    }
    if req.stage not in stage_map:
        raise HTTPException(400, f"Invalid stage. Must be one of: {list(stage_map)}")
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")

        # Reset scene assets cascading downwards
        assets = session.query(SceneAsset).filter_by(episode_id=eid).all()
        
        if req.stage in ("script", "images", "videos", "audio"):
            # if we rerun audio, reset audio_status
            if req.stage in ("script", "images", "videos", "audio"):
                for a in assets:
                    a.audio_status = "PENDING"
                # Delete old audio
                audio_dir = _PROJECT_ROOT / "storage" / "temp" / ep.episode_tag / "audio"
                if audio_dir.exists():
                    for f in audio_dir.glob("*.mp3"):
                        try: f.unlink()
                        except: pass

            # if we rerun videos, reset video_status
            if req.stage in ("script", "images", "videos"):
                for a in assets:
                    a.video_status = "PENDING"
                # Delete old clips
                clip_dir = _PROJECT_ROOT / "storage" / "temp" / ep.episode_tag / "clips"
                if clip_dir.exists():
                    for f in clip_dir.glob("*.mp4"):
                        try: f.unlink()
                        except: pass

            # if we rerun images, reset image_status
            if req.stage in ("script", "images"):
                for a in assets:
                    a.image_status = "PENDING"

        ep.status = stage_map[req.stage]
        ep.error_stage = None
        ep.error_message = None
        session.commit()
    run_pipeline_task.delay(ep.theme_key)

    return {"ok": True, "message": f"Episode {eid} re-running from stage: {req.stage}"}


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
        ep.status = EpisodeStatus.GENERATING_IMAGES
        session.commit()
    
    # 自动恢复流水线运行
    run_pipeline_task.delay(ep.theme_key)

            
    return {"ok": True, "message": f"Episode {eid} approved! Assets generation started."}


@app.post("/api/episodes/{eid}/approve-images")
def approve_images(eid: int, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep or ep.status != EpisodeStatus.PENDING_IMAGE_REVIEW:
            raise HTTPException(400, "Invalid state")
        ep.status = EpisodeStatus.GENERATING_VIDEOS
        session.commit()
    run_pipeline_task.delay(ep.theme_key)

    return {"ok": True, "message": "Images approved. Starting video generation."}


@app.post("/api/episodes/{eid}/reject-image")
def reject_image(eid: int, req: RejectRequest, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep or ep.status != EpisodeStatus.PENDING_IMAGE_REVIEW:
            raise HTTPException(400, "Invalid state")
        
        # Mark SceneAsset as PENDING
        asset = session.query(SceneAsset).filter_by(episode_id=eid, scene_index=req.scene_index).first()
        if asset:
            asset.image_status = "PENDING"
            
        # Append feedback to prompt
        script = json.loads(ep.script_json or "{}")
        for s in script.get("scenes", []):
            if s["scene_index"] == req.scene_index:
                s["visual_prompt"] += f"\n[USER CORRECTION: {req.feedback}]"
                break
        ep.script_json = json.dumps(script, ensure_ascii=False)
        ep.status = EpisodeStatus.GENERATING_IMAGES
        session.commit()
    run_pipeline_task.delay(ep.theme_key)

    return {"ok": True, "message": f"Scene {req.scene_index} rejected. Regenerating..."}


@app.post("/api/episodes/{eid}/reject-all-images")
def reject_all_images(eid: int, req: GlobalRejectRequest, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")
            
        # Mark all SceneAssets as PENDING for images and delete old files
        assets = session.query(SceneAsset).filter_by(episode_id=eid).all()
        for asset in assets:
            asset.image_status = "PENDING"
            if asset.image_path and os.path.exists(asset.image_path):
                try:
                    os.remove(asset.image_path)
                except Exception:
                    pass
            
        # Append global feedback to prompt for all scenes
        script = json.loads(ep.script_json or "{}")
        for s in script.get("scenes", []):
            s["visual_prompt"] = re.sub(r"\n\[GLOBAL USER CORRECTION: .*?\]", "", s["visual_prompt"])
            s["visual_prompt"] += f"\n[GLOBAL USER CORRECTION: {req.feedback}]"
        ep.script_json = json.dumps(script, ensure_ascii=False)
        ep.status = EpisodeStatus.GENERATING_IMAGES
        session.commit()
    run_pipeline_task.delay(ep.theme_key)

    return {"ok": True, "message": "All images rejected. Global prompt updated. Regenerating..."}


@app.post("/api/episodes/{eid}/approve-videos")
def approve_videos(eid: int, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep or ep.status != EpisodeStatus.PENDING_VIDEO_REVIEW:
            raise HTTPException(400, "Invalid state")
        ep.status = EpisodeStatus.GENERATING_AUDIO_AND_COMPILE
        session.commit()
    run_pipeline_task.delay(ep.theme_key)

    return {"ok": True, "message": "Videos approved. Starting audio and compilation."}


@app.post("/api/episodes/{eid}/reject-video")
def reject_video(eid: int, req: RejectRequest, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep or ep.status != EpisodeStatus.PENDING_VIDEO_REVIEW:
            raise HTTPException(400, "Invalid state")
        
        asset = session.query(SceneAsset).filter_by(episode_id=eid, scene_index=req.scene_index).first()
        if asset:
            asset.video_status = "PENDING"
            
        script = json.loads(ep.script_json or "{}")
        for s in script.get("scenes", []):
            if s["scene_index"] == req.scene_index:
                camera_note = s.get("camera_note", "")
                s["camera_note"] = camera_note + f" [USER CORRECTION: {req.feedback}]"
                break
        ep.script_json = json.dumps(script, ensure_ascii=False)
        ep.status = EpisodeStatus.GENERATING_VIDEOS
        session.commit()
    run_pipeline_task.delay(ep.theme_key)

    return {"ok": True, "message": f"Video {req.scene_index} rejected. Regenerating..."}


@app.post("/api/episodes/{eid}/approve-publish")
def approve_publish(eid: int, req: PublishRequest, background_tasks: BackgroundTasks):
    with get_session() as session:
        ep = session.get(Episode, eid)
        if not ep:
            raise HTTPException(404, "Episode not found")
        if ep.status not in (EpisodeStatus.COMPLETED, EpisodeStatus.PENDING_PUBLISH):
            raise HTTPException(400, f"Invalid state: {ep.status.value}")
        if not ep.video_output_path or not Path(ep.video_output_path).exists():
            raise HTTPException(400, "Video file not found on disk.")
    background_tasks.add_task(_publish_task, eid, req.platforms)
    return {"ok": True, "message": f"Publishing episode {eid} to {req.platforms} in background…"}


def _publish_task(eid: int, platforms: list[str]):
    try:
        with get_session() as session:
            ep = session.get(Episode, eid)
            script_data = json.loads(ep.script_json or "{}")
            output_paths = {
                "douyin": ep.video_output_path,
                "kuaishou": ep.video_output_path,
                "global": ep.video_global_path
            }
            
        from main import stage_publish
        with get_session() as session:
            ep = session.get(Episode, eid)
            stage_publish(output_paths, ep, script_data, platforms=platforms)
    except Exception as e:
        with get_session() as session:
            ep = session.get(Episode, eid)
            if ep:
                ep.status = EpisodeStatus.FAILED
                ep.error_stage = "publish"
                ep.error_message = str(e)
                session.commit()


# ── Pipeline control ───────────────────────────────────────



@app.get("/api/pipeline/status")
def pipeline_status():
    # 同时检查内存标志 AND 数据库里是否有 GENERATING 状态的集数
    generating_statuses = [
        EpisodeStatus.GENERATING_SCRIPT,
        EpisodeStatus.GENERATING_IMAGES,
        EpisodeStatus.GENERATING_VIDEOS,
        EpisodeStatus.GENERATING_AUDIO_AND_COMPILE,
    ]
    db_running = False
    with get_session() as session:
        for st in generating_statuses:
            if session.query(Episode).filter(Episode.status == st).first():
                db_running = True
                break
    return {"running": db_running}



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



# ── Logs ───────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs(lines: int = 100):
    return {"lines": _read_log_tail(lines)}


# ── WebSocket log stream ───────────────────────────────────

_ws_clients: set[WebSocket] = set()
_log_line_cursor = 0


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    global _log_line_cursor, _ws_clients
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


