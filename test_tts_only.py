import sys
import time
from pathlib import Path
from loguru import logger
from core.tts_engine import DynamicTTSEngine

def run_test():
    tts = DynamicTTSEngine()
    desktop = Path("/Users/mac/Desktop")
    human_out = desktop / "test_human_terrified.mp3"
    clone_out = desktop / "test_clone_robotic.mp3"
    
    # 模拟从大模型接过来的带动作描写的原始文本
    text_human = "这到底是什么东西（惊恐大口喘气）<break time=\"1000ms\"/>别过来！"
    logger.info("Testing Human (Fearful)...")
    try:
        tts.generate("林悦", "terrified", text_human, human_out)
        logger.success(f"Human audio generated: {human_out}")
    except Exception as e:
        logger.error(f"Human audio failed: {e}")

    # 防止阿里云并发限制拦截，休息5秒
    import time
    time.sleep(5)

    # 模拟克隆人的原始文本
    text_clone = "你只是我的备用零件，不需要害怕！"
    logger.info("Testing Clone (Cold/Monotone)...")
    try:
        tts.generate("林悦（克隆）", "cold", text_clone, clone_out)
        logger.success(f"Clone audio generated: {clone_out}")
    except Exception as e:
        logger.error(f"Clone audio failed: {e}")

if __name__ == "__main__":
    run_test()
