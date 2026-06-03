import time
import base64
import requests
import os
import jwt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable, Tuple, Any

from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import (
    STORAGE_TEMP_DIR,
    MAX_WORKERS,
    FLUX_API_KEY,  # Reused as SiliconFlow API key
    HAILUO_API_KEY,
    HAILUO_API_URL,
    ZHIPU_API_KEY,
    VIDEO_PROVIDER,
    API_MAX_RETRIES,
)
from database.db_session import get_session
from database.models import SceneAsset

class VideoGenError(Exception):
    pass

def _image_to_base64_data_uri(image_path: str) -> str:
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(image_path).suffix.lower().lstrip(".")
    mime_type = "jpeg" if ext == "jpg" else ext
    return f"data:image/{mime_type};base64,{encoded}"

def _poll_and_download_atomic(
    task_id: str, 
    fetch_status_fn: Callable[[], Tuple[str, Any]], 
    extract_url_fn: Callable[[Any], Optional[str]], 
    save_path: Path, 
    max_polls: int = 360, 
    poll_interval: int = 10
) -> Path:
    """统一的原子性轮询与下载辅助函数"""
    video_url = None
    for _ in range(max_polls):
        time.sleep(poll_interval)
        try:
            status, data = fetch_status_fn()
        except Exception as e:
            logger.warning(f"API poll error for task {task_id}: {e}, retrying...")
            continue
            
        if status in ("success", "succeed", "completed"):
            video_url = extract_url_fn(data)
            if video_url:
                break
            else:
                raise VideoGenError(f"Task {task_id} succeeded but returned no video url. Data: {data}")
        elif status in ("failed", "error", "fail"):
            raise VideoGenError(f"Task {task_id} failed on server. Data: {data}")
            
        logger.debug(f"Task {task_id} is {status}...")
        
    if not video_url:
        raise VideoGenError(f"Task {task_id} timed out after {max_polls * poll_interval} seconds.")
        
    logger.info(f"Downloading video for task {task_id}...")
    temp_path = save_path.with_suffix('.tmp')
    for attempt in range(5):
        try:
            with requests.get(video_url, stream=True, timeout=60) as vid_resp:
                vid_resp.raise_for_status()
                with open(temp_path, "wb") as f:
                    for chunk in vid_resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            import subprocess
            raw_path = temp_path.with_suffix('.raw.mp4')
            temp_path.rename(raw_path)
            
            logger.info(f"Standardizing video to 30fps/yuv420p for task {task_id}...")
            cmd = [
                "ffmpeg", "-y", "-i", str(raw_path),
                "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p",
                "-an", str(save_path)
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                raise VideoGenError(f"FFmpeg standardization failed: {res.stderr.decode('utf-8', errors='ignore')}")
            
            try:
                raw_path.unlink()
            except:
                pass
            break
        except Exception as e:
            if attempt == 4:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                raise VideoGenError(f"Failed to download video after 5 attempts: {e}")
            logger.warning(f"Download error for {task_id}: {e}, retrying {attempt + 1}/5...")
            time.sleep(5)
            
    logger.success(f"Video downloaded successfully to {save_path}")
    return save_path

@retry(retry=retry_if_exception_type(VideoGenError), stop=stop_after_attempt(API_MAX_RETRIES), wait=wait_exponential(min=10, max=60), reraise=True)
def _siliconflow_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    if not FLUX_API_KEY:
        raise VideoGenError("FLUX_API_KEY (SiliconFlow Token) is not configured in .env")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {FLUX_API_KEY}"
    }

    submit_url = "https://api.siliconflow.cn/v1/video/submit"
    img_data_uri = _image_to_base64_data_uri(image_path)
    
    payload = {
        "model": "Wan-AI/Wan2.2-I2V-A14B",
        "image": img_data_uri,
        "prompt": prompt,
        "seed": 42
    }
    
    logger.info(f"Submitting SiliconFlow I2V task for image: {Path(image_path).name}")
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=60)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"SiliconFlow submit failed after 5 attempts: {e}")
            time.sleep(5)
            
    try:
        resp_data = resp.json()
    except Exception:
        raise VideoGenError(f"SiliconFlow submit failed, invalid JSON response: {resp.text}")
    
    if resp.status_code != 200:
        raise VideoGenError(f"SiliconFlow submit failed: {resp_data}")
        
    task_id = resp_data.get("req_id") or resp_data.get("id") or resp_data.get("requestId")
    if not task_id:
        raise VideoGenError(f"SiliconFlow submit failed, no task ID: {resp_data}")
        
    logger.info(f"SiliconFlow task submitted. Task ID: {task_id}")
    
    def fetch_status():
        query_url = "https://api.siliconflow.cn/v1/video/status"
        payload_status = {"requestId": task_id}
        q_resp = requests.post(query_url, json=payload_status, headers=headers, timeout=15)
        q_resp.raise_for_status()
        q_data = q_resp.json()
        return q_data.get("status", "").lower(), q_data

    def extract_url(data):
        videos = data.get("results", {}).get("videos", [])
        if videos:
            return videos[0].get("url")
        return data.get("url") or data.get("file_url")

    return _poll_and_download_atomic(task_id, fetch_status, extract_url, save_path)

