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

    # 引入清洗工具，去掉括号里的动作描写如“(哭泣)”，否则 Edge-TTS 会直接读出“括号”
    from core.tts_engine import LLMTextSanitizer
    text = LLMTextSanitizer.sanitize(text, emotion, speaker)

    if not text:
        logger.warning(f"Text empty after sanitization for speaker {speaker}. Generating silence.")
        # 直接生成空白音频文件并返回
        save_path.parent.mkdir(parents=True, exist_ok=True)
        pad_cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "3.0", "-c:a", "libmp3lame", str(save_path)
        ]
        subprocess.run(pad_cmd, check=True, capture_output=True)
        # 伪造空 VTT
        vtt_path = save_path.with_suffix(".vtt")
        vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
        return save_path

    # SSML 悬疑断句解析：将 [pause:Xs] 替换为标点，或者直接用 SSML（这里用逗号/句号最简单有效）
    # edge-tts 在遇到多个句号时会自动拉长停顿
    processed_text = re.sub(r'\[pause:(\d+\.?\d*)s?\]', lambda m: "。" * max(1, int(float(m.group(1)) * 2)), text)
    processed_text = processed_text.replace("...", "。。。")
    
    # === 决定是否走云端高保真引擎 (DashScope) ===
    from config.settings import DASHSCOPE_API_KEY
    import os
    CUSTOM_TERRIFIED = os.getenv("DASHSCOPE_VOICE_TERRIFIED", "")
    CUSTOM_ROBOTIC = os.getenv("DASHSCOPE_VOICE_ROBOTIC", "")
    
    is_clone = speaker and "克隆" in speaker
    # 如果配置了阿里云 API Key，全量使用高质量的 DashScope，彻底抛弃本地廉价合成的怪异语调
    use_dashscope = bool(DASHSCOPE_API_KEY)
        
    from core.tts_engine import DynamicTTSEngine
    engine = DynamicTTSEngine(theme_key)

    try:
        if use_dashscope and DASHSCOPE_API_KEY:
            # 云端引擎，内部会自动进行 sanitize，失败会降级 Kokoro
            engine.generate(role=speaker, emotion=emotion, raw_text=text, output_path=save_path)
        else:
            # 本地免费、高拟真兜底引擎 (Kokoro 82M)
            # text 在上方已经 sanitize 过，直接传进去
            engine._generate_kokoro(role=speaker, emotion=emotion, clean_text=processed_text, output_path=save_path)
    except Exception as e:
        logger.error(f"TTS generation completely failed: {e}")
        # 如果彻底失败，生成静音兜底
        save_path.parent.mkdir(parents=True, exist_ok=True)
        pad_cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "3.0", "-c:a", "libmp3lame", str(save_path)
        ]
        subprocess.run(pad_cmd, check=True, capture_output=True)

    # === Pedalboard 音频后期处理 (Audio FX) ===
    try:
        from core.audio_post_processor import apply_audio_preset
        tmp_path = save_path.with_suffix('.post.mp3')
        apply_audio_preset(save_path, tmp_path, emotion, speaker)
        if tmp_path.exists():
            import shutil
            shutil.move(tmp_path, save_path)
    except Exception as e:
        logger.warning(f"[Audio FX] Post-processing failed, using raw audio: {e}")

    # 探测时长
    dur_cmd = ["ffprobe", "-i", str(save_path), "-show_entries", "format=duration", "-v", "quiet", "-of", "csv=p=0"]
    dur_str = subprocess.check_output(dur_cmd).decode().strip()
    dur_sec = float(dur_str)
    
    # ── P1-2: 使用 WhisperX 强制对齐逐词字幕 ──
    try:
        from core.whisper_aligner import generate_word_level_vtt
        generate_word_level_vtt(save_path, save_path.with_suffix(".vtt"))
    except Exception as e:
        logger.error(f"[Whisper] 字幕对齐失败，降级使用整句字幕: {e}")
        def format_ts(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
        vtt_content = f"WEBVTT\n\n00:00:00.000 --> {format_ts(dur_sec)}\n{text}\n"
        save_path.with_suffix(".vtt").write_text(vtt_content, encoding="utf-8")
        
    # ── P0-2: 短台词静音填充 (Silence Padding) ──
    if dur_sec < 3.0:
        logger.info(f"Audio is short ({dur_sec:.2f}s). Padding to 3.0s to ensure scene length.")
        padded_path = save_path.with_name(f"padded_{save_path.name}")
        pad_cmd = [
            "ffmpeg", "-y", "-i", str(save_path),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-filter_complex", "[0:a]apad=pad_dur=3.0[outa]",
            "-map", "[outa]", str(padded_path)
        ]
        subprocess.run(pad_cmd, check=True, capture_output=True)
        import shutil
        shutil.move(str(padded_path), str(save_path))

    logger.debug("Voice and VTT saved: {}", save_path)
    return save_path


@retry(retry=retry_if_exception_type(AudioGenError),
       stop=stop_after_attempt(API_MAX_RETRIES),
       wait=wait_exponential(min=3, max=30), reraise=True)
def generate_sfx(prompt: str, save_path: Path,
                 duration_seconds: int = CLIP_DURATION_SECONDS) -> Path:
    """生成单个分镜的点音效（Foley）。免费账号时作为兜底使用 FFmpeg 合成音效。"""
    from config.settings import USE_ELEVENLABS_SFX
    def _generate_fallback_sfx():
        # ── P1-1: 解决 SFX 兜底音频复用 ──
        # 利用提示词的 hash 改变合成参数，让不同音效有差异
        h = hash(prompt)
        base_freq = 40 + (h % 80)        # 40Hz 到 120Hz
        mod_freq = 0.1 + ((h % 10) / 10) # 0.1Hz 到 1.0Hz 的颤音
        noise_vol = 0.05 + ((h % 15) / 100) # 白噪声比例

        import subprocess
        save_path.parent.mkdir(parents=True, exist_ok=True)
        # 用不同的正弦波组合与噪声制作独特的低频氛围
        aeval_expr = f"random(0)*{noise_vol}*sin(2*PI*t*{mod_freq}) + sin(2*PI*t*{base_freq})*0.4"
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"aevalsrc='{aeval_expr}'",
            "-t", str(duration_seconds),
            "-c:a", "libmp3lame",
            str(save_path)
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.debug(f"Synthetic FFmpeg SFX saved: {save_path} (Freq: {base_freq}Hz)")
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
            json={
                "text": prompt,
                "duration_seconds": float(duration_seconds),
                "prompt_influence": 0.7,  # 提升至 0.7，让生成效果更贴近提示词
            },
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


# ─────────────────────────────────────────────────────────────────────────────
# 整集环境音生成（ElevenLabs 免费账号的最佳用法）
# 来源：voice-pro 分析 — ElevenLabs 免费账号最适合做整集统一的环境音底层
#
# 策略：
# 1. 一次性生成 40 秒高质量恐怖医院环境音底层
# 2. 缓存到 sfx_library/，整个项目只消耗 1 次 API 配额（而不是每个分镜消耗 1 次）
# 3. ffmpeg_compiler.py 的 _build_audio_track 会把它作为全局底层铺满整集
# ─────────────────────────────────────────────────────────────────────────────

# 按主题预设的高质量环境音提示词（精心设计，针对 ElevenLabs 音效引擎优化）
AMBIENT_PROMPTS = {
    "hospital_horror": (
        "Hospital archive room at 3am, deep sub-bass drone hum, "
        "distant hospital machinery beeping erratically, "
        "fluorescent light buzzing and flickering, "
        "occasional distant muffled footsteps on linoleum, "
        "the sound of old metal filing cabinet drawers being opened, "
        "eerie silence punctuated by creaking metal shelves, "
        "subtle distant crying that stops abruptly, "
        "low frequency infrasound pressure"
    ),
    "school_horror": (
        "Abandoned school hallway at night, wind through broken windows, "
        "lockers rattling, distant children's laughter echoing, "
        "fluorescent light buzzing, creaking floorboards"
    ),
    "default": (
        "Dark atmospheric horror ambience, low drone, "
        "distant unnerving sounds, tension building"
    ),
}

_SFX_LIBRARY_DIR = Path("./sfx_library")


def generate_episode_ambient_sfx(
    theme_key: str = "hospital_horror",
    duration_seconds: int = 40,
    force_regenerate: bool = False,
) -> Optional[Path]:
    """
    生成整集统一的环境音底层音轨（ElevenLabs 免费账号的最优使用方式）。

    - 第一次调用：请求 ElevenLabs API，生成 {duration_seconds} 秒高质量环境音
    - 后续调用：直接返回缓存路径，不再消耗 API 配额
    - 在 ffmpeg_compiler._build_audio_track 中作为全局底层铺满整集视频

    Returns:
        Path to ambient audio file，失败时返回 None（管线可以继续，只是没有环境音）
    """
    from config.settings import USE_ELEVENLABS_SFX

    _SFX_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _SFX_LIBRARY_DIR / f"ambient_{theme_key}_{duration_seconds}s.mp3"

    # 命中缓存：直接返回，节省 API 配额
    if cache_path.exists() and cache_path.stat().st_size > 10_000 and not force_regenerate:
        logger.info(f"[Ambient SFX] 命中缓存，跳过 API 调用: {cache_path}")
        return cache_path

    prompt = AMBIENT_PROMPTS.get(theme_key, AMBIENT_PROMPTS["default"])

    # ElevenLabs API 最大支持 22 秒，超过 22 秒需要分段拼接
    MAX_EL_DURATION = 22

    def _call_elevenlabs(dur: int, save_to: Path) -> bool:
        """调用 ElevenLabs 生成一段音效，返回是否成功"""
        if not USE_ELEVENLABS_SFX or not ELEVENLABS_API_KEY:
            return False
        try:
            resp = requests.post(
                f"{_EL_BASE}/sound-generation",
                json={
                    "text": prompt,
                    "duration_seconds": float(min(dur, MAX_EL_DURATION)),
                    "prompt_influence": 0.8,  # 高影响系数确保氛围贴合提示词
                },
                headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
                timeout=90,
            )
            resp.raise_for_status()
            save_to.parent.mkdir(parents=True, exist_ok=True)
            save_to.write_bytes(resp.content)
            logger.success(f"[Ambient SFX] ElevenLabs 片段生成成功: {save_to} ({dur}s)")
            return True
        except Exception as e:
            logger.warning(f"[Ambient SFX] ElevenLabs 调用失败: {e}")
            return False

    # ElevenLabs 最长 22 秒，超过则分段生成再用 FFmpeg 拼接
    import subprocess
    if duration_seconds <= MAX_EL_DURATION:
        success = _call_elevenlabs(duration_seconds, cache_path)
    else:
        # 分两段生成（22s + 余下部分）再拼接
        seg1 = _SFX_LIBRARY_DIR / f"ambient_{theme_key}_seg1.mp3"
        seg2 = _SFX_LIBRARY_DIR / f"ambient_{theme_key}_seg2.mp3"
        remaining = duration_seconds - MAX_EL_DURATION

        ok1 = _call_elevenlabs(MAX_EL_DURATION, seg1)
        ok2 = _call_elevenlabs(remaining, seg2)

        if ok1 and ok2:
            # FFmpeg 拼接两段
            concat_list = _SFX_LIBRARY_DIR / "ambient_concat.txt"
            concat_list.write_text(f"file '{seg1.absolute()}'\nfile '{seg2.absolute()}'\n")
            try:
                subprocess.run([
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    "-c:a", "libmp3lame",
                    "-b:a", "192k",
                    str(cache_path)
                ], check=True, capture_output=True)
                seg1.unlink(missing_ok=True)
                seg2.unlink(missing_ok=True)
                concat_list.unlink(missing_ok=True)
                success = True
            except subprocess.CalledProcessError as e:
                logger.error(f"[Ambient SFX] FFmpeg 拼接失败: {e}")
                success = False
        else:
            success = ok1  # 至少用第一段
            if ok1 and seg1.exists():
                import shutil
                shutil.copy(seg1, cache_path)

    if not success or not cache_path.exists():
        # 完全兜底：用 FFmpeg lavfi 生成合成环境音（无 API 消耗）
        logger.warning("[Ambient SFX] ElevenLabs 失败，使用 FFmpeg 合成恐怖医院环境音")
        try:
            # 合成：45Hz 心理压抑低频 + 白噪声 + 电磁蜂鸣
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i",
                (
                    "aevalsrc='"
                    "sin(2*PI*45*t)*0.3 +"           # 45Hz 心理低频
                    "random(0)*0.08 +"                # 白噪声
                    "sin(2*PI*60*t)*0.15*sin(t*0.3)" # 灯管蜂鸣
                    "'"
                ),
                "-t", str(duration_seconds),
                "-c:a", "libmp3lame",
                "-b:a", "128k",
                str(cache_path)
            ], check=True, capture_output=True)
            logger.info(f"[Ambient SFX] FFmpeg 合成环境音已保存: {cache_path}")
        except Exception as e:
            logger.error(f"[Ambient SFX] FFmpeg 合成也失败了: {e}")
            return None

    return cache_path


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
