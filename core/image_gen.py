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


# ──────────────────────────────────────────────────────────
# 单张生图（完整流程）
# ──────────────────────────────────────────────────────────

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

    # 强制休眠 5 秒，防止触发硅基流动免费账户的每分钟请求限制 (RPM)
    time.sleep(5)
    
    logger.success("Scene {} image saved: {}", scene_index, save_path)
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
