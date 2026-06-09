"""
core/audio_gen.py — ElevenLabs TTS + SFX 生成模块
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()  # Ensure .env is loaded into os.environ for os.getenv calls below

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
    # ── 当前 edge-tts 可用的 zh-CN 声音（2025年版）──────────────────────
    # zh-CN-XiaoxiaoNeural  Female  Warm       （主角女声）
    # zh-CN-XiaoyiNeural    Female  Lively     （克隆体 / 通用女声）
    # zh-CN-YunjianNeural   Male    Passion    （警察 / 反派）
    # zh-CN-YunxiNeural     Male    Lively     （旁白 / 主角男）
    # zh-CN-YunxiaNeural    Male    Cute       （男配角）
    # zh-CN-YunyangNeural   Male    Professional（医生 / 严肃角色）
    
    # 小剧固定角色（女）
    "林悦（克隆）": "zh-CN-XiaoyiNeural",   # 克隆体：XiaoyiNeural + 极低语速/音调 = 冷漠机械感
    "林悦":         "zh-CN-XiaoxiaoNeural",  # 女主角：温暖女声
    "克隆":         "zh-CN-XiaoyiNeural",    # 通用克隆体
    "护士":         "zh-CN-XiaoxiaoNeural",  # 甜美护士
    "女人":         "zh-CN-XiaoyiNeural",    # 通用女声
    "她":           "zh-CN-XiaoxiaoNeural",
    "女":           "zh-CN-XiaoxiaoNeural",
    "小女孩":       "zh-CN-XiaoyiNeural",    # 活泼女童
    "助手":         "zh-CN-XiaoxiaoNeural",  # 助理女声
    # 小剧固定角色（男）
    "旁白":         "zh-CN-YunxiNeural",     # 男声旁白，稳重阳光
    "主角":         "zh-CN-YunxiNeural",
    "医生":         "zh-CN-YunyangNeural",   # 专业严肃男声
    "警察":         "zh-CN-YunjianNeural",   # 低沉激情男声
    "神秘人":       "zh-CN-YunjianNeural",   # 借用低沉男声
    "反派":         "zh-CN-YunjianNeural",
    "DEFAULT":      "zh-CN-XiaoxiaoNeural"   # 默认女声
}

def _get_voice_for_speaker(speaker: str) -> str:
    """动态多角色声纹路由"""
    if not speaker:
        return VOICE_ROUTER["DEFAULT"]
    
    # 先尝试完整精确匹配（最高优先级）
    if speaker in VOICE_ROUTER:
        return VOICE_ROUTER[speaker]
    
    # 再按 key 长度从长到短做子串匹配，避免「林悦」比「克隆」先命中「林悦（克隆）」
    sorted_keys = sorted((k for k in VOICE_ROUTER if k != "DEFAULT"), key=len, reverse=True)
    for key in sorted_keys:
        if key in speaker:
            return VOICE_ROUTER[key]
            
    # 未知角色：用 hash 稳定分配声音，保证整部剧同一角色声音一致
    available_voices = list(set(v for k, v in VOICE_ROUTER.items() if k != "DEFAULT"))
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
    
    # === 新增：DashScope 自定义音色拦截逻辑 ===
    from config.settings import DASHSCOPE_API_KEY
    import os
    CUSTOM_TERRIFIED = os.getenv("DASHSCOPE_VOICE_TERRIFIED", "")
    CUSTOM_ROBOTIC = os.getenv("DASHSCOPE_VOICE_ROBOTIC", "")
    
    is_clone = speaker and "克隆" in speaker
    use_dashscope = False
    
    if is_clone and CUSTOM_ROBOTIC:
        use_dashscope = True
    elif emotion in ("fearful", "terrified", "panicked", "nervous", "shocked") and CUSTOM_TERRIFIED:
        use_dashscope = True
        
    if use_dashscope and DASHSCOPE_API_KEY:
        try:
            from core.tts_engine import DynamicTTSEngine
            engine = DynamicTTSEngine()
            # DynamicTTSEngine 的 generate 内部会调用 sanitize 并且处理生成
            engine.generate(role=speaker, emotion=emotion, raw_text=text, output_path=save_path)
            
            # 由于 DashScope 不直接生成 VTT，我们通过探测时长伪造一个包含整句的字幕文件
            dur_cmd = ["ffprobe", "-i", str(save_path), "-show_entries", "format=duration", "-v", "quiet", "-of", "csv=p=0"]
            dur_str = subprocess.check_output(dur_cmd).decode().strip()
            dur_sec = float(dur_str)
            
            def format_ts(seconds: float) -> str:
                h = int(seconds // 3600)
                m = int((seconds % 3600) // 60)
                s = int(seconds % 60)
                ms = int((seconds - int(seconds)) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
                
            vtt_content = f"WEBVTT\n\n00:00:00.000 --> {format_ts(dur_sec)}\n{text}\n"
            vtt_path = save_path.with_suffix(".vtt")
            vtt_path.write_text(vtt_content, encoding="utf-8")
            
            logger.debug(f"Voice (DashScope) and VTT saved: {save_path}")
            return save_path
        except Exception as e:
            logger.error(f"DashScope TTS failed: {e}. Falling back to Edge-TTS.")
            # 如果 DashScope 失败，优雅降级到 edge-tts 继续往下执行
            pass
            
    voice_id = _get_voice_for_speaker(speaker)
    vtt_path = save_path.with_suffix(".vtt")
    
    # ── 情绪 → rate/pitch 映射（直接用参数，彻底抛弃 SSML） ──────────────
    # edge-tts 无论是 --file CLI 还是 Python API Communicate()，
    # 都不会解析 <speak> SSML 标签，会把 XML 当纯文字朗读 → 产生英文噪音
    # 正确方案：用 Communicate(text, voice, rate=, pitch=) 的参数控制情绪节奏
    EMOTION_RATE_MAP = {
        "fearful":    ("+20%", "+5Hz"),
        "terrified":  ("+30%", "+10Hz"),
        "panicked":   ("+35%", "+8Hz"),
        "angry":      ("+10%", "+3Hz"),
        "determined": ("+5%",  "-2Hz"),
        "cold":       ("-15%", "-8Hz"),
        "sad":        ("-10%", "-5Hz"),
        "neutral":    ("-10%", "+0Hz"),
        "shocked":    ("+15%", "+5Hz"),
    }
    emo_rate, emo_pitch = EMOTION_RATE_MAP.get(emotion, ("-10%", "+0Hz"))
    
    # ── 用 edge-tts Python API 直接传纯文本 + 参数 ──────────────────────
    try:
        import asyncio
        import edge_tts
        
        async def _synthesize():
            communicate = edge_tts.Communicate(
                processed_text,
                voice_id,
                rate=emo_rate,
                pitch=emo_pitch,
            )
            await communicate.save(str(save_path))
            # 收集 WordBoundary 事件生成字幕
            sub_maker = edge_tts.SubMaker()
            async for chunk in edge_tts.Communicate(processed_text, voice_id, rate=emo_rate, pitch=emo_pitch).stream():
                if chunk["type"] == "WordBoundary":
                    sub_maker.feed(chunk)
            srt_content = sub_maker.get_srt()
            if srt_content.strip():
                vtt_path.write_text(srt_content, encoding="utf-8")
            else:
                # 兜底：写一个单条 SRT 字幕，时长与音频一致
                # 注意时间格式必须用逗号（SRT标准），_parse_time_to_seconds 才能正确解析
                vtt_path.write_text(f"1\n00:00:00,000 --> 00:00:09,900\n{text}\n\n", encoding="utf-8")
        
        asyncio.run(_synthesize())
        
    except Exception as e:
        # 降级：CLI --text 传纯文本（绝不用 --file 传 SSML）
        logger.warning(f"edge_tts Python API failed ({e}), falling back to CLI with --text")
        # 关键修复：负数参数（如 -10%）必须用 = 连接，否则 argparse 把它当另一个 flag
        cmd = [
            "python3", "-m", "edge_tts",
            "--text", processed_text,
            "--voice", voice_id,
            f"--rate={emo_rate}",    # 必须用 = ，避免 -10% 被 argparse 误识别为 flag
            f"--pitch={emo_pitch}",  # 同上
            "--write-media", str(save_path),
            "--write-subtitles", str(vtt_path)
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as ex:
            raise AudioGenError(f"Edge-TTS CLI failed: {ex.stderr}")
        

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
            "-i", "aevalsrc='random(0)*0.15*sin(2*PI*t*0.5) + sin(2*PI*t*80)*0.4'",
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


import redis
import json

def _publish_progress(episode_tag: str, step_name: str, pct: int):
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.publish("pipeline_progress", json.dumps({
            "active": True, "step_name": step_name, "step": 4, "total": 6, "pct": pct, "episode": episode_tag
        }))
    except:
        pass

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
