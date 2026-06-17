"""
core/tts_engine.py — 动态情感语音合成器 (Dynamic Emotional TTS Engine)

优化记录 (awesome-gpt-image-2 分析 + voice-pro 分析):
  P0: 修复 cosyvoice-v3.5-plus 的 instruction 情感参数未传入 Bug
  P1: 新增 F5-TTS 本地推理零样本声音克隆作为备选引擎
  P3: 用 DeepSeek LLM 替换硬编码的 TextSanitizer 字符串替换逻辑
"""

import re
import os
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


# ─────────────────────────────────────────────────────────────────────────────
# P3 优化：LLM 驱动的台词情感重写器
# 来源：voice-pro 分析 — 替换硬编码字符串匹配，改用语义理解
# ─────────────────────────────────────────────────────────────────────────────
class LLMTextSanitizer:
    """
    使用 DeepSeek LLM 对台词进行情感级别的语义重写。
    相比硬编码的字符串替换，能适应任意台词内容而不会失效。
    """

    _llm_cache: dict = {}  # 避免对同一段台词重复调用 LLM

    @staticmethod
    def sanitize(text: str, emotion: str = "", role: str = "") -> str:
        """
        对台词进行清洗和情感重写。
        优先使用 LLM 重写，LLM 不可用时退回到正则清洗。
        """
        # 1. 基础清洗：剔除括号内的动作描写（如"（惊恐喘气）"）防止被读出来
        clean_text = re.sub(r'（[^）]+）|\([^)]+\)|\[[^\]]+\]', '', text)
        clean_text = re.sub(r'<[^>]+>', '', clean_text)
        clean_text = clean_text.strip()

        if not clean_text:
            return ""

        # 2. 尝试 LLM 驱动的情感重写
        cache_key = f"{emotion}|{role}|{clean_text}"
        if cache_key in LLMTextSanitizer._llm_cache:
            return LLMTextSanitizer._llm_cache[cache_key]

        try:
            rewritten = LLMTextSanitizer._rewrite_with_llm(clean_text, emotion, role)
            LLMTextSanitizer._llm_cache[cache_key] = rewritten
            logger.info(f"[TextSanitizer] LLM 重写完成: '{clean_text}' → '{rewritten}'")
            return rewritten
        except Exception as e:
            logger.warning(f"[TextSanitizer] LLM 重写失败，退回规则处理: {e}")
            return LLMTextSanitizer._rule_based_fallback(clean_text, emotion, role)

    @staticmethod
    def _rewrite_with_llm(text: str, emotion: str, role: str) -> str:
        """调用 DeepSeek 对台词进行情感层面的重写。"""
        from openai import OpenAI

        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not deepseek_key or deepseek_key.startswith("sk-xxx"):
            raise RuntimeError("DEEPSEEK_API_KEY not configured")

        client = OpenAI(
            api_key=deepseek_key,
            base_url="https://api.deepseek.com"
        )

        is_clone = role and "克隆" in role

        if is_clone:
            system_prompt = (
                "你是一个专业的影视配音台词编辑。\n"
                "任务：将输入台词改写成适合「机器人/克隆体」风格的版本。\n"
                "规则：\n"
                "1. 在句子中间插入不自然的「等停顿（用……表示）」\n"
                "2. 句尾用句号而不是感叹号，保持冰冷\n"
                "3. 不要改变台词的实际含义\n"
                "4. 只返回改写后的台词，不要加任何解释\n"
                "5. 保留原文语言（中文原文返回中文）"
            )
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            system_prompt = (
                "你是一个专业的影视配音台词编辑。\n"
                "任务：将输入台词改写成适合「极度恐惧、颤抖」状态的版本。\n"
                "规则：\n"
                "1. 在词语之间插入结巴效果，如「我、我」「别，别」「到底……」\n"
                "2. 用省略号「……」表示恐惧导致的断气和吞字\n"
                "3. 关键词可以重复一次增强紧张感\n"
                "4. 不要改变台词的实际含义，不要加戏\n"
                "5. 只返回改写后的台词，不要加任何解释\n"
                "6. 保留原文语言"
            )
        else:
            # 普通情绪：只做清洗，不做重写
            return text

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            temperature=0.3,
            max_tokens=200
        )
        return response.choices[0].message.content.strip()

    @staticmethod
    def _rule_based_fallback(text: str, emotion: str, role: str) -> str:
        """LLM 不可用时的规则兜底（保留原有逻辑，但不写死具体词语）"""
        is_clone = "克隆" in (role or "")

        if is_clone:
            text = text.replace("。", "。 ").replace("，", " ")
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            # 通用的恐惧处理：在句首加结巴，在逗号处加省略号
            text = re.sub(r'([，,])\s*', r'……\1', text)
            text = re.sub(r'^([\u4e00-\u9fff])', r'\1……\1，', text)

        return text.strip()


