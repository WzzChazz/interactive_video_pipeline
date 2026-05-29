import time
import base64
import requests
import os
import jwt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

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

def _siliconflow_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    if not FLUX_API_KEY:
        raise VideoGenError("FLUX_API_KEY (SiliconFlow Token) is not configured in .env")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {FLUX_API_KEY}"
    }

    # 1. Submit Task
    submit_url = "https://api.siliconflow.cn/v1/video/submit"
    img_data_uri = _image_to_base64_data_uri(image_path)
    
    payload = {
        "model": "Wan-AI/Wan2.2-I2V-A14B",
        "image": img_data_uri,
        "prompt": prompt,
        "seed": 42
    }
    
    logger.info("Submitting SiliconFlow I2V task for image: {}", Path(image_path).name)
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=60)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"SiliconFlow submit failed after 5 attempts: {e}")
            logger.warning(f"SiliconFlow API post error: {e}, retrying {attempt+1}/5...")
            time.sleep(5)
            
    try:
        resp_data = resp.json()
    except Exception:
        raise VideoGenError(f"SiliconFlow submit failed, invalid JSON response: {resp.text}")
    
    if resp.status_code != 200:
        raise VideoGenError(f"SiliconFlow submit failed: {resp_data}")
        
    task_id = resp_data.get("req_id") or resp_data.get("id") or resp_data.get("requestId")
    if not task_id:
        raise VideoGenError(f"SiliconFlow submit failed, no task ID found in response: {resp_data}")
        
    logger.info("SiliconFlow task submitted. Task ID: {}", task_id)
    
    # 2. Poll Status
    query_url = f"https://api.siliconflow.cn/v1/video/status"
    video_url = None
    
    payload_status = {
        "requestId": task_id
    }
    
    for _ in range(360): # Poll up to 60 minutes (360 * 10s)
        time.sleep(10)
        
        try:
            q_resp = requests.post(query_url, json=payload_status, headers=headers, timeout=15)
            q_resp.raise_for_status()
            q_data = q_resp.json()
        except Exception as e:
            logger.warning("SiliconFlow API poll error for task {}: {}, retrying...", task_id, str(e))
            continue
        
        status = q_data.get("status", "").lower()
        if status == "success" or status == "succeed" or status == "completed":
            try:
                # SiliconFlow returns video url in a specific format
                results = q_data.get("results", {})
                videos = results.get("videos", [])
                if videos and len(videos) > 0:
                    video_url = videos[0].get("url")
                else:
                    video_url = q_data.get("url") or q_data.get("file_url")
                
                if video_url:
                    break
                else:
                    raise KeyError("missing url")
            except Exception as e:
                raise VideoGenError(f"SiliconFlow succeeded but missing video url: {q_data}. Error: {e}")
        elif status == "failed" or status == "error":
            reason = q_data.get("reason", "Unknown error")
            raise VideoGenError(f"SiliconFlow task failed: {reason} | Response: {q_data}")
        
        logger.debug("SiliconFlow task {} is {}...", task_id, status)
        
    if not video_url:
        raise VideoGenError(f"SiliconFlow task {task_id} timed out after 60 minutes.")
        
    # 3. Download Video
    logger.info("Downloading SiliconFlow video for task {}...", task_id)
    for attempt in range(5):
        try:
            vid_resp = requests.get(video_url, stream=True, timeout=60)
            vid_resp.raise_for_status()
            
            with open(save_path, "wb") as f:
                for chunk in vid_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Failed to download video after 5 attempts: {e}")
            logger.warning("Download error for {}: {}, retrying {}/5...", task_id, str(e), attempt + 1)
            time.sleep(5)
            
    logger.success("SiliconFlow video downloaded successfully to {}", save_path)
    return save_path

