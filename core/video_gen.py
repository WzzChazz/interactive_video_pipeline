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
    JIMENG_API_KEY,
    JIMENG_API_URL,
    JIMENG_MODEL,
    KEN_BURNS_ONLY,
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

def check_video_brightness(video_path: Path) -> None:
    """自动使用 FFmpeg 检查视频亮度，过亮则抛出红字警告"""
    import subprocess
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "frame=tags=lavfi.signalstats.YAVG",
            "-of", "default=noprint_wrappers=1:nokey=1", "-vf", "signalstats", str(video_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = [float(x.strip()) for x in res.stdout.split() if x.strip()]
        if not lines: return
        avg_y = sum(lines) / len(lines)
        if avg_y > 150:  # 0-255，仅作信息记录（治愈题材本就明亮，不再当作错误）
            logger.debug(f"[亮度] 视频 {video_path.name} 偏亮 (YAVG={avg_y:.1f})")
        else:
            logger.debug(f"[质量检查] 视频 {video_path.name} 亮度合规 (YAVG={avg_y:.1f})")
    except Exception as e:
        logger.warning(f"亮度检测失败: {e}")

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
            err_str = str(e)
            if any(term in err_str for term in ["400", "401", "403", "1301", "不安全", "敏感", "FAIL"]):
                raise VideoGenError(f"Task {task_id} failed permanently (Safety/Auth Error): {err_str}")
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
    check_video_brightness(save_path)
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
        # kling-v1(2024) 已老;默认升到 v1-6,可用 KLING_MODEL 环境变量覆盖(无key未实测,兜底通道)
        "model_name": os.getenv("KLING_MODEL", "kling-v1-6"),
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
        # video-01(2024) 已老;默认升到 Hailuo-02,可用 HAILUO_MODEL 环境变量覆盖(无key未实测,兜底通道)
        "model": os.getenv("HAILUO_MODEL", "MiniMax-Hailuo-02"),
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

def _aliyun_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    """调用阿里云通义万相 (Wan) 图生视频模型，完美解决毁图问题且极低成本。"""
    import os
    import requests
    
    from dotenv import load_dotenv
    load_dotenv()
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not DASHSCOPE_API_KEY:
        raise VideoGenError("DASHSCOPE_API_KEY is not configured in .env")

    img_data_uri = _image_to_base64_data_uri(image_path)
    
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "X-DashScope-Async": "enable",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "wan2.7-i2v",
        "input": {
            "prompt": prompt,
            "media": [
                {
                    "type": "first_frame",
                    "url": img_data_uri
                }
            ]
        },
        "parameters": {
            "resolution": "720P"
        }
    }
    
    submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    
    # 无限制缓冲：增加 1 次自动重试应对网络抖动
    task_id = None
    data = {}
    for attempt in range(2):
        try:
            resp = requests.post(submit_url, headers=headers, json=payload, timeout=120)
            data = resp.json()
            if "output" not in data or "task_id" not in data["output"]:
                raise ValueError(f"No task_id in response: {data}")
            task_id = data["output"]["task_id"]
            break
        except Exception as e:
            if attempt == 1:
                raise VideoGenError(f"Aliyun Wan API submit failed after retry: {e}")
            logger.warning(f"Aliyun API submit failed, retrying in 3s... ({e})")
            time.sleep(3)
            
    logger.info(f"Aliyun Wan task submitted. Task ID: {task_id}")
    
    query_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    
    def fetch_status():
        qr = requests.get(query_url, headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}"})
        qdata = qr.json()
        if "output" not in qdata:
            return "failed", None
            
        status = qdata["output"]["task_status"]
        if status == "SUCCEEDED":
            return "success", qdata
        elif status == "FAILED":
            logger.error(f"Aliyun Wan task failed: {qdata}")
            return "failed", None
        return "processing", None
        
    def extract_url(qdata):
        return qdata["output"]["video_url"]
        
    return _poll_and_download_atomic(task_id, fetch_status, extract_url, save_path)


