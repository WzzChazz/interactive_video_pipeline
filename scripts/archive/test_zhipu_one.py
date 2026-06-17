"""快速测试智谱 CogVideoX-Flash 一个镜头，看看质量"""
import os
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
from loguru import logger
from core.video_gen import _zhipu_generate

image_path = "storage/temp/S01E024/images/scene_03.jpg"  # 第3张：发现X-001档案特写
save_path  = Path("storage/temp/S01E024/clips/test_zhipu_scene03.mp4")

prompt = (
    "Extreme close-up shot, a terrified Chinese female doctor holding a flashlight, "
    "illuminating a yellowed folder labeled EXPERIMENTAL RECORDS X-001. "
    "Pitch black basement background, only flashlight beam visible. "
    "Slow camera push in, cinematic horror atmosphere, teal color grade."
)

logger.info("提交智谱 CogVideoX-Flash 任务...")
result = _zhipu_generate(image_path, save_path, prompt=prompt)
logger.success(f"完成！视频保存在: {result}")
