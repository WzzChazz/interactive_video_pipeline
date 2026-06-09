"""
config/settings.py
==================
全局配置中枢。所有 API Keys、路径、超时参数均从此处统一读取。
生产环境通过 .env 文件注入，本地开发直接填写默认值即可。
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# ── 项目根目录 ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), 
        env_file_encoding='utf-8', 
        extra='ignore'
    )

    # 1. 数据库
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'storage' / 'pipeline.db'}"

    # 2. LLM 配置
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-reasoner"

    # 3. 视觉生图
    FLUX_API_KEY: str = ""
    FLUX_API_URL: str = "https://api.siliconflow.cn/v1/images/generations"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    USE_LIPSYNC: bool = False
    MIDJOURNEY_API_KEY: str = ""

    # 4. 图生视频
    KLING_API_KEY: str = ""
    KLING_API_URL: str = "https://api-beijing.klingai.com/v1"
    RUNWAY_API_KEY: str = ""
    RUNWAY_API_URL: str = "https://api.dev.runwayml.com/v1"
    HAILUO_API_KEY: str = ""
    HAILUO_API_URL: str = "https://api.minimax.io/v1"
    ZHIPU_API_KEY: str = ""
    VIDEO_PROVIDER: str = "kling"  # "kling" | "runway" | "hailuo" | "zhipu"

    # 5. 音频合成
    USE_ELEVENLABS_SFX: bool = False
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = ""
    ELEVENLABS_MODEL_ID: str = "eleven_turbo_v2_5"
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_VOICE_ID: str = "cosyvoice-v3.5-plus-vd-bailian-f0f1b1bb3679400486ad031fc8bd2bed"
    DASHSCOPE_NARRATOR_VOICE_ID: str = "longlaotie"

    # 6. 浏览器自动化（DrissionPage）
    BROWSER_USER_DATA_DIR: str = str(BASE_DIR / "storage" / "browser_profile")
    BROWSER_HEADLESS: bool = False
    DOUYIN_TARGET_VIDEO_URL: str = ""
    DOUYIN_CREATOR_URL: str = "https://creator.douyin.com/creator-micro/content/upload"

    # 8. 流水线全局参数
    MAX_WORKERS: int = Field(default=4, ge=1)
    API_MAX_RETRIES: int = Field(default=3, ge=1)
    CLIP_DURATION_SECONDS: int = 5
    VIDEO_WIDTH: int = 1080
    VIDEO_HEIGHT: int = 1920
    SUBTITLE_FONT_PATH: str = "/System/Library/Fonts/PingFang.ttc"
    DAILY_RUN_TIME: str = "08:00"
    
    # 自动化发布设置
    PAUSE_BEFORE_PUBLISH: bool = False


# 实例化强类型配置对象
_cfg = AppSettings()

# 导出为模块变量（保持完全向后兼容）
DATABASE_URL = _cfg.DATABASE_URL
ANTHROPIC_API_KEY = _cfg.ANTHROPIC_API_KEY
ANTHROPIC_MODEL = _cfg.ANTHROPIC_MODEL
DEEPSEEK_API_KEY = _cfg.DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL = _cfg.DEEPSEEK_BASE_URL
DEEPSEEK_MODEL = _cfg.DEEPSEEK_MODEL
FLUX_API_KEY = _cfg.FLUX_API_KEY
FLUX_API_URL = _cfg.FLUX_API_URL
OPENAI_API_KEY = _cfg.OPENAI_API_KEY
OPENAI_BASE_URL = _cfg.OPENAI_BASE_URL
USE_LIPSYNC = _cfg.USE_LIPSYNC
MIDJOURNEY_API_KEY = _cfg.MIDJOURNEY_API_KEY
KLING_API_KEY = _cfg.KLING_API_KEY
KLING_API_URL = _cfg.KLING_API_URL
RUNWAY_API_KEY = _cfg.RUNWAY_API_KEY
RUNWAY_API_URL = _cfg.RUNWAY_API_URL
HAILUO_API_KEY = _cfg.HAILUO_API_KEY
HAILUO_API_URL = _cfg.HAILUO_API_URL
ZHIPU_API_KEY = _cfg.ZHIPU_API_KEY
VIDEO_PROVIDER = _cfg.VIDEO_PROVIDER
USE_ELEVENLABS_SFX = _cfg.USE_ELEVENLABS_SFX
ELEVENLABS_API_KEY = _cfg.ELEVENLABS_API_KEY
ELEVENLABS_VOICE_ID = _cfg.ELEVENLABS_VOICE_ID
ELEVENLABS_MODEL_ID = _cfg.ELEVENLABS_MODEL_ID
DASHSCOPE_API_KEY = _cfg.DASHSCOPE_API_KEY
DASHSCOPE_VOICE_ID = _cfg.DASHSCOPE_VOICE_ID
DASHSCOPE_NARRATOR_VOICE_ID = _cfg.DASHSCOPE_NARRATOR_VOICE_ID
BROWSER_USER_DATA_DIR = _cfg.BROWSER_USER_DATA_DIR
BROWSER_HEADLESS = _cfg.BROWSER_HEADLESS
DOUYIN_TARGET_VIDEO_URL = _cfg.DOUYIN_TARGET_VIDEO_URL
DOUYIN_CREATOR_URL = _cfg.DOUYIN_CREATOR_URL
MAX_WORKERS = _cfg.MAX_WORKERS
API_MAX_RETRIES = _cfg.API_MAX_RETRIES
CLIP_DURATION_SECONDS = _cfg.CLIP_DURATION_SECONDS
VIDEO_WIDTH = _cfg.VIDEO_WIDTH
VIDEO_HEIGHT = _cfg.VIDEO_HEIGHT
SUBTITLE_FONT_PATH = _cfg.SUBTITLE_FONT_PATH
DAILY_RUN_TIME = _cfg.DAILY_RUN_TIME
PAUSE_BEFORE_PUBLISH = _cfg.PAUSE_BEFORE_PUBLISH

# 7. 存储路径
STORAGE_TEMP_DIR: Path = BASE_DIR / "storage" / "temp"
STORAGE_OUTPUT_DIR: Path = BASE_DIR / "storage" / "outputs"

# 确保目录存在
STORAGE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