@retry(retry=retry_if_exception_type(VideoGenError), stop=stop_after_attempt(API_MAX_RETRIES), wait=wait_exponential(min=10, max=60), reraise=True)
def _seedance_generate_one(image_path: str, save_path: Path, prompt: str, model: str) -> Path:
    """即梦 Seedance 图生视频（火山引擎 Ark）单个模型。提交任务 → 轮询 → 下载。"""
    if not JIMENG_API_KEY:
        raise VideoGenError("JIMENG_API_KEY (火山引擎 Ark) 未在 .env 配置")

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {JIMENG_API_KEY}"}
    # 4K 图直接 base64 上传又慢又没必要（Seedance 内部自定分辨率）→ 先缩到 1280 宽
    import subprocess, tempfile
    _small = Path(tempfile.gettempdir()) / f"sd_in_{Path(image_path).stem}.jpg"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(image_path), "-vf", "scale=1280:-2",
                        "-q:v", "3", str(_small)], capture_output=True, timeout=30)
        _src = str(_small) if (_small.exists() and _small.stat().st_size > 0) else str(image_path)
    except Exception:
        _src = str(image_path)
    img_data_uri = _image_to_base64_data_uri(_src)
    # 治愈系：竖屏 9:16、5 秒、轻微运动；Seedance 用 --ratio/--duration 文本参数
    text = (prompt.strip() or "gentle subtle motion, cozy and calm") + " --ratio 9:16 --duration 5"
    payload = {
        "model": model,
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": img_data_uri}},
        ],
    }

    logger.info(f"Submitting Seedance(即梦) I2V task for: {Path(image_path).name}")
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(JIMENG_API_URL, json=payload, headers=headers, timeout=60)
            break
        except Exception as e:
            if attempt == 4:
                raise VideoGenError(f"Seedance submit failed after 5 attempts: {e}")
            time.sleep(5)

    try:
        rd = resp.json()
    except Exception:
        raise VideoGenError(f"Seedance submit invalid JSON: {resp.text[:300]}")
    if resp.status_code != 200:
        raise VideoGenError(f"Seedance submit failed: {rd}")
    task_id = rd.get("id") or rd.get("task_id")
    if not task_id:
        raise VideoGenError(f"Seedance submit returned no task id: {rd}")
    logger.info(f"Seedance task submitted. Task ID: {task_id}")

    def fetch_status():
        q = requests.get(f"{JIMENG_API_URL}/{task_id}", headers=headers, timeout=15)
        q.raise_for_status()
        d = q.json()
        st = (d.get("status") or "").lower()
        # Ark 用 succeeded/failed/running/queued，归一化到 poll helper 认的词
        st = {"succeeded": "success", "failed": "failed"}.get(st, st)
        return st, d

    def extract_url(d):
        content = d.get("content") or {}
        return content.get("video_url") or d.get("video_url")

    return _poll_and_download_atomic(task_id, fetch_status, extract_url, save_path)


# 免费额度耗尽 / ID无效的模型 → 持久化到文件(重启不再反复撞429),72小时后自动重试(防欠费充值后仍被跳过)
_EXHAUSTED_FILE = STORAGE_TEMP_DIR / "seedance_exhausted.json"
_EXHAUSTED_TTL_HOURS = 72

def _load_exhausted() -> dict:
    try:
        import json
        data = json.loads(_EXHAUSTED_FILE.read_text())
        now = time.time()
        return {m: ts for m, ts in data.items() if now - ts < _EXHAUSTED_TTL_HOURS * 3600}
    except Exception:
        return {}

_SEEDANCE_EXHAUSTED: dict = _load_exhausted()

def _mark_exhausted(model: str) -> None:
    _SEEDANCE_EXHAUSTED[model] = time.time()
    try:
        import json
        _EXHAUSTED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _EXHAUSTED_FILE.write_text(json.dumps(_SEEDANCE_EXHAUSTED))
    except Exception as e:
        logger.warning(f"[Seedance] 持久化 exhausted 失败(仅影响重启后记忆): {e}")

def _seedance_generate(image_path: str, save_path: Path, prompt: str = "") -> Path:
    """Seedance 模型链：便宜优先。某个用尽/ID无效自动切下一个(持久化跳过,72h后重试)。"""
    from config.settings import JIMENG_MODEL_CHAIN
    chain = [m.strip() for m in JIMENG_MODEL_CHAIN.split(",") if m.strip()] or [JIMENG_MODEL]
    last_err = None
    for model in chain:
        if model in _SEEDANCE_EXHAUSTED:
            continue
        try:
            logger.info(f"[Seedance] 使用模型 {model}")
            return _seedance_generate_one(image_path, save_path, prompt, model)
        except Exception as e:
            es = str(e).lower()
            # 额度耗尽/无余额/模型ID无效 → 标记跳过，换下一个
            if any(q in es for q in ["arrearage", "quota", "欠费", "balance", "insufficient",
                                     "余额", "exhaust", "notfound", "not found", "invalidendpoint",
                                     "invalid endpoint", "404"]):
                logger.warning(f"[Seedance] {model} 不可用(额度尽/ID错) → 切下一个(72h内跳过)")
                _mark_exhausted(model)
                last_err = e
                continue
            raise
    raise last_err or VideoGenError("所有 Seedance 模型均不可用")


_PROVIDER_PRIORITY = ["seedance", "aliyun", "kling", "siliconflow", "zhipu", "hailuo"]
_QUOTA_ERRORS = ["arrearage", "429", "overdue", "quota", "balance", "insufficient", "rate limit", "token"]