def _kling_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    import os
    KLING_AK = os.getenv("KLING_AK", "").strip()
    KLING_SK = os.getenv("KLING_SK", "").strip()
    if not KLING_AK or not KLING_SK:
        raise VideoGenError("KLING_AK or KLING_SK is not configured in .env")

    # Generate JWT token
    headers_jwt = {"alg": "HS256", "typ": "JWT"}
    payload_jwt = {
        "iss": KLING_AK,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    token = jwt.encode(payload_jwt, KLING_SK.encode("utf-8"), algorithm="HS256", headers=headers_jwt)
    
    headers = {
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
    
    logger.info("Submitting Kling I2V task for image: {}", Path(image_path).name)
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=60)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Kling submit failed after 5 attempts: {e}")
            logger.warning("Kling API post error: {}, retrying {}/5...", str(e), attempt+1)
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
        
    logger.info("Kling task submitted. Task ID: {}", task_id)
    
    # 2. Poll Status
    query_url = f"https://api-beijing.klingai.com/v1/videos/image2video/{task_id}"
    video_url = None
    
    for _ in range(360):
        time.sleep(10)
        try:
            # Kling JWT token expires in 30 mins, re-generate it to be safe for long polling
            payload_jwt["exp"] = int(time.time()) + 1800
            payload_jwt["nbf"] = int(time.time()) - 5
            token = jwt.encode(payload_jwt, KLING_SK.encode("utf-8"), algorithm="HS256", headers=headers_jwt)
            headers["Authorization"] = f"Bearer {token}"
            
            q_resp = requests.get(query_url, headers=headers, timeout=15)
            q_resp.raise_for_status()
            q_data = q_resp.json()
        except Exception as e:
            logger.warning("Kling API poll error for task {}: {}, retrying...", task_id, str(e))
            continue
        
        status = q_data.get("data", {}).get("task_status", "").lower()
        if status == "succeed":
            try:
                results = q_data.get("data", {}).get("task_result", {})
                videos = results.get("videos", [])
                if videos and len(videos) > 0:
                    video_url = videos[0].get("url")
                if video_url:
                    break
                else:
                    raise KeyError("missing url")
            except Exception as e:
                raise VideoGenError(f"Kling succeeded but missing url: {q_data}. Error: {e}")
        elif status == "failed":
            reason = q_data.get("data", {}).get("task_status_msg", "Unknown error")
            raise VideoGenError(f"Kling task failed: {reason} | Response: {q_data}")
        
        logger.debug("Kling task {} is {}...", task_id, status)
        
    if not video_url:
        raise VideoGenError(f"Kling task {task_id} timed out.")
        
    # 3. Download
    logger.info("Downloading Kling video for task {}...", task_id)
    for attempt in range(5):
        try:
            vid_resp = requests.get(video_url, stream=True, timeout=60)
            vid_resp.raise_for_status()
            
            with open(save_path, "wb") as f:
                for chunk in vid_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Failed to download video after 5 attempts: {e}")
            logger.warning("Download error for {}: {}, retrying {}/5...", task_id, str(e), attempt + 1)
            time.sleep(5)
            
    logger.success("Kling video downloaded successfully to {}", save_path)
    return save_path

def _hailuo_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    """调用 Minimax 海螺视频 API (hailuo-02) 进行图生视频"""
    if not HAILUO_API_KEY:
        raise VideoGenError("HAILUO_API_KEY is not configured in .env")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {HAILUO_API_KEY.strip()}"
    }

    # 1. 提交任务
    submit_url = f"{HAILUO_API_URL}/video_generation"
    img_data_uri = _image_to_base64_data_uri(image_path)
    
    payload = {
        "model": "video-01",  # 默认海螺图生视频模型
        "prompt": prompt,
        "first_frame_image": img_data_uri
    }
    
    logger.info("Submitting Hailuo I2V task for image: {}", Path(image_path).name)
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=60)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Hailuo submit failed after 5 attempts: {e}")
            logger.warning(f"Hailuo API post error: {e}, retrying {attempt+1}/5...")
            time.sleep(5)
            
    try:
        resp_data = resp.json()
    except Exception:
        raise VideoGenError(f"Hailuo submit failed, invalid JSON response: {resp.text}")
    
    if resp.status_code != 200:
        raise VideoGenError(f"Hailuo submit failed: {resp_data}")
        
    task_id = resp_data.get("task_id")
    if not task_id:
        raise VideoGenError(f"Hailuo submit failed, no task_id found: {resp_data}")
        
    logger.info("Hailuo task submitted. Task ID: {}", task_id)
    
    # 2. 轮询状态
    query_url = f"{HAILUO_API_URL}/query/video_generation?task_id={task_id}"
    video_url = None
    
    for _ in range(360): # Poll up to 60 minutes (360 * 10s)
        time.sleep(10)
        
        try:
            q_resp = requests.get(query_url, headers=headers, timeout=15)
            q_resp.raise_for_status()
            q_data = q_resp.json()
        except Exception as e:
            logger.warning("Hailuo API poll error for task {}: {}, retrying...", task_id, str(e))
            continue
        
        status = q_data.get("status", "").lower()
        if status == "success" or status == "completed":
            try:
                video_url = q_data.get("file_id")
                if video_url:
                    # In some cases Hailuo returns the URL as file_id, or we need to fetch the file URL.
                    # Usually if it's an http link we can download it directly.
                    if not video_url.startswith("http"):
                        # It is a file_id, we need to fetch the actual URL
                        file_id = video_url
                        file_url_endpoint = f"{HAILUO_API_URL}/files/retrieve?file_id={file_id}"
                        try:
                            f_resp = requests.get(file_url_endpoint, headers=headers, timeout=10)
                            f_resp.raise_for_status()
                            f_data = f_resp.json()
                            if "file" in f_data and "download_url" in f_data["file"]:
                                video_url = f_data["file"]["download_url"]
                            else:
                                raise VideoGenError(f"Could not parse download_url from file retrieve response: {f_data}")
                        except Exception as e:
                            raise VideoGenError(f"Failed to retrieve file URL for file_id {file_id}: {e}")
                    break
            except Exception as e:
                raise VideoGenError(f"Hailuo succeeded but failed to get url from {q_data}. Error: {e}")
        elif status == "fail" or status == "error":
            reason = q_data
            raise VideoGenError(f"Hailuo task failed: {reason}")
        
        logger.debug("Hailuo task {} is {}...", task_id, status)
        
    if not video_url:
        raise VideoGenError(f"Hailuo task {task_id} timed out.")
        
    # 3. 下载视频
    logger.info("Downloading Hailuo video for task {}...", task_id)
    for attempt in range(5):
        try:
            vid_resp = requests.get(video_url, stream=True, timeout=60)
            vid_resp.raise_for_status()
            
            with open(save_path, "wb") as f:
                for chunk in vid_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Failed to download video after 5 attempts: {e}")
            logger.warning("Download error for {}: {}, retrying {}/5...", task_id, str(e), attempt + 1)
            time.sleep(5)
            
    logger.success("Hailuo video downloaded successfully to {}", save_path)
    return save_path

