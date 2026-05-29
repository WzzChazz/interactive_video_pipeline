import sys
import time
import subprocess
from pathlib import Path
from loguru import logger
from core.tts_engine import DynamicTTSEngine

def apply_dsp(input_wav: Path, output_wav: Path, effect_type: str):
    """使用 FFmpeg 对生成的 AI 语音进行后期的数字信号处理 (DSP) 以强行赋予情感张力"""
    if effect_type == "terrified":
        # Vibrato: 模拟恐惧时的声带颤抖 (频率 8Hz，深度 0.4 极度颤抖)
        # Tremolo: 模拟气虚和呼吸不稳的音量颤抖
        filter_str = "vibrato=f=7.0:d=0.4,tremolo=f=4.0:d=0.6"
    elif effect_type == "robotic":
        # 机械音效：
        # 1. 稍微压低音调，放慢语速
        # 2. Chorus 叠加产生金属冷酷感 (短延迟多重混响)
        filter_str = "atempo=0.85,chorus=0.6:0.9:50|60:0.4|0.32:0.25|0.4:2|2.3"
    else:
        filter_str = "anull"

    cmd = [
        "ffmpeg", "-y", "-i", str(input_wav),
        "-af", filter_str,
        str(output_wav)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def run_test():
    tts = DynamicTTSEngine()
    
    desktop = Path("/Users/mac/Desktop")
    human_raw = desktop / "raw_human.wav"
    clone_raw = desktop / "raw_clone.wav"
    
    human_out = desktop / "DSP_HUMAN_TERRIFIED.wav"
    clone_out = desktop / "DSP_CLONE_ROBOTIC.wav"
    
    text_human = "这...这到底是什么东西？！别过来！"
    logger.info("Generating raw human voice...")
    tts.generate("林悦", "terrified", text_human, human_raw)
    
    logger.info("Applying DSP Terror filters...")
    apply_dsp(human_raw, human_out, "terrified")
    logger.success(f"Extremely Terrified Human saved to: {human_out}")

    text_clone = "你只是我的备用零件。不需要害怕。"
    logger.info("Generating raw clone voice...")
    tts.generate("林悦（克隆）", "cold", text_clone, clone_raw)
    
    logger.info("Applying DSP Robotic filters...")
    apply_dsp(clone_raw, clone_out, "robotic")
    logger.success(f"Robotic Clone saved to: {clone_out}")

if __name__ == "__main__":
    run_test()