# 向后兼容别名
TextSanitizer = LLMTextSanitizer


# ─────────────────────────────────────────────────────────────────────────────
# P4 优化：Chatterbox Turbo 情感标签引擎 (Phase 2.3)
# ─────────────────────────────────────────────────────────────────────────────
class ChatterboxTTSEngine:
    EMOTION_TAG_MAP = {
        "fearful": "[gasp]", "terrified": "[gasp]", "shocked": "[gasp]",
        "nervous": "[sigh]", "sad": "[sigh]", "disgusted": "[groan]",
    }

    def is_available(self) -> bool:
        try:
            import chatterbox
            return True
        except ImportError:
            return False

    def generate(self, role: str, emotion: str, text: str, output_path: Path) -> Path:
        if not self.is_available():
            raise AudioGenError("Chatterbox not installed")

        from chatterbox.tts import ChatterboxTTS
        if not hasattr(self.__class__, '_model'):
            logger.info("[Chatterbox] Loading model to CPU...")
            self.__class__._model = ChatterboxTTS.from_pretrained(device="cpu")

        tag = self.EMOTION_TAG_MAP.get(emotion, "")
        final_text = f"{tag} {text}" if tag else text
        
        logger.info(f"[Chatterbox] Generating for {role} ({emotion}): {final_text}")
        wav = self.__class__._model.generate(final_text)
        
        import soundfile as sf
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), wav.squeeze(0).numpy(), self.__class__._model.sr)
        return output_path