@retry(retry=retry_if_exception_type(VideoGenError),
       stop=stop_after_attempt(5),
       wait=wait_exponential(min=10, max=60), reraise=True)
def _zhipu_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    if not ZHIPU_API_KEY:
        raise VideoGenError("ZHIPU_API_KEY is not configured in .env")

    from zhipuai import ZhipuAI
    client = ZhipuAI(api_key=ZHIPU_API_KEY)

    logger.info("Submitting Zhipu CogVideoX-Flash I2V task for image: {}", Path(image_path).name)
    
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

    logger.info("Zhipu task submitted. Task ID: {}", task_id)

    # Poll status
    video_url = None
    for _ in range(360):
        time.sleep(10)
        try:
            result = client.videos.retrieve_videos_result(id=task_id)
        except Exception as e:
            logger.warning("Zhipu API poll error for task {}: {}, retrying...", task_id, str(e))
            continue
            
        status = result.task_status.upper()
        if status == "SUCCESS":
            if result.video_result and len(result.video_result) > 0:
                video_url = result.video_result[0].url
                break
            else:
                raise VideoGenError("Zhipu succeeded but returned no video_url.")
        elif status == "FAIL":
            raise VideoGenError("Zhipu task failed on server side.")
            
        logger.debug("Zhipu task {} is {}...", task_id, status)
        
    if not video_url:
        raise VideoGenError(f"Zhipu task {task_id} timed out.")
        
    logger.info("Downloading Zhipu video for task {}...", task_id)
    for attempt in range(5):
        try:
            vid_resp = requests.get(video_url, stream=True, timeout=60)
            vid_resp.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in vid_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Failed to download Zhipu video after 5 attempts: {e}")
            logger.warning("Download error for {}: {}, retrying {}/5...", task_id, str(e), attempt + 1)
            time.sleep(5)
            
    logger.success("Zhipu video downloaded successfully to {}", save_path)
    return save_path

