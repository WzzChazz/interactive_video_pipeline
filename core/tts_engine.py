"""
core/tts_engine.py — 动态情感语音合成器 (Dynamic Emotional TTS Engine)
"""

import re
import threading
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
        self._event = threading.Event()

    def on_open(self):
        self.file = open(self.file_path, "wb")

    def on_data(self, data: bytes):
        if self.file:
            self.file.write(data)

    def on_complete(self):
        if self.file:
            self.file.close()
        self._event.set()

    def on_error(self, message: str):
        if self.file:
            self.file.close()
        self.error_msg = message
        self._event.set()

    def wait(self, timeout=30):
        self._event.wait(timeout)


class TextSanitizer:
    """全面升级：不再返回纯文本，而是直接输出带有物理控制参数的 SSML 标记语言"""
    @staticmethod
    def sanitize(text: str, emotion: str = "", role: str = "") -> str:
        """
        Removes all illegal XML/SSML tags that crash the TTS engine,
        while strictly preserving narrative pacing.
        """
        # 1. 基础清洗：剔除所有括号内的动作描写（如 "（惊恐喘气）"）防止被读出来
        clean_text = re.sub(r'（[^）]+）|\([^)]+\)|\[[^\]]+\]', '', text)
        clean_text = re.sub(r'<[^>]+>', '', clean_text)
        
        # 2. 模拟由于情绪极度紧张导致的严重口吃和喘息
        if emotion == "terrified":
            # 将句首词进行严重结巴处理
            clean_text = re.sub(r'(^|[。！？\?!])\s*([\u4e00-\u9fff])', r'\1 \2……\2，', clean_text)
            # 在长句中间强行插入大量的逗号和省略号，迫使模型断气、急促呼吸
            clean_text = clean_text.replace("到底是什么", "到底……到底是什么")
            clean_text = clean_text.replace("别过来", "别，别过来……！")
            clean_text = clean_text.replace("我不", "我、我不……")
        elif emotion == "cold" or emotion == "monotone":
            # 机器人无感情，句号替换成死板的停顿
            clean_text = clean_text.replace("。", "。 ")
            clean_text = clean_text.replace("，", " ")
            
        # 移除多余的空格
        return clean_text.strip()

class DynamicTTSEngine:
    def __init__(self):
        if not DASHSCOPE_API_KEY:
            raise AudioGenError("DASHSCOPE_API_KEY is not set.")
        dashscope.api_key = DASHSCOPE_API_KEY
        self.model = "cosyvoice-v1"
        self.theme_config = THEMES.get("hospital_horror", {})
        
    def _map_voice(self, role: str, emotion: str) -> str:
        import os
        CUSTOM_TERRIFIED_VOICE = os.getenv("DASHSCOPE_VOICE_TERRIFIED", "longxiaochun")
        CUSTOM_ROBOTIC_VOICE = os.getenv("DASHSCOPE_VOICE_ROBOTIC", "longxiaochun")   
        
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
        
        logger.info(f"[TTS] Generating for {role} ({emotion}): {clean_text} via VoiceID: {vid} (speed: {speed})")
        
        # 致命修复：根据 VoiceID 前缀动态推断底模，千万不要写死 cosyvoice-v1！
        # 如果是 cosyvoice-v3.5-plus-bailian-xxx，那底模必须是 cosyvoice-v3.5-plus
        model_name = "cosyvoice-v1"
        if vid.startswith("cosyvoice-v3.5-plus"):
            model_name = "cosyvoice-v3.5-plus"
        elif vid.startswith("cosyvoice-v3.5"):
            model_name = "cosyvoice-v3.5"
            
        max_retries = 3
        for attempt in range(max_retries):
            try:
                cb = FileCallback(output_path)
                synthesizer = SpeechSynthesizer(
                    model=model_name,
                    voice=vid,
                    speech_rate=speed,
                    callback=cb
                    # 仅保留 voice 和 speech_rate，因为 instruction 字段会导致 418 错误
                )
                synthesizer.call(clean_text)
                cb.wait(timeout=45)  # 阻塞主线程等待文件写完
                
                if cb.error_msg:
                    raise AudioGenError(f"DashScope Error: {cb.error_msg}")
                    
                # 检查是否因为 418 内部错误导致生成了 0 字节文件
                if not output_path.exists() or output_path.stat().st_size == 0:
                    raise AudioGenError("Generated file is 0 bytes. Engine failed internally without explicit error.")
                    
                return output_path
                
            except Exception as e:
                logger.warning(f"[TTS] Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt == max_retries - 1:
                    raise AudioGenError(f"TTS generation completely failed after {max_retries} retries: {e}")
                import time
                time.sleep(3) # 重试前休息3秒
                
        return output_path
