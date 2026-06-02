"""
core/audio_gen.py — ElevenLabs TTS + SFX 生成模块
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import (
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL_ID,
    DASHSCOPE_API_KEY, DASHSCOPE_VOICE_ID,
    STORAGE_TEMP_DIR, MAX_WORKERS, API_MAX_RETRIES, CLIP_DURATION_SECONDS,
)
from database.db_session import get_session
from database.models import SceneAsset

_EL_BASE = "https://api.elevenlabs.io/v1"

class AudioGenError(Exception):
    pass


import re
import subprocess
import hashlib

VOICE_ROUTER = {
    "旁白": "zh-CN-YunxiNeural",       # 男声，稳重阳光
    "主角": "zh-CN-YunxiNeural",
    "医生": "zh-CN-YunyangNeural",     # 新闻播音男声，适合沉稳大叔
    "警察": "zh-CN-YunjianNeural",     # 低沉男声，适合悬疑
    "护士": "zh-CN-XiaoxiaoNeural",    # 甜美年轻女声
    "小女孩": "zh-CN-XiaoyiNeural",    # 活泼女声，适合女童
    "神秘人": "zh-CN-YunjianNeural",   # 借用低沉男声替代沙哑
    "反派": "zh-CN-YunjianNeural",
    "DEFAULT": "zh-CN-YunxiNeural"
}

def _get_voice_for_speaker(speaker: str) -> str:
    """动态多角色声纹路由"""
    if not speaker:
        return VOICE_ROUTER["DEFAULT"]
    
    # 精确匹配
    for key, voice in VOICE_ROUTER.items():
        if key in speaker:
            return voice
            
    # 如果没匹配到，根据名字的 hash 稳定分配一个声音，保证同一个未知角色的声音在整部剧中是一致的
    available_voices = list(set(VOICE_ROUTER.values()))
    h = int(hashlib.md5(speaker.encode('utf-8')).hexdigest(), 16)
    return available_voices[h % len(available_voices)]

def _is_scream(text: str) -> bool:
    """极端情绪降级：判断是否是纯尖叫/呼救，用于拦截交给真实音效引擎"""
    if len(text) > 10:
        return False
    if re.search(r"啊+|救命|不+|快跑", text) and ("！" in text or "!" in text):
        return True
    return False

@retry(retry=retry_if_exception_type(AudioGenError),
       stop=stop_after_attempt(API_MAX_RETRIES),
       wait=wait_exponential(min=3, max=30), reraise=True)
def generate_voice(text: str, save_path: Path, emotion: str = "neutral",
                   speaker: str = "", theme_key: str = "hospital_horror") -> Path:
    """调用 edge-tts 生成配音 MP3，并强制生成精确字幕 .vtt"""
    
    # 极端情绪降级拦截：如果是纯尖叫，直接生成真人惨叫音效
    if _is_scream(text):
        logger.warning(f"Scream intercepted: '{text}'. Redirecting to SFX engine for realistic scream.")
        # 我们用英文提示词给 ElevenLabs SFX 让它生成尖叫
        scream_prompt = "A terrifying, realistic, blood-curdling human scream of absolute panic and fear"
        if "女" in speaker or "护士" in speaker:
            scream_prompt = "A terrifying, realistic, blood-curdling female scream of absolute panic and fear"
        elif "男" in speaker:
            scream_prompt = "A terrifying, realistic, blood-curdling male scream of absolute panic and fear"
            
        try:
            generate_sfx(scream_prompt, save_path, duration_seconds=2)
            # 伪造一个空的 VTT，因为尖叫不需要在屏幕上打字幕
            vtt_path = save_path.with_suffix(".vtt")
            vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
            return save_path
        except Exception as e:
            logger.error(f"Scream SFX failed, falling back to TTS: {e}")
            # 如果音效失败，继续往下走 TTS 兜底
            pass

    # SSML 悬疑断句解析：将 [pause:Xs] 替换为标点，或者直接用 SSML（这里用逗号/句号最简单有效）
    # edge-tts 在遇到多个句号时会自动拉长停顿
    processed_text = re.sub(r'\[pause:(\d+\.?\d*)s?\]', lambda m: "。" * max(1, int(float(m.group(1)) * 2)), text)
    processed_text = processed_text.replace("...", "。。。")
    
    voice_id = _get_voice_for_speaker(speaker)
    vtt_path = save_path.with_suffix(".vtt")
    
    # 使用 python3 -m edge_tts 避免全局 PATH 找不到
    cmd = [
        "python3", "-m", "edge_tts",
        "--text", processed_text,
        "--voice", voice_id,
        "--rate=-10%", # 悬疑剧整体语速降10%，增加压迫感。注意必须用 = 号，否则 argparse 会把 -10% 当成 flag
        "--write-media", str(save_path),
        "--write-subtitles", str(vtt_path)
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise AudioGenError(f"Edge-TTS failed: {e.stderr}")
        
    logger.debug("Voice (Edge-TTS) and VTT saved: {}", save_path)
    return save_path


@retry(retry=retry_if_exception_type(AudioGenError),
       stop=stop_after_attempt(API_MAX_RETRIES),
       wait=wait_exponential(min=3, max=30), reraise=True)
def generate_sfx(prompt: str, save_path: Path,
                 duration_seconds: int = CLIP_DURATION_SECONDS) -> Path:
    from config.settings import USE_ELEVENLABS_SFX
    def _generate_fallback_sfx():
        import subprocess
        save_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "aevalsrc='random(0)*0.03*sin(2*PI*t*0.5) + sin(2*PI*t*40)*0.1'",
            "-t", str(duration_seconds),
            "-c:a", "libmp3lame",
            str(save_path)
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.debug(f"Synthetic FFmpeg SFX saved: {save_path}")
            return save_path
        except subprocess.CalledProcessError as e:
            raise AudioGenError(f"Local FFmpeg SFX generation failed: {e.stderr.decode()}")

    if not USE_ELEVENLABS_SFX:
        logger.info("ElevenLabs SFX is disabled via switch. Generating local atmospheric soundscape using FFmpeg lavfi.")
        return _generate_fallback_sfx()
            
    if not ELEVENLABS_API_KEY:
        logger.warning("ELEVENLABS_API_KEY not configured. Falling back to synthetic SFX.")
        return _generate_fallback_sfx()
        
    try:
        resp = requests.post(
            f"{_EL_BASE}/sound-generation",
            json={"text": prompt, "duration_seconds": float(duration_seconds),
                  "prompt_influence": 0.3},
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        if e.response is not None and e.response.status_code in (401, 402, 429):
            logger.warning(f"ElevenLabs SFX API quota/auth error (HTTP {e.response.status_code}). Activating synthetic SFX fallback.")
            return _generate_fallback_sfx()
        logger.error(f"ElevenLabs SFX request failed: {e}. Activating synthetic SFX fallback.")
        return _generate_fallback_sfx()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(resp.content)
    logger.debug("SFX saved ({} bytes): {}", len(resp.content), save_path)
    return save_path


def generate_audio(scenes: list[dict], episode_tag: str, episode_id: Optional[int] = None, theme_key: str = "hospital_horror") -> dict[int, dict[str, str]]:
    """
    并发生成所有分镜的配音 + 音效（支持断点续跑）。

    Returns:
        {scene_index: {"voice": 路径, "sfx": 路径}}
    """
    audio_dir = STORAGE_TEMP_DIR / episode_tag / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, dict[str, str]] = {}
    errors: list[str] = []

    def _worker(scene: dict) -> tuple[int, dict]:
        idx = scene["scene_index"]
        paths: dict[str, str] = {"voice": "", "sfx": ""}
        
        vp = audio_dir / f"scene_{idx:02d}_voice.mp3"
        sp = audio_dir / f"scene_{idx:02d}_sfx.mp3"

        # 检查数据库断点
        if episode_id is not None:
            with get_session() as session:
                asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                if not asset:
                    asset = SceneAsset(episode_id=episode_id, scene_index=idx, audio_status="PENDING")
                    session.add(asset)
                    session.commit()
                else:
                    if asset.audio_status == "COMPLETED" and vp.exists() and sp.exists():
                        logger.info("Scene {} audio already generated, skipping.", idx)
                        paths["voice"] = str(vp)
                        paths["sfx"] = str(sp)
                        return idx, paths

        try:
            if scene.get("dialogue"):
                speaker = scene.get("speaker", "")
                generate_voice(scene["dialogue"], vp, scene.get("emotion", "neutral"), speaker, theme_key)
                paths["voice"] = str(vp)
            
            sfx = scene.get("sfx_prompt", "")
            if sfx and sfx.lower() not in ("ambient silence", ""):
                generate_sfx(sfx, sp)
                paths["sfx"] = str(sp)
            else:
                # 为了保持统一，即使没有sfx也建一个空文件，方便校验 exists()
                sp.touch()
                paths["sfx"] = str(sp)

            # 更新成功状态
            if episode_id is not None:
                with get_session() as session:
                    asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                    if asset:
                        asset.audio_status = "COMPLETED"
                        asset.audio_path = str(vp)
                        session.commit()
            return idx, paths

        except Exception as e:
            if episode_id is not None:
                with get_session() as session:
                    asset = session.query(SceneAsset).filter_by(episode_id=episode_id, scene_index=idx).first()
                    if asset:
                        asset.audio_status = "FAILED"
                        session.commit()
            raise

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(scenes))) as pool:
        futures = {pool.submit(_worker, s): s["scene_index"] for s in scenes}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                i, paths = fut.result()
                results[i] = paths
                logger.info("Audio [{}/{}] scene {} done", len(results), len(scenes), i)
            except AudioGenError as e:
                logger.error("Audio scene {} failed: {}", idx, e)
                errors.append(f"scene_{idx}: {e}")

    if errors:
        raise AudioGenError(f"{len(errors)} audio(s) failed:\n" + "\n".join(errors))
    logger.success("All {} audio tracks done for {}.", len(results), episode_tag)
    return results
