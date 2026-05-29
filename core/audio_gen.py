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


@retry(retry=retry_if_exception_type(AudioGenError),
       stop=stop_after_attempt(API_MAX_RETRIES),
       wait=wait_exponential(min=3, max=30), reraise=True)
def generate_voice(text: str, save_path: Path, emotion: str = "neutral",
                   speaker: str = "", theme_key: str = "hospital_horror") -> Path:
    """调用阿里通义 DashScope (CosyVoice) 生成配音 MP3。"""
    if not DASHSCOPE_API_KEY:
        raise AudioGenError("DASHSCOPE_API_KEY not configured.")
    
    from config.settings import DASHSCOPE_NARRATOR_VOICE_ID
    from config.themes import THEMES
    
    # 主题配音配置
    theme_config = THEMES.get(theme_key, THEMES["hospital_horror"])
    
    # 动态声音映射 (Voice Casting)
    if not speaker or speaker in ("旁白", "Narrator"):
        vid = theme_config.get("voice_map", {}).get("旁白", "longlaotie")
    elif speaker in ("系统", "System", "未知"):
        vid = theme_config.get("voice_map", {}).get("系统", "longxiaoxia")
    else:
        if "林悦" in speaker and "克隆" in speaker:
            vid = "longxiaoxia"
        elif "林悦" in speaker:
            vid = "longxiaochun"
        else:
            vid = theme_config.get("voice_map", {}).get(speaker)
            if not vid:
                vid = theme_config.get("voice_id", "longshuo")
    
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
    except ImportError:
        raise AudioGenError("dashscope package not installed. Run: pip install dashscope")
        
    dashscope.api_key = DASHSCOPE_API_KEY
    
    # 将大模型传来的简短情绪标签，扩展为 CosyVoice 强力支持的“丰富情境指导词 (Instruction)”
    # 这能够极其明显地改变配音的面部表情管理和语气节奏
    speech_rate = 0.85
    if not speaker or speaker in ("旁白", " Narrator"):
        instruction_str = "用充满悬疑感、极其压抑且低沉平缓的语气进行恐怖解说。"
    elif speaker in ("系统", "System"):
        instruction_str = "用冰冷、机械、毫无人类情感波动的语气下达指令。"
    else:
        # 检测克隆体角色：如果说话人名字包含"克隆"，强制使用冰冷机械音
        is_clone_character = speaker and ("克隆" in speaker)
        
        if is_clone_character or emotion == "clone":
            instruction_str = "毫无感情的克隆体，语气极其冰冷、机械、空洞，语速缓慢，令人毛骨悚然。像一个没有灵魂的人偶在说话。"
            speech_rate = 0.65
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            instruction_str = "处于极度恐怖且危险的环境中，语气压抑、带有一丝颤抖和强烈的恐惧感，仿佛在尽力压低声音，甚至带着快要哭出来的惊悚感。"
        elif emotion == "cold":
            instruction_str = "语气极其冰冷、毫无感情、令人毛骨悚然的平静，带着一种机械般的冷酷感和压迫感。"
        elif emotion == "angry":
            instruction_str = "情绪极其愤怒，声音提高，咬牙切齿地说话，充满攻击性。"
        elif emotion == "sad":
            instruction_str = "情绪非常悲伤，声音哽咽，充满绝望感和无力感。"
        elif emotion == "excited" or emotion == "happy":
            instruction_str = "情绪激动，说话急促，带有癫狂或者兴奋的语气。"
        elif emotion == "determined":
            instruction_str = "深呼吸，语气坚定果决，充满绝境求生的勇气。"
        else:
            instruction_str = "身处诡异的环境中，语气自然但带有一种本能的警惕和紧张感。"
    
    # 建立回调机制将音频流写入文件
    class FileCallback(ResultCallback):
        def __init__(self, file_path):
            self.file_path = file_path
            self.file = None
            self.error_msg = None

        def on_open(self):
            self.file = open(self.file_path, "wb")

        def on_data(self, data: bytes):
            if self.file:
                self.file.write(data)

        def on_complete(self):
            if self.file:
                self.file.close()

        def on_error(self, message: str):
            if self.file:
                self.file.close()
            self.error_msg = message
            
    save_path.parent.mkdir(parents=True, exist_ok=True)
    cb = FileCallback(save_path)
    
    # 修正模型：longxiaochun 等经典音色必须使用 cosyvoice-v1
    target_model = "cosyvoice-v1"
    # 过滤掉角色名和无用的引号，防止 TTS 把它读出来（例如把 "林悦：“谁在那儿？”" 变成 "谁在那儿？"）
    import re
    clean_text = re.sub(r'^[^:：]+[:：]\s*', '', text)
    clean_text = clean_text.replace('“', '').replace('”', '').replace('"', '').strip()
    
    try:
        synthesizer = SpeechSynthesizer(
            model=target_model, 
            voice=vid, 
            instruction=instruction_str,
            speech_rate=speech_rate, # 稍微放慢语速，增加悬疑感和压迫感
            callback=cb
        )
        synthesizer.call(clean_text)
        if cb.error_msg:
            raise AudioGenError(f"DashScope TTS Error: {cb.error_msg}")
    except Exception as e:
        raise AudioGenError(f"DashScope TTS failed: {e}") from e
        
    logger.debug("Voice saved: {}", save_path)
    return save_path


@retry(retry=retry_if_exception_type(AudioGenError),
       stop=stop_after_attempt(API_MAX_RETRIES),
       wait=wait_exponential(min=3, max=30), reraise=True)
def generate_sfx(prompt: str, save_path: Path,
                 duration_seconds: int = CLIP_DURATION_SECONDS) -> Path:
    from config.settings import USE_ELEVENLABS_SFX
    if not USE_ELEVENLABS_SFX:
        logger.info("ElevenLabs SFX is disabled via switch. Generating local atmospheric soundscape using FFmpeg lavfi.")
        import subprocess
        save_path.parent.mkdir(parents=True, exist_ok=True)
        # 生成令人不安的悬疑氛围音：包含极低频心跳/轰鸣 (40Hz) 和高频空旷白噪
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
            
    if not ELEVENLABS_API_KEY:
        raise AudioGenError("ELEVENLABS_API_KEY not configured.")
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
            error_msg = f"SFX generation FAILED due to API quota/auth (HTTP {e.response.status_code}). Stopping pipeline to save costs."
            logger.error(error_msg)
            raise AudioGenError(error_msg)
        raise AudioGenError(f"SFX request failed: {e}") from e
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
