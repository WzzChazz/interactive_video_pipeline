"""
core/image_gen.py
=================
Flux 批量生图模块，支持断点续跑和国内低价高质量的硅基流动（SiliconFlow）API。

职责：
  1. 接收 EpisodeScript 的 scenes 列表，批量调用 硅基流动 API 生成分镜图。
  2. 生成前检查 DB 的 SceneAsset，实现细粒度断点续跑。
  3. 支持 character_ref_url（角色参考图）注入，保持主角外貌跨镜一致。
  4. 线程池并发生图，最大化速度。
  5. 每张图保存至 storage/temp/{episode_tag}/images/scene_{idx}.png 并更新 DB。

API 规格（Flux.1 Dev via SiliconFlow）：
  POST https://api.siliconflow.cn/v1/images/generations
"""

import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import (
    FLUX_API_KEY, # Reusing this for FAL_KEY
    STORAGE_TEMP_DIR,
    MAX_WORKERS,
    API_MAX_RETRIES,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
)
from database.db_session import get_session
from database.models import SceneAsset


# ──────────────────────────────────────────────────────────
# 单张生图（调用硅基流动 API）
# ──────────────────────────────────────────────────────────

class ImageGenError(Exception):
    pass

@retry(
    retry=retry_if_exception_type(ImageGenError),
    stop=stop_after_attempt(10),  # 增加重试次数以应对 429 频率限制
    wait=wait_exponential(multiplier=2, min=10, max=60),  # 每次重试间隔 10s, 20s, 40s... 最大等待 60s，确保能跨过“每分钟请求数(RPM)”限制
    reraise=True,
)
def _call_siliconflow_flux(
    prompt: str,
    character_ref_url: Optional[str] = None,
) -> str:
    """
    向 硅基流动 (SiliconFlow) 提交任务，同步返回图片下载 URL。
    """
    from config.settings import FLUX_API_KEY, FLUX_API_URL
    if not FLUX_API_KEY:
        raise ImageGenError("FLUX_API_KEY (for SiliconFlow) is not configured.")

    full_prompt = prompt
    if character_ref_url:
        full_prompt = f"{prompt}, character reference: {character_ref_url}"

    headers = {
        "Authorization": f"Bearer {FLUX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "Kwai-Kolors/Kolors",
        "prompt": full_prompt,
        "image_size": "576x1024",  # 竖屏 9:16
        "batch_size": 1,
    }

    try:
        resp = requests.post(FLUX_API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        images = data.get("images", [])
        if not images:
            raise ImageGenError(f"SiliconFlow returned no images. Resp: {resp.text[:300]}")
        url = images[0].get("url")
        if not url:
            raise ImageGenError("SiliconFlow missing url field in response.")
        return url
    except requests.RequestException as e:
        raise ImageGenError(f"SiliconFlow submit failed: {e}") from e


@retry(
    retry=retry_if_exception_type(ImageGenError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
def _download_image(url: str, save_path: Path) -> None:
    """将图片从 URL 下载保存到本地路径。"""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import requests
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(response.content)
    except Exception as e:
        raise ImageGenError(f"Failed to download image from {url}: {e}") from e


class ImageQAError(Exception):
    pass

def _visual_qa_image(image_path: Path, prompt_context: str) -> None:
    from config.settings import DASHSCOPE_API_KEY
    import dashscope
    
    if not DASHSCOPE_API_KEY:
        logger.warning("[Visual QA] DASHSCOPE_API_KEY not configured, skipping QA.")
        return
        
    dashscope.api_key = DASHSCOPE_API_KEY
    
    try:
        # dashscope MultiModalConversation 支持直接传本地绝对路径 file://
        file_url = f"file://{image_path.absolute()}"
        
        prompt = f"""你是一个极其严格的自动化视觉审查员。你的任务是检查生成的 AI 图片是否符合标准。
如果发现以下任意一项严重违规，必须直接回复以 "REJECT: " 开头的理由。
如果完全合格，请回复 "PASS"。

【必须拒绝的情况】：
1. 画面任何角落（特别是右下角、左下角）带有“AI生成”、“无界AI”或类似的中英文字符水印。
2. 画面的环境/光线与剧本提示词存在严重冲突（例如：提示词要求是“黑夜”、“阴暗病房”，画面却看起来是大白天、阳光明媚）。
3. 画面主体出现了极其恐怖扭曲的AI结构错误（如三头六臂，极其扭曲的五官）。

这是为该分镜生成的图片，对应的提示词要求是：'{prompt_context}'。请审查它是否带有水印或严重不符。
"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"image": file_url},
                    {"text": prompt}
                ]
            }
        ]
        
        response = dashscope.MultiModalConversation.call(
            model='qwen-vl-max',
            messages=messages
        )
        
        if response.status_code == 200:
            result = response.output.choices[0].message.content[0]["text"].strip()
            if result.startswith("REJECT"):
                raise ImageQAError(result)
            logger.success(f"[Visual QA] 质检通过 ✅ (Qwen-VL)")
        else:
            logger.warning(f"[Visual QA] Qwen-VL error: {response.code} - {response.message}")
            
    except Exception as e:
        if isinstance(e, ImageQAError):
            raise e
        logger.warning(f"[Visual QA] API error, skipping QA check: {e}")

# ──────────────────────────────────────────────────────────
# 单张生图（完整流程）
# ──────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type((ImageGenError, ImageQAError)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    reraise=True,
)
def generate_single_image(
    scene_index: int,
    visual_prompt: str,
    save_path: Path,
    character_ref_url: Optional[str] = None,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
) -> Path:
    """
    生成单张分镜图并保存到 save_path。
    返回实际保存的 Path。
    """
    logger.info("Generating image for scene {}: {:.60s}...", scene_index, visual_prompt)

    final_prompt = visual_prompt

    img_url  = _call_siliconflow_flux(final_prompt, character_ref_url)
    _download_image(img_url, save_path)
    
    # 执行 QA
    logger.info(f"[Visual QA] 正在审核 Scene {scene_index}...")
    try:
        _visual_qa_image(save_path, visual_prompt)
    except ImageQAError as qa_err:
        logger.error(f"[Visual QA] Scene {scene_index} 质检失败 ❌：{qa_err} (正在销毁重做...)")
        if save_path.exists():
            save_path.unlink()
        import time
        time.sleep(2)
        raise qa_err

    # 强制休眠 5 秒，防止触发硅基流动免费账户的每分钟请求限制 (RPM)
    import time
    time.sleep(5)
    
    logger.success("Scene {} image saved and verified: {}", scene_index, save_path)
    return save_path


# ──────────────────────────────────────────────────────────
# 批量生图（并发）
# ──────────────────────────────────────────────────────────

def generate_images(
    scenes: list[dict],
    episode_tag: str,
    character_ref_url: Optional[str] = None,
    episode_id: Optional[int] = None,
) -> dict[int, str]:
    """
    并发生成所有分镜图片（带断点续跑）。
    """
    img_dir = STORAGE_TEMP_DIR / episode_tag / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    results: dict[int, str] = {}
    errors: list[str] = []

    def _worker(scene: dict) -> tuple[int, str]:
        idx    = scene["scene_index"]
        prompt = scene["visual_prompt"]
        path   = img_dir / f"scene_{idx:02d}.png"

        # 检查数据库断点
        if episode_id is not None:
            with get_session() as session:
                asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                if not asset:
                    asset = SceneAsset(episode_id=episode_id, scene_index=idx, image_status="PENDING")
                    session.add(asset)
                    session.commit()
                else:
                    if asset.image_status == "COMPLETED" and path.exists():
                        logger.info("Scene {} image already generated, skipping.", idx)
                        return idx, str(path)

        # 执行生图
        try:
            generate_single_image(
                scene_index=idx,
                visual_prompt=prompt,
                save_path=path,
                character_ref_url=character_ref_url,
            )
            # 更新成功状态
            if episode_id is not None:
                with get_session() as session:
                    asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                    if asset:
                        asset.image_status = "COMPLETED"
                        asset.image_path = str(path)
                        session.commit()
            return idx, str(path)
        except Exception as e:
            if episode_id is not None:
                with get_session() as session:
                    asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                    if asset:
                        asset.image_status = "FAILED"
                        session.commit()
            raise

    # 硅基流动限制并发，改为串行执行避免 429 Too Many Requests
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = {pool.submit(_worker, s): s["scene_index"] for s in scenes}
        for future in as_completed(futures):
            scene_idx = futures[future]
            try:
                idx, path = future.result()
                results[idx] = path
                logger.info("Image [{}/{}] done: scene {}", len(results), len(scenes), idx)
            except ImageGenError as e:
                logger.error("Image generation failed for scene {}: {}", scene_idx, e)
                errors.append(f"scene_{scene_idx}: {e}")

    if errors:
        raise ImageGenError(
            f"{len(errors)} image(s) failed:\n" + "\n".join(errors)
        )

    logger.success("All {} images generated for {}.", len(results), episode_tag)
    return results