# ─────────────────────────────────────────────────────────────────────────────
# P1 优化：F5-TTS 本地零样本声音克隆引擎
class F5TTSEngine:
    """
    本地零样本声音克隆引擎，基于 F5-TTS。
    优点：完全免费，无 API 调用，3 秒参考音频即可克隆任意声线。
    前置条件：pip install f5-tts
    参考音频目录：./local_models/voice_references/
    """

    REF_AUDIO_DIR = Path("./local_models/voice_references")

    def __init__(self):
        self._check_available()

    def _check_available(self) -> bool:
        """检查 f5-tts 是否已安装"""
        try:
            import importlib
            importlib.import_module("f5_tts")
            return True
        except ImportError:
            logger.warning("[F5-TTS] f5-tts 未安装。请运行: pip install f5-tts")
            return False

    def is_available(self) -> bool:
        try:
            import importlib
            importlib.import_module("f5_tts")
            return True
        except ImportError:
            return False

    def generate(self, role: str, emotion: str, text: str, output_path: Path) -> Path:
        """
        使用 F5-TTS 进行零样本声音克隆。
        自动根据 role 查找对应的参考音频文件。
        """
        if not self.is_available():
            raise AudioGenError("F5-TTS 未安装，请先运行 pip install f5-tts")

        # 查找角色对应的参考音频
        ref_audio, ref_text = self._resolve_ref_audio(role, emotion)
        
        # ── P2: 使用 Demucs 提取纯净人声，去除参考音频的底噪/BGM ──
        try:
            from core.demucs_utils import isolate_vocals
            ref_audio = isolate_vocals(ref_audio)
        except Exception as e:
            logger.warning(f"[Demucs] 人声分离失败，使用原始参考音频: {e}")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[F5-TTS] 生成 {role}({emotion}): {text[:50]}...")

        import subprocess
        import sys

        cmd = [
            sys.executable, "-m", "f5_tts.infer.infer_cli",
            "--model", "F5TTS_v1_Base",
            "--ref_audio", str(ref_audio),
            "--ref_text", ref_text,
            "--gen_text", text,
            "--output_file", str(output_path),
            "--remove_silence",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise AudioGenError(f"F5-TTS 推理失败: {result.stderr[:300]}")
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise AudioGenError("F5-TTS 输出文件为空")
            logger.success(f"[F5-TTS] 完成: {output_path}")
            return output_path
        except subprocess.TimeoutExpired:
            raise AudioGenError("F5-TTS 推理超时（>120s）")

    def _resolve_ref_audio(self, role: str, emotion: str) -> tuple[Path, str]:
        """
        根据角色和情绪查找对应的参考音频。
        优先级：role_emotion.wav > role.wav > default_terrified.wav > default.wav
        """
        is_clone = role and "克隆" in role

        candidates = []
        if is_clone:
            candidates = [
                (self.REF_AUDIO_DIR / "robotic_female.wav", "你只是我的备用零件。"),
                (self.REF_AUDIO_DIR / "default.wav", "你好，我是林悦。"),
            ]
        elif emotion in ("fearful", "terrified", "shocked"):
            candidates = [
                (self.REF_AUDIO_DIR / "terrified_female_v3.wav", "别……别过来！那是什么？"),
                (self.REF_AUDIO_DIR / "default.wav", "你好，我是林悦。"),
            ]
        else:
            candidates = [
                (self.REF_AUDIO_DIR / "default.wav", "你好，我是林悦医生。"),
            ]

        for ref_audio, ref_text in candidates:
            if ref_audio.exists() and ref_audio.stat().st_size > 1024:
                return ref_audio, ref_text

        raise AudioGenError(
            f"找不到可用的参考音频文件。请在 {self.REF_AUDIO_DIR} 下放置参考音频。\n"
            "所需文件：terrified_female_v3.wav / robotic_female.wav / default.wav"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 主 TTS 引擎（DashScope CosyVoice，带 P0 修复 + F5-TTS 降级）
# ─────────────────────────────────────────────────────────────────────────────
class DynamicTTSEngine:
    def __init__(self, theme_key: str = "hospital_horror"):
        if not DASHSCOPE_API_KEY:
            raise AudioGenError("DASHSCOPE_API_KEY is not set.")
        dashscope.api_key = DASHSCOPE_API_KEY
        self.model = "cosyvoice-v1"
        self.theme_key = theme_key
        self.theme_config = THEMES.get(theme_key, THEMES.get("hospital_horror", {}))

        # P1: 初始化备选引擎
        self._f5tts = F5TTSEngine()
        self._chatterbox = ChatterboxTTSEngine()
        use_f5 = os.getenv("USE_F5TTS", "false").lower() == "true"
        self._use_f5tts_primary = use_f5 and self._f5tts.is_available()
        if self._use_f5tts_primary:
            logger.info("[TTS] F5-TTS 已激活作为主引擎（本地零样本克隆）")
        else:
            logger.info("[TTS] 使用 DashScope CosyVoice 作为主引擎")

    def _split_text_into_chunks(self, text: str, max_chars: int = 80) -> list[str]:
        """按标点符号分块，防止过长文本被 API 截断"""
        chunks = re.split(r'(?<=[。！？…])', text)
        merged, current = [], ""
        for chunk in chunks:
            if len(current) + len(chunk) <= max_chars:
                current += chunk
            else:
                if current: merged.append(current.strip())
                current = chunk
        if current.strip(): merged.append(current.strip())
        return [c for c in merged if c]

    def _concat_audio_chunks(self, chunk_paths: list[Path], output_path: Path) -> Path:
        """使用 FFmpeg 交叉淡入淡出拼接音频块"""
        if not chunk_paths:
            raise AudioGenError("No chunks to concatenate")
        if len(chunk_paths) == 1:
            import shutil
            shutil.copy(chunk_paths[0], output_path)
            return output_path

        inputs = sum([["-i", str(p)] for p in chunk_paths], [])
        filt = "".join(f"[{i}:a]" for i in range(len(chunk_paths)))
        filt += f"concat=n={len(chunk_paths)}:v=0:a=1[out]"
        
        import subprocess
        subprocess.run(["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filt, "-map", "[out]", "-ar", "48000", str(output_path)
        ], capture_output=True, check=True)
        return output_path

    def _map_voice(self, role: str, emotion: str) -> str:
        CUSTOM_TERRIFIED_VOICE = os.getenv("DASHSCOPE_VOICE_TERRIFIED", "longxiaochun")
        CUSTOM_ROBOTIC_VOICE = os.getenv("DASHSCOPE_VOICE_ROBOTIC", "longxiaochun")

        is_clone = role and "克隆" in role
        voice_map = self.theme_config.get("voice_map", {})

        if not role or role in ("旁白", "Narrator"):
            return voice_map.get("旁白", "longlaotie")
        elif is_clone:
            return CUSTOM_ROBOTIC_VOICE
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            return CUSTOM_TERRIFIED_VOICE
        elif role in voice_map:
            # 按角色名在主题 voice_map 精确匹配 → 团团/林溪拿到各自的反差 casting
            return voice_map[role]
        else:
            return self.theme_config.get("voice_id", "longxiaochun")

    def _build_instruction(self, role: str, emotion: str) -> tuple[str, float]:
        is_clone = role and "克隆" in role
        speech_rate = 1.0  # Normal speed

        if is_clone:
            instruct = "极其冰冷无机质，语速极度缓慢，像机器一样毫无波澜，语调单一，没有任何情绪起伏。"
            speech_rate = 0.85
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            instruct = "极度恐惧，声音颤抖，带有强烈的哭腔，气息急促，像是在极度惊恐中说话。"
            speech_rate = 1.15
        elif emotion == "cold" or emotion == "monotone":
            instruct = "冷漠平静，没有任何情绪，语气淡漠克制。"
            speech_rate = 0.95
        else:
            instruct = "身处诡异的环境中，声音自然但带有一丝警惕和不安。"

        return instruct, speech_rate

    def generate(self, role: str, emotion: str, raw_text: str, output_path: Path) -> Path:
        """生成单条情感语音。引入自动分块和 4 引擎降级链。"""

        # P3: LLM 驱动的台词情感重写
        clean_text = LLMTextSanitizer.sanitize(raw_text, emotion, role)
        if not clean_text:
            logger.warning(f"[TTS] 清洗后台词为空，跳过 Scene: {raw_text}")
            return output_path

        # 文本分块
        text_chunks = self._split_text_into_chunks(clean_text)
        chunk_paths = []

        import tempfile
        tmp_dir = Path(tempfile.gettempdir()) / "tts_chunks"
        tmp_dir.mkdir(exist_ok=True)

        for i, chunk_text in enumerate(text_chunks):
            chunk_out = tmp_dir / f"{output_path.stem}_chunk_{i}.wav"
            
            # P1: 如果配置了 F5-TTS 作为主引擎，优先使用
            if self._use_f5tts_primary:
                try:
                    chunk_paths.append(self._f5tts.generate(role, emotion, chunk_text, chunk_out))
                    continue
                except Exception as e:
                    logger.warning(f"[TTS] F5-TTS 失败，降级至 4引擎降级链: {e}")

            # 4引擎降级链: DashScope -> Chatterbox -> F5-TTS -> Kokoro
            success = False
            
            # 1. DashScope CosyVoice (云端最佳)
            try:
                chunk_paths.append(self._generate_dashscope(role, emotion, chunk_text, chunk_out))
                success = True
            except Exception as e:
                logger.warning(f"[TTS] DashScope 失败: {e}. 尝试降级 Chatterbox...")

            # 2. Chatterbox Turbo (本地情感)
            if not success and self._chatterbox.is_available():
                try:
                    chunk_paths.append(self._chatterbox.generate(role, emotion, chunk_text, chunk_out))
                    success = True
                except Exception as e:
                    logger.warning(f"[TTS] Chatterbox 失败: {e}. 尝试降级 F5-TTS...")

            # 3. F5-TTS (本地零样本)
            if not success and self._f5tts.is_available() and not self._use_f5tts_primary:
                try:
                    chunk_paths.append(self._f5tts.generate(role, emotion, chunk_text, chunk_out))
                    success = True
                except Exception as e:
                    logger.warning(f"[TTS] F5-TTS 失败: {e}. 尝试降级 Kokoro...")

            # 4. Kokoro (终极本地兜底)
            if not success:
                try:
                    chunk_paths.append(self._generate_kokoro(role, emotion, chunk_text, chunk_out))
                except Exception as e:
                    logger.error(f"[TTS] Kokoro 失败，彻底崩溃: {e}")
                    raise AudioGenError(f"All TTS engines failed for text: {chunk_text}")

        # 拼接块
        return self._concat_audio_chunks(chunk_paths, output_path)

    def _generate_kokoro(self, role: str, emotion: str, clean_text: str, output_path: Path) -> Path:
        """本地离线高拟真 TTS 兜底引擎 (Kokoro 82M)"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import soundfile as sf
            from kokoro import KPipeline
        except ImportError:
            logger.error("[TTS] Kokoro 未安装，无法生成语音。请运行: pip install kokoro 'misaki[zh]' soundfile")
            raise AudioGenError("Kokoro TTS fallback not installed")

        # 初始化管道（注意单例缓存，避免重复加载模型）
        if not hasattr(self.__class__, '_kokoro_pipeline'):
            logger.info("[TTS] 首次初始化 Kokoro 本地模型...")
            # 自动加载支持中文的管道
            self.__class__._kokoro_pipeline = KPipeline(lang_code='z')

        pipeline = self.__class__._kokoro_pipeline
        
        # 音色映射 (Kokoro 中文音色库)
        # zf_xiaobei: 甜美女声 / zm_yunjian: 成熟男声
        voice = 'zf_xiaobei'
        is_clone = role and "克隆" in role
        
        if role in ("旁白", "主角", "警察", "神秘人", "反派", "医生"):
            voice = 'zm_yunjian'
            
        speed = 1.0
        if is_clone:
            speed = 0.95
        elif emotion in ("fearful", "nervous", "shocked", "terrified"):
            speed = 1.1
            
        logger.info(f"[TTS] (Kokoro) Generating for {role} ({emotion}): {clean_text} via {voice}")
        
        # 替换全角省略号，避免 Kokoro 出现断气或奇怪的长顿挫
        safe_text = clean_text.replace("……", "，").replace("...", "，")
        
        # 生成语音
        generator = pipeline(
            safe_text, voice=voice, speed=speed, split_pattern=r'\n+'
        )
        
        audio_chunks = []
        sample_rate = 24000
        for i, (gs, ps, audio) in enumerate(generator):
            if audio is not None:
                audio_chunks.append(audio)
                
        if not audio_chunks:
            raise AudioGenError("Kokoro generated empty audio.")
            
        import numpy as np
        final_audio = np.concatenate(audio_chunks)
        sf.write(str(output_path), final_audio, sample_rate)
        
        return output_path

    def _generate_dashscope(self, role: str, emotion: str, clean_text: str, output_path: Path) -> Path:
        """DashScope CosyVoice 生成逻辑（含 P0 修复）"""
        vid = self._map_voice(role, emotion)
        instruct, speed = self._build_instruction(role, emotion)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[TTS] Generating for {role} ({emotion}): {clean_text} via VoiceID: {vid} (speed: {speed})")

        # 致命修复：根据 VoiceID 前缀动态推断底模
        model_name = "cosyvoice-v1"
        if vid.startswith("cosyvoice-v3.5-plus"):
            model_name = "cosyvoice-v3.5-plus"
        elif vid.startswith("cosyvoice-v3.5"):
            model_name = "cosyvoice-v3.5"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                cb = FileCallback(output_path)

                # ─────────────────────────────────────────────────────────────
                # P0 修复：cosyvoice-v3.5-plus 支持 instruction 情感参数
                # 原来这一行被注释掉了，导致所有情感指令无效！
                # v1 模型的 instruction 会导致 418 错误，所以按模型版本分支处理
                # ─────────────────────────────────────────────────────────────
                if model_name == "cosyvoice-v3.5-plus":
                    synthesizer = SpeechSynthesizer(
                        model=model_name,
                        voice=vid,
                        speech_rate=speed,
                        instruction=instruct,  # ← P0 修复核心：恢复情感指令！
                        callback=cb
                    )
                    logger.debug(f"[TTS] instruction 已启用 (v3.5-plus): {instruct}")
                else:
                    # v1 模型不支持 instruction，退回不带情感参数的调用
                    synthesizer = SpeechSynthesizer(
                        model=model_name,
                        voice=vid,
                        speech_rate=speed,
                        callback=cb
                    )

                synthesizer.call(clean_text)
                cb.wait(timeout=45)

                if cb.error_msg:
                    raise AudioGenError(f"DashScope Error: {cb.error_msg}")

                if not output_path.exists() or output_path.stat().st_size == 0:
                    raise AudioGenError("Generated file is 0 bytes. Engine failed internally without explicit error.")

                return output_path

            except Exception as e:
                logger.warning(f"[TTS] Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt == max_retries - 1:
                    raise AudioGenError(f"TTS generation completely failed after {max_retries} retries: {e}")
                import time
                time.sleep(3)

        return output_path
