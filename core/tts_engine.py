"""
core/tts_engine.py — 动态情感语音合成器 (Dynamic Emotional TTS Engine)
"""

import re
from pathlib import Path
from loguru import logger
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback

from config.settings import DASHSCOPE_API_KEY
from config.themes import THEMES

class AudioGenError(Exception):
    pass

class FileCallback(ResultCallback):
    def __init__(self, file_path: Path):
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


class TextSanitizer:
    """全面升级：不再返回纯文本，而是直接输出带有物理控制参数的 SSML 标记语言"""
    @staticmethod
    @staticmethod
    def sanitize(raw_text: str, emotion: str = "neutral", role: str = "") -> str:
        # 1. 基础清洗（坚决洗掉括号里的动作描写）
        clean_text = re.sub(r'^[^:：]+[:：]\s*', '', raw_text)
        clean_text = re.sub(r'（[^）]+）|\([^)]+\)|\[[^\]]+\]', '', clean_text)
        clean_text = clean_text.replace('“', '').replace('”', '').strip()
        
        is_clone = role and "克隆" in role
        
        # 2. 构建纯文本（因为 cosyvoice-v1 不支持 prosody 标签）
        if is_clone or emotion == "cold":
            # 克隆体：强制断句为句号
            clean_text = re.sub(r'[，、！？]', '。', clean_text)
            clean_text = re.sub(r'<break[^>]*>', '。', clean_text)
            
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            # 人类恐惧主角：制造气虚结巴感
            clean_text = re.sub(r'(^|[。！？\?!])\s*([\u4e00-\u9fff])', r'\1 \2……\2', clean_text)
            clean_text = clean_text.replace("！", "……")
            
        return clean_text

class DynamicTTSEngine:
    def __init__(self):
        if not DASHSCOPE_API_KEY:
            raise AudioGenError("DASHSCOPE_API_KEY is not set.")
        dashscope.api_key = DASHSCOPE_API_KEY
        self.model = "cosyvoice-v1"
        self.theme_config = THEMES.get("hospital_horror", {})
        
    def _map_voice(self, role: str, emotion: str) -> str:
        CUSTOM_TERRIFIED_VOICE = "longxiaochun" 
        CUSTOM_ROBOTIC_VOICE = "longxiaochun"   
        
        is_clone = role and "克隆" in role
        
        if not role or role in ("旁白", "Narrator"):
            return self.theme_config.get("voice_map", {}).get("旁白", "longlaotie")
        elif is_clone:
            return CUSTOM_ROBOTIC_VOICE
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            return CUSTOM_TERRIFIED_VOICE
        else:
            return self.theme_config.get("voice_id", "longxiaochun")

    def _build_instruction(self, role: str, emotion: str) -> tuple[str, float]:
        is_clone = role and "克隆" in role
        speech_rate = 0.85
        
        if is_clone:
            instruct = "极其冰冷无机质，像机器一样毫无波澜。"
            speech_rate = 0.6
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            instruct = "极度恐怖，带有强烈的哭腔和发抖。"
            speech_rate = 1.15
        else:
            instruct = "身处诡异的环境中，自然但警惕。"
            
        return instruct, speech_rate

    def generate(self, role: str, emotion: str, raw_text: str, output_path: Path) -> Path:
        """生成单条情感语音"""
        clean_text = TextSanitizer.sanitize(raw_text, emotion, role)
        vid = self._map_voice(role, emotion)
        instruct, speed = self._build_instruction(role, emotion)
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cb = FileCallback(output_path)
        
        logger.info(f"[TTS] Generating for {role} ({emotion}): {clean_text} via VoiceID: {vid} (speed: {speed})")
        
        try:
            synthesizer = SpeechSynthesizer(
                model=self.model,
                voice=vid,
                instruction=instruct,
                speech_rate=speed,
                callback=cb
            )
            synthesizer.call(clean_text)
            
            if cb.error_msg:
                raise AudioGenError(f"DashScope Error: {cb.error_msg}")
        except Exception as e:
            raise AudioGenError(f"TTS generation failed: {e}")
            
        return output_path
