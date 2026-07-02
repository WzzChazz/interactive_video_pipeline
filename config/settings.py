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

import redis as _redis

_redis_pool = _redis.ConnectionPool(
    host='localhost', 
    port=6379, 
    db=0,
    decode_responses=True, 
    socket_timeout=2
)

def get_redis() -> _redis.Redis:
    return _redis.Redis(connection_pool=_redis_pool)

def get_chinese_font() -> str:
    import platform
    candidates = {
        "Darwin": [
            "/System/Library/Fonts/Supplemental/Songti.ttc",
            "/System/Library/Fonts/PingFang.ttc"
        ],
        "Windows": ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"],
        "Linux": ["/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"],
    }
    for path in candidates.get(platform.system(), []):
        if os.path.exists(path): return path
    return ""  # Fallback gracefully

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
    JIMENG_API_KEY: str = ""  # 火山引擎 Ark Key（即梦 Seedance 图生视频）
    JIMENG_API_URL: str = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
    JIMENG_MODEL: str = "doubao-seedance-1-0-pro-fast-250528"  # 默认=最便宜可用档
    # 模型链：⚠️ 链序=付费序!账户有余额时免费额度用尽后会按首个模型价格【静默扣费】(不报错不切换),
    # 所以必须便宜在前。官方价(元/百万token): pro-fast 4.2 | 1.5-pro无声 8 | lite 10 | pro 15。
    # 一条5s竖屏≈25万token → pro-fast≈¥1.04/条(治愈微动足够), pro≈¥3.7(别当主力)。
    # 2.0/2.0-fast 在Ark是 46/37元/M(≈¥9/条),太贵不入链。
    JIMENG_MODEL_CHAIN: str = ("doubao-seedance-1-0-pro-fast-250528,"
                               "doubao-seedance-1-5-pro-250528,"
                               "doubao-seedance-1-0-lite-i2v-250428,doubao-seedance-1-0-lite-i2v-250528,"
                               "doubao-seedance-1-0-pro-250528")
    JIMENG_IMAGE_MODEL: str = "doubao-seedream-4-0-250828"  # 即梦 Seedream 4.0（文生图 + 参考图锁脸）
    IP_REFERENCE_IMAGE: str = ""  # 固定IP定妆照路径(林溪+团团)；设了且有即梦key→每张图走Seedream参考图锁脸
    KEN_BURNS_ONLY: bool = True  # 验证期=True：治愈所有镜头都走免费Ken Burns(不花图生视频钱)；想开动作镜改False
    VIDEO_PROVIDER: str = "seedance"  # seedance(即梦) | zhipu | kling | hailuo | aliyun
    # 治愈线内容基调开关（A/B 用）：
    # cozy = 正向治愈金句分享引擎(萌尖峰+一句可截图正向治愈金句+@礼物CTA)，合规、转发/收藏驱动 —— 默认、推荐
    # sassy= 搞笑嘴替+有事发生+戏剧反转，⚠️ 偏摆烂/丧、可能触犯"正向引导"被限流，慎用
    HEALING_STYLE: str = "cozy"

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
JIMENG_API_KEY = _cfg.JIMENG_API_KEY
JIMENG_API_URL = _cfg.JIMENG_API_URL
JIMENG_MODEL = _cfg.JIMENG_MODEL
JIMENG_MODEL_CHAIN = _cfg.JIMENG_MODEL_CHAIN
JIMENG_IMAGE_MODEL = _cfg.JIMENG_IMAGE_MODEL
IP_REFERENCE_IMAGE = _cfg.IP_REFERENCE_IMAGE
KEN_BURNS_ONLY = _cfg.KEN_BURNS_ONLY
VIDEO_PROVIDER = _cfg.VIDEO_PROVIDER
HEALING_STYLE = _cfg.HEALING_STYLE
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