def generate_single_clip(
    scene_index: int,
    image_path: str,
    save_path: Path,
    camera_note: str = ""
) -> Path:
    """生成单个视频片段。"""
    if VIDEO_PROVIDER == "zhipu":
        logger.info("Generating clip scene {} via Zhipu CogVideoX API...", scene_index)
        return _zhipu_generate(image_path, save_path, prompt=camera_note)
    elif VIDEO_PROVIDER == "hailuo":
        logger.info("Generating clip scene {} via Hailuo API...", scene_index)
        return _hailuo_generate(image_path, save_path, prompt=camera_note)
    else:
        logger.info("Generating clip scene {} via Kling API...", scene_index)
        return _kling_generate(image_path, save_path, prompt=camera_note)

def generate_video_clips(
    scenes: list[dict],
    image_manifest: dict[int, str],
    episode_tag: str,
    episode_id: Optional[int] = None,
) -> dict[int, str]:
    """并发将所有分镜静态图转换为视频片段（含断点续跑）。"""
    video_dir = STORAGE_TEMP_DIR / episode_tag / "clips"
    video_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, str] = {}
    errors: list[str] = []

    def _worker(scene: dict) -> tuple[int, str]:
        idx = scene["scene_index"]
        camera_note = scene.get("camera_note", "")
        
        # Golden 3 Seconds pacing: Force aggressive/Kubrick motion on first 2 scenes
        if idx in [1, 2]:
            camera_note += ", extremely fast zoom in, terrifying kubrick style symmetrical push"
        
        img_path = image_manifest.get(idx, "")
        if not img_path or not Path(img_path).exists():
            raise VideoGenError(f"Scene {idx}: image not found at '{img_path}'")
        
        save_path = video_dir / f"scene_{idx:02d}.mp4"

        # 检查数据库断点
        if episode_id is not None:
            with get_session() as session:
                asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                if not asset:
                    asset = SceneAsset(episode_id=episode_id, scene_index=idx, video_status="PENDING")
                    session.add(asset)
                    session.commit()
                else:
                    if asset.video_status == "COMPLETED" and save_path.exists():
                        logger.info("Scene {} video already generated, skipping.", idx)
                        return idx, str(save_path)

        try:
            generate_single_clip(
                scene_index=idx,
                image_path=img_path,
                save_path=save_path,
                camera_note=camera_note
            )
            # 更新成功状态
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

    # 可灵 API 支持并发，使用设定的 MAX_WORKERS
    workers = min(MAX_WORKERS, len(scenes))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, s): s["scene_index"] for s in scenes}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                i, path = fut.result()
                results[i] = path
                logger.info("Clip [{}/{}] done: scene {}", len(results), len(scenes), i)
            except VideoGenError as e:
                logger.error("Clip scene {} failed: {}", idx, e)
                errors.append(f"scene_{idx}: {e}")

    if errors:
        raise VideoGenError(f"{len(errors)} clip(s) failed:\n" + "\n".join(errors))
    logger.success("All {} video clips done for {}.", len(results), episode_tag)
    return results
