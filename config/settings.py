"""
config/settings.py
==================
全局配置中枢。所有 API Keys、路径、超时参数均从此处统一读取。
生产环境通过 .env 文件注入，本地开发直接填写默认值即可。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── 项目根目录 ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

# 优先加载同级 .env 文件（开发本地覆盖）
load_dotenv(BASE_DIR / ".env", override=True)


# ──────────────────────────────────────────────────────────
# 1. 数据库
# ──────────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{BASE_DIR / 'storage' / 'pipeline.db'}"
)

# ──────────────────────────────────────────────────────────
# 2. LLM 配置
# ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner")

# ──────────────────────────────────────────────────────────
# 3. 视觉生图
# ──────────────────────────────────────────────────────────
FLUX_API_KEY: str = os.getenv("FLUX_API_KEY", "")
FLUX_API_URL: str = os.getenv("FLUX_API_URL", "https://api.siliconflow.cn/v1/images/generations")
USE_LIPSYNC: bool = os.getenv("USE_LIPSYNC", "false").lower() == "true"

MIDJOURNEY_API_KEY: str = os.getenv("MIDJOURNEY_API_KEY", "")

# ──────────────────────────────────────────────────────────
# 4. 图生视频
# ──────────────────────────────────────────────────────────
KLING_API_KEY: str = os.getenv("KLING_API_KEY", "")
KLING_API_URL: str = os.getenv("KLING_API_URL", "https://api-beijing.klingai.com/v1")

RUNWAY_API_KEY: str = os.getenv("RUNWAY_API_KEY", "")
RUNWAY_API_URL: str = os.getenv("RUNWAY_API_URL", "https://api.dev.runwayml.com/v1")

HAILUO_API_KEY: str = os.getenv("HAILUO_API_KEY", "")
HAILUO_API_URL: str = os.getenv("HAILUO_API_URL", "https://api.minimax.io/v1")

ZHIPU_API_KEY: str = os.getenv("ZHIPU_API_KEY", "")

# 默认优先使用 Zhipu 
VIDEO_PROVIDER: str = os.getenv("VIDEO_PROVIDER", "zhipu")  # "kling" | "runway" | "hailuo" | "zhipu"

# ──────────────────────────────────────────────────────────
# 5. 音频合成
# ──────────────────────────────────────────────────────────
USE_ELEVENLABS_SFX: bool = os.getenv("USE_ELEVENLABS_SFX", "false").lower() == "true"
ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "")      # 音效/备用声音 ID
ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_VOICE_ID: str = os.getenv("DASHSCOPE_VOICE_ID", "cosyvoice-v3.5-plus-vd-bailian-f0f1b1bb3679400486ad031fc8bd2bed") # 主人公：用户定制的专属清冷悬疑青年音
DASHSCOPE_NARRATOR_VOICE_ID: str = os.getenv("DASHSCOPE_NARRATOR_VOICE_ID", "longlaotie") # 旁白：默认成熟低沉男声 (龙老铁)

# ──────────────────────────────────────────────────────────
# 6. 浏览器自动化（DrissionPage）
# ──────────────────────────────────────────────────────────
# 持久化 Chrome User Data 目录，用于保持抖音登录 Session
BROWSER_USER_DATA_DIR: str = os.getenv(
    "BROWSER_USER_DATA_DIR",
    str(BASE_DIR / "storage" / "browser_profile")
)
BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

# 目标抖音视频 URL（上一集，用于抓取投票评论）
DOUYIN_TARGET_VIDEO_URL: str = os.getenv("DOUYIN_TARGET_VIDEO_URL", "")
# 抖音创作者中心上传入口
DOUYIN_CREATOR_URL: str = "https://creator.douyin.com/creator-micro/content/upload"

# ──────────────────────────────────────────────────────────
# 7. 存储路径
# ──────────────────────────────────────────────────────────
STORAGE_TEMP_DIR: Path = BASE_DIR / "storage" / "temp"
STORAGE_OUTPUT_DIR: Path = BASE_DIR / "storage" / "outputs"

# 确保目录存在
STORAGE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────
# 8. 流水线全局参数
# ──────────────────────────────────────────────────────────
# 并行生成资产时的最大线程数
MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "4"))

# API 调用最大重试次数（tenacity 使用）
API_MAX_RETRIES: int = int(os.getenv("API_MAX_RETRIES", "3"))

# 单个视频片段时长（秒）
CLIP_DURATION_SECONDS: int = int(os.getenv("CLIP_DURATION_SECONDS", "5"))

# 视频输出分辨率
VIDEO_WIDTH: int = int(os.getenv("VIDEO_WIDTH", "1080"))
VIDEO_HEIGHT: int = int(os.getenv("VIDEO_HEIGHT", "1920"))  # 竖屏 9:16

# 字幕字体路径（请替换为系统中实际存在的字体）
SUBTITLE_FONT_PATH: str = os.getenv(
    "SUBTITLE_FONT_PATH",
    "/System/Library/Fonts/PingFang.ttc"  # macOS 默认中文字体
)

# 每日任务执行时间（24h 格式，例如 "08:00"）
DAILY_RUN_TIME: str = os.getenv("DAILY_RUN_TIME", "08:00")

# ──────────────────────────────────────────────────────────
# 8. 自动化发布设置
# ──────────────────────────────────────────────────────────
BROWSER_HEADLESS: bool = False
PAUSE_BEFORE_PUBLISH: bool = os.getenv("PAUSE_BEFORE_PUBLISH", "false").lower() == "true"