@retry(retry=retry_if_exception_type(VideoGenError), stop=stop_after_attempt(API_MAX_RETRIES), wait=wait_exponential(min=10, max=60), reraise=True)
def _kling_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    import os
    KLING_AK = os.getenv("KLING_AK", "").strip()
    KLING_SK = os.getenv("KLING_SK", "").strip()
    if not KLING_AK or not KLING_SK:
        raise VideoGenError("KLING_AK or KLING_SK is not configured in .env")

    headers_jwt = {"alg": "HS256", "typ": "JWT"}
    
    def get_kling_headers():
        payload_jwt = {
            "iss": KLING_AK,
            "exp": int(time.time()) + 1800,
            "nbf": int(time.time()) - 5
        }
        token = jwt.encode(payload_jwt, KLING_SK.encode("utf-8"), algorithm="HS256", headers=headers_jwt)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    submit_url = "https://api-beijing.klingai.com/v1/videos/image2video"
    with open(image_path, "rb") as f:
        img_raw_b64 = base64.b64encode(f.read()).decode("utf-8")
    
    payload = {
        "model_name": "kling-v1",
        "image": img_raw_b64,
        "prompt": prompt
    }
    
    logger.info(f"Submitting Kling I2V task for image: {Path(image_path).name}")
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(submit_url, json=payload, headers=get_kling_headers(), timeout=60)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Kling submit failed after 5 attempts: {e}")
            time.sleep(5)
            
    try:
        resp_data = resp.json()
    except Exception:
        raise VideoGenError(f"Kling submit failed, invalid JSON: {resp.text}")
    
    if resp.status_code != 200 or resp_data.get("code") != 0:
        raise VideoGenError(f"Kling submit failed: {resp_data}")
        
    task_id = resp_data.get("data", {}).get("task_id")
    if not task_id:
        raise VideoGenError(f"Kling submit failed, no task ID found: {resp_data}")
        
    logger.info(f"Kling task submitted. Task ID: {task_id}")
    
    def fetch_status():
        query_url = f"https://api-beijing.klingai.com/v1/videos/image2video/{task_id}"
        q_resp = requests.get(query_url, headers=get_kling_headers(), timeout=15)
        q_resp.raise_for_status()
        q_data = q_resp.json()
        return q_data.get("data", {}).get("task_status", "").lower(), q_data

    def extract_url(data):
        videos = data.get("data", {}).get("task_result", {}).get("videos", [])
        if videos:
            return videos[0].get("url")
        return None

    return _poll_and_download_atomic(task_id, fetch_status, extract_url, save_path)

@retry(retry=retry_if_exception_type(VideoGenError), stop=stop_after_attempt(API_MAX_RETRIES), wait=wait_exponential(min=10, max=60), reraise=True)
def _hailuo_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    if not HAILUO_API_KEY:
        raise VideoGenError("HAILUO_API_KEY is not configured in .env")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {HAILUO_API_KEY.strip()}"
    }

    submit_url = f"{HAILUO_API_URL}/video_generation"
    img_data_uri = _image_to_base64_data_uri(image_path)
    
    payload = {
        "model": "video-01",
        "prompt": prompt,
        "first_frame_image": img_data_uri
    }
    
    logger.info(f"Submitting Hailuo I2V task for image: {Path(image_path).name}")
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=60)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Hailuo submit failed after 5 attempts: {e}")
            time.sleep(5)
            
    try:
        resp_data = resp.json()
    except Exception:
        raise VideoGenError(f"Hailuo submit failed, invalid JSON response: {resp.text}")
    
    if resp.status_code != 200:
        raise VideoGenError(f"Hailuo submit failed: {resp_data}")
        
    task_id = resp_data.get("task_id")
    if not task_id:
        raise VideoGenError(f"Hailuo submit failed, no task_id: {resp_data}")
        
    logger.info(f"Hailuo task submitted. Task ID: {task_id}")
    
    def fetch_status():
        query_url = f"{HAILUO_API_URL}/query/video_generation?task_id={task_id}"
        q_resp = requests.get(query_url, headers=headers, timeout=15)
        q_resp.raise_for_status()
        q_data = q_resp.json()
        return q_data.get("status", "").lower(), q_data

    def extract_url(data):
        file_id = data.get("file_id")
        if file_id:
            if str(file_id).startswith("http"):
                return file_id
            file_url_endpoint = f"{HAILUO_API_URL}/files/retrieve?file_id={file_id}"
            f_resp = requests.get(file_url_endpoint, headers=headers, timeout=10)
            f_resp.raise_for_status()
            f_data = f_resp.json()
            return f_data.get("file", {}).get("download_url")
        return None

    return _poll_and_download_atomic(task_id, fetch_status, extract_url, save_path)