def generate_single_clip(scene_index: int, image_path: str, save_path: Path, camera_note: str = "", theme_key: str = "hospital_horror") -> Path:
    """生成单个视频片段，自带容灾兜底和多引擎自动降级。"""
    providers = _PROVIDER_PRIORITY.copy()
    if VIDEO_PROVIDER in providers:
        providers.remove(VIDEO_PROVIDER)
        providers.insert(0, VIDEO_PROVIDER)
        
    for provider in providers:
        try:
            logger.info(f"Generating clip scene {scene_index} via {provider.capitalize()} API...")
            if provider == "seedance":
                return _seedance_generate(image_path, save_path, prompt=camera_note)
            elif provider == "zhipu":
                return _zhipu_generate(image_path, save_path, prompt=camera_note)
            elif provider == "aliyun":
                return _aliyun_generate(image_path, save_path, prompt=camera_note)
            elif provider == "hailuo":
                return _hailuo_generate(image_path, save_path, prompt=camera_note)
            elif provider == "siliconflow":
                return _siliconflow_generate(image_path, save_path, prompt=camera_note)
            else:
                return _kling_generate(image_path, save_path, prompt=camera_note)
        except Exception as e:
            err_str = str(e).lower()
            logger.warning(f"[VideoGen] {provider} failed for scene {scene_index}: {e}")
            # Automatically try next provider on ANY failure (including quota)
            continue
            
    logger.error(f"All generative APIs failed for scene {scene_index}. Activating Stock Footage Fallback...")
    from core.stock_footage_fallback import fetch_fallback_video
    
    from config.themes import THEMES
    _healing = not THEMES.get(theme_key, {}).get("is_serial", True)
    if _healing:
        keyword = "cozy warm cute pet, soft daylight"
    else:
        keyword = "dark eerie background"
        if camera_note:
            words = camera_note.replace(",", " ").split()
            if len(words) >= 2:
                keyword = f"{words[0]} {words[1]} dark"

    return fetch_fallback_video(keyword, save_path, image_path=image_path)

def make_ken_burns_clip(image_path: str, save_path: Path, duration: float = 5.0, seed: int = 0) -> Path:
    """把静图做成缓慢推拉(Ken Burns)的视频——治愈系免费替代图生视频，且慢镜更治愈。"""
    import subprocess
    from config.settings import VIDEO_WIDTH, VIDEO_HEIGHT
    fps = 30  # 与 API 片段标准化后的 30fps 对齐，避免混合拼接时帧率不一致导致卡顿
    frames = max(1, int(duration * fps))
    # 按 seed 交替缓慢放大/缩小，避免每条都一样
    if seed % 2 == 0:
        z = "min(zoom+0.0010,1.18)"
    else:
        z = "if(lte(zoom,1.0),1.18,max(zoom-0.0010,1.0))"
    vf = (
        f"scale={VIDEO_WIDTH*2}:{VIDEO_HEIGHT*2}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH*2}:{VIDEO_HEIGHT*2},"
        f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-vf", vf, "-t", str(duration), "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(save_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info(f"[KenBurns] 静图推拉片段生成: {save_path.name}")
    return save_path


def make_punch_cut_clip(image_path: str, save_path: Path, duration: float = 5.0, seed: int = 0) -> Path:
    """开场快切（治愈系第1镜专用）：同一张爆点图按递进景别【硬切】成多段(每段<1s)，头部制造
    pattern interrupt 压低 2 秒跳出率；最后定格在最紧景别软着陆补满 duration，保持治愈感不破。
    纯 FFmpeg、零成本。compiler 用 outpoint 从头部裁剪 → 即使裁到 ~2.5s 也保留头部快切。"""
    import subprocess, tempfile
    from config.settings import VIDEO_WIDTH, VIDEO_HEIGHT
    fps = 30
    # (zoom 推近倍数, focus_y 纵向取景偏上对脸, seg_dur 段时长)
    # 前 3 段快切(<1s)抓拇指：全景→推一档→怼脸；末段长定格软着陆。
    segments = [
        (1.00, 0.50, 0.60),
        (1.18, 0.40, 0.60),
        (1.42, 0.36, 0.70),
    ]
    fast = sum(s[2] for s in segments)
    segments.append((1.42, 0.36, max(0.5, duration - fast)))  # 软着陆定格

    tmp_dir = Path(tempfile.gettempdir())
    parts: list[Path] = []
    try:
        for i, (z, fy, seg_dur) in enumerate(segments):
            part = tmp_dir / f"punch_{Path(image_path).stem}_{seed}_{i}.mp4"
            vf = (
                f"crop=iw/{z:.4f}:ih/{z:.4f}:(iw-iw/{z:.4f})/2:(ih-ih/{z:.4f})*{fy:.3f},"
                f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,fps={fps}"
            )
            subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
                "-vf", vf, "-t", f"{seg_dur:.3f}", "-r", str(fps),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(part),
            ], check=True, capture_output=True)
            parts.append(part)

        concat_list = tmp_dir / f"punch_concat_{Path(image_path).stem}_{seed}.txt"
        with open(concat_list, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(save_path),
        ], check=True, capture_output=True)
    finally:
        for p in parts:
            try: p.unlink()
            except Exception: pass
        try: concat_list.unlink()
        except Exception: pass

    logger.info(f"[PunchCut] 开场快切片段生成: {save_path.name}（{len(segments)}段硬切）")
    return save_path


