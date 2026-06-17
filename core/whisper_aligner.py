import os
import time
from pathlib import Path
from loguru import logger
from faster_whisper import WhisperModel

# 全局缓存模型，避免每次调用都加载
_WHISPER_MODEL = None

def _get_whisper_model() -> WhisperModel:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        logger.info("[Whisper] Loading faster-whisper model (small)...")
        # 如果是 Mac，可以使用 cpu 或者 coreml，这里默认 cpu + compute_type="int8"
        # 如果是 CUDA 环境，可以用 "cuda" + "float16"
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        
        try:
            _WHISPER_MODEL = WhisperModel("small", device=device, compute_type=compute_type)
            logger.success("[Whisper] Model loaded successfully.")
        except Exception as e:
            logger.error(f"[Whisper] Failed to load model: {e}")
            raise
    return _WHISPER_MODEL

def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 VTT 格式 00:00:00.000"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def generate_word_level_vtt(audio_path: str | Path, output_vtt_path: str | Path) -> str:
    """
    使用 faster-whisper 获取词级时间戳，并生成 WebVTT 文件。
    
    Args:
        audio_path: 待识别的音频文件路径
        output_vtt_path: 输出的 .vtt 文件路径
        
    Returns:
        保存的 vtt 文件路径
    """
    audio_path = Path(audio_path)
    output_vtt_path = Path(output_vtt_path)
    
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
    logger.debug(f"[Whisper] Starting word-level alignment for {audio_path.name}")
    start_time = time.time()
    
    model = _get_whisper_model()
    
    # 强制开启词级时间戳 word_timestamps=True
    segments, info = model.transcribe(str(audio_path), word_timestamps=True, language="zh")
    
    vtt_lines = ["WEBVTT\n"]
    
    for segment in segments:
        for word in segment.words:
            # 去除前后空格
            text = word.word.strip()
            if not text:
                continue
                
            start_ts = format_timestamp(word.start)
            end_ts = format_timestamp(word.end)
            
            vtt_lines.append(f"\n{start_ts} --> {end_ts}")
            vtt_lines.append(text)
            
    vtt_content = "\n".join(vtt_lines) + "\n"
    output_vtt_path.write_text(vtt_content, encoding="utf-8")
    
    elapsed = time.time() - start_time
    logger.info(f"[Whisper] Generated word-level VTT in {elapsed:.2f}s: {output_vtt_path.name}")
    
    return str(output_vtt_path)