@retry(retry=retry_if_exception_type(VideoGenError), stop=stop_after_attempt(API_MAX_RETRIES), wait=wait_exponential(min=10, max=60), reraise=True)
def _zhipu_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    if not ZHIPU_API_KEY:
        raise VideoGenError("ZHIPU_API_KEY is not configured in .env")

    from zhipuai import ZhipuAI
    client = ZhipuAI(api_key=ZHIPU_API_KEY)

    logger.info(f"Submitting Zhipu CogVideoX-Flash task for image: {Path(image_path).name}")
    img_data_uri = _image_to_base64_data_uri(image_path)
    
    try:
        response = client.videos.generations(
            model="cogvideox-flash",
            image_url=img_data_uri,
            prompt=prompt
        )
        task_id = response.id
    except Exception as e:
        raise VideoGenError(f"Zhipu submit failed: {e}")

    logger.info(f"Zhipu task submitted. Task ID: {task_id}")

    def fetch_status():
        result = client.videos.retrieve_videos_result(id=task_id)
        return result.task_status.lower(), result

    def extract_url(data):
        if data.video_result and len(data.video_result) > 0:
            return data.video_result[0].url
        return None

    return _poll_and_download_atomic(task_id, fetch_status, extract_url, save_path)

def generate_single_clip(scene_index: int, image_path: str, save_path: Path, camera_note: str = "") -> Path:
    """生成单个视频片段，自带容灾兜底。"""
    try:
        if VIDEO_PROVIDER == "zhipu":
            logger.info("Generating clip scene {} via Zhipu API...", scene_index)
            return _zhipu_generate(image_path, save_path, prompt=camera_note)
        elif VIDEO_PROVIDER == "hailuo":
            logger.info("Generating clip scene {} via Hailuo API...", scene_index)
            return _hailuo_generate(image_path, save_path, prompt=camera_note)
        else:
            logger.info("Generating clip scene {} via Kling API...", scene_index)
            return _kling_generate(image_path, save_path, prompt=camera_note)
    except Exception as e:
        logger.error(f"Generative API failed for scene {scene_index}: {e}. Activating Fallback...")
        from core.stock_footage_fallback import fetch_fallback_video
        
        keyword = "dark eerie background"
        if camera_note:
            words = camera_note.replace(",", " ").split()
            if len(words) >= 2:
                keyword = f"{words[0]} {words[1]} dark"
                
        return fetch_fallback_video(keyword, save_path)

def generate_video_clips(scenes: list[dict], image_manifest: dict[int, str], episode_tag: str, episode_id: Optional[int] = None) -> dict[int, str]:
    """并发将所有分镜静态图转换为视频片段（含断点续跑）。"""
    video_dir = STORAGE_TEMP_DIR / episode_tag / "clips"
    video_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, str] = {}
    errors: list[str] = []

    if episode_id is not None:
        with get_session() as session:
            for scene in scenes:
                idx = scene["scene_index"]
                asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                if not asset:
                    asset = SceneAsset(episode_id=episode_id, scene_index=idx, video_status="PENDING")
                    session.add(asset)
            session.commit()

    def _worker(scene: dict) -> tuple[int, str]:
        idx = scene["scene_index"]
        camera_note = scene.get("camera_note", "")
        
        if idx in [1, 2]:
            camera_note += ", extremely fast zoom in, terrifying kubrick style symmetrical push"
        
        img_path = image_manifest.get(idx, "")
        if not img_path or not Path(img_path).exists():
            raise VideoGenError(f"Scene {idx}: image not found at '{img_path}'")
        
        save_path = video_dir / f"scene_{idx:02d}.mp4"

        if episode_id is not None:
            with get_session() as session:
                asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                if asset and asset.video_status == "COMPLETED" and save_path.exists():
                    logger.info(f"Scene {idx} video already generated, skipping.")
                    return idx, str(save_path)

        try:
            generate_single_clip(
                scene_index=idx,
                image_path=img_path,
                save_path=save_path,
                camera_note=camera_note
            )
            if episode_id is not None:
                with get_session() as session:
                    asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                    if asset:
                        asset.video_status = "COMPLETED"
                        asset.video_path = str(save_path)
                        session.commit()
            return idx, str(save_path)
        except Exception as e:
            if episode_id is not None:
                with get_session() as session:
                    asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                    if asset:
                        asset.video_status = "FAILED"
                        session.commit()
            raise

    workers = min(MAX_WORKERS, len(scenes))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, s): s["scene_index"] for s in scenes}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                i, path = fut.result()
                results[i] = path
                logger.info(f"Clip [{len(results)}/{len(scenes)}] done: scene {i}")
            except VideoGenError as e:
                logger.error(f"Clip scene {idx} failed: {e}")
                errors.append(f"scene_{idx}: {e}")

    if errors:
        raise VideoGenError(f"{len(errors)} clip(s) failed:\n" + "\n".join(errors))
    logger.success(f"All {len(results)} video clips done for {episode_tag}.")
    return results