def generate_video_clips(scenes: list[dict], image_manifest: dict[int, str], episode_tag: str, episode_id: Optional[int] = None, theme_key: str = "hospital_horror") -> dict[int, str]:
    """并发将分镜静图转视频。治愈题材：静镜走免费Ken Burns，仅 needs_motion 的动作镜走图生视频。"""
    from config.themes import THEMES
    healing = not THEMES.get(theme_key, {}).get("is_serial", True)
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
        
        # 提取更丰富的 visual_prompt
        vp = scene.get("visual_prompt", {})
        pose = vp.get("pose", "") if isinstance(vp, dict) else ""
        camera_type = vp.get("type", "Cinematic shot") if isinstance(vp, dict) else ""
        base_camera = scene.get("camera_note", "")
        
        # 按题材组装图生视频提示词（治愈 vs 恐怖）
        if healing:
            # 借鉴 Seedance「时间戳分镜法」：把 5s 拆成两拍，给模型明确的运动时序，
            # 而非一段静态描述让它自己猜。第二拍专放团团慢半拍的呆萌反应＝放大反差萌卖点。
            act = pose if pose else "settling in cozily with a soft natural micro-movement"
            cam = base_camera or "slow gentle push-in"
            camera_note = (
                f"[Subject] An elegant calm young woman (Lin Xi) and a round chubby derpy capybara (Tuan Tuan), soft anime illustration style.\n"
                f"[Environment] cozy warm home, soft natural daylight, pastel cream tones, plants.\n"
                f"[0-2s] {act}. Only the lightest natural motion — a blink, a breath, hair/clothes swaying gently.\n"
                f"[2-5s] the motion continues softly; Tuan Tuan reacts a beat late with a deadpan derpy little move (slow blink / tiny head tilt).\n"
                f"[Camera] {camera_type}. {cam}, steady and slow, absolutely no shake, no fast motion.\n"
                f"[Atmosphere] cozy healing slice-of-life, warm soft lighting, wholesome, calm and slow.\n"
                f"[Negative] NO text, NO subtitles, NO captions, NO logo, NO watermark; no horror, no darkness, no fast cuts."
            )
        else:
            # 采用 GitHub 开源项目推荐的结构化 Prompt 逻辑，动态组装恐怖悬疑镜头
            action_text = pose if pose else "Exhibiting nervous tension, moving carefully."
            cam_text = f"{camera_type}. {base_camera}" if base_camera else f"{camera_type}. Slow cinematic movement, subtle hand-held camera shake to induce anxiety."
            camera_note = (
                f"[Subject] Characters in focus. {action_text}\\n"
                f"[Environment] Claustrophobic and pitch-black setting, illuminated only by a harsh, narrow light source. Heavy volumetric dust in the air.\\n"
                f"[Action] {action_text}\\n"
                f"[Camera] {cam_text}\\n"
                f"[Atmosphere] Neo-noir suspense thriller, extreme low-key lighting, deep shadows, cinematic color grading, high contrast, mysterious and gripping."
            )
            # 脱敏过滤，把低级敏感词替换为中性悬疑词
            unsafe_words = ["horror", "terrifying", "terror", "dead", "blood", "kill", "creepy", "scary", "ghost", "bloody"]
            for w in unsafe_words:
                camera_note = camera_note.replace(w, "suspense").replace(w.capitalize(), "Suspense")
        
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
            # 快切开场已撤(make_punch_cut_clip 保留但不再路由)：拆解8条爆款证明赢家是"萌脸定格+微动",非快切。
            if healing and (KEN_BURNS_ONLY or not scene.get("needs_motion", False)):
                _why = "验证期全Ken Burns" if KEN_BURNS_ONLY else "静镜"
                logger.info(f"Scene {idx}: {_why} → Ken Burns 缓慢推拉（免费）")
                make_ken_burns_clip(img_path, save_path, duration=5.0, seed=idx)
            else:
                generate_single_clip(
                    scene_index=idx,
                    image_path=img_path,
                    save_path=save_path,
                    camera_note=camera_note,
                    theme_key=theme_key,
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
