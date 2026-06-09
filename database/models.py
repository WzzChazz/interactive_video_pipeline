"""
database/models.py
==================
SQLAlchemy ORM 数据模型定义。
使用 SQLAlchemy 2.x 的 DeclarativeBase + Mapped 注解风格，
提供完整的类型安全和自动补全支持。
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Enum,
    Integer,
    Float,
    String,
    Text,
    DateTime,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ──────────────────────────────────────────────────────────
# 枚举：Episode 生命周期状态机
# ──────────────────────────────────────────────────────────
class EpisodeStatus(str, enum.Enum):
    """
    Episode 全生命周期状态枚举。
    继承 str 使其在 JSON 序列化时直接输出字符串值。

    状态流转:
        VOTING → GENERATING → COMPLETED → PUBLISHED
                     ↓
                   FAILED  （任意阶段均可跌入）
    """
    VOTING            = "VOTING"             # 上一集已发布，正在收集投票
    GENERATING_SCRIPT = "GENERATING_SCRIPT"  # LLM 生成剧本中
    PENDING_REVIEW    = "PENDING_REVIEW"     # 剧本生成完毕，等待人工审核确认
    GENERATING_IMAGES = "GENERATING_IMAGES"  # 剧本审核通过，生图中
    PENDING_IMAGE_REVIEW = "PENDING_IMAGE_REVIEW" # 生图完毕，等待人工确认
    GENERATING_VIDEOS = "GENERATING_VIDEOS"  # 图片审核通过，图生视频中
    PENDING_VIDEO_REVIEW = "PENDING_VIDEO_REVIEW" # 视频片段生成完毕，等待人工确认
    GENERATING_AUDIO_AND_COMPILE = "GENERATING_AUDIO_AND_COMPILE" # 配音、音效、视频最终硬烧录合并
    PENDING_PUBLISH   = "PENDING_PUBLISH"    # 最终视频合成完毕，等待勾选平台发布
    COMPLETED         = "COMPLETED"          # 视频合成完毕(旧状态保留兼容)
    PUBLISHED         = "PUBLISHED"          # 已成功发布至选中平台
    FAILED            = "FAILED"             # 流水线某步骤失败，需人工介入


# ──────────────────────────────────────────────────────────
# ORM Base
# ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""
    pass


# ──────────────────────────────────────────────────────────
# Episode 表
# ──────────────────────────────────────────────────────────
class Episode(Base):
    """
    Episodes 表：记录每一集短剧的完整生命周期数据。

    设计原则：
    - 所有 AI 生成内容（剧本 JSON、分镜 Prompt）以 TEXT 存储，避免过早模式化。
    - 状态字段 status 是核心幂等字段，流水线重启时从此字段决定断点续跑位置。
    - asset_manifest_json 记录所有中间产物路径，便于失败重跑时跳过已完成步骤。
    """

    __tablename__ = "episodes"

    # ── 主键 & 标识 ────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="季份 ID")
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False, comment="本季第几集")
    theme_key: Mapped[str] = mapped_column(String(64), nullable=False, default="hospital_horror", comment="关联的题材宇宙")
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="本集标题")

    # ── 抖音视频关联 ────────────────────────────────────────
    douyin_video_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True, comment="发布后的抖音视频 ID"
    )
    douyin_video_url: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True, comment="发布后的抖音视频链接（用于下一集抓票）"
    )

    # ── 抖音数据回流 ─────────────────────────────────────────
    views_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="抖音实际播放量"
    )
    likes_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="抖音实际点赞量"
    )
    audience_profile: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="观众画像JSON(如男女比例, 年龄层)"
    )
    completion_rate: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="完播率 (例如 0.15 表示 15%)"
    )
    five_sec_retention: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="5秒留存率 (例如 0.30 表示 30%)"
    )

    # ── LLM 生成内容 ────────────────────────────────────────
    script_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Claude 生成的结构化剧本 JSON（含分镜数、台词、视觉 Prompt、音效 Prompt）"
    )
    chosen_branch: Mapped[Optional[str]] = mapped_column(
        String(8), nullable=True, comment="本集由哪个分支驱动：A / B / INIT"
    )

    # ── 投票统计 ────────────────────────────────────────────
    vote_a_count: Mapped[int] = mapped_column(Integer, default=0, comment="A 票数")
    vote_b_count: Mapped[int] = mapped_column(Integer, default=0, comment="B 票数")

    # ── 资产清单 ────────────────────────────────────────────
    asset_manifest_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment=(
            "资产路径清单 JSON，记录各分镜对应的 image/audio/video/sfx 本地路径，"
            "用于断点续跑时判断哪些步骤已完成"
        )
    )

    # ── 最终合成视频 ────────────────────────────────────────
    video_output_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="最终合成的国内版视频文件绝对路径"
    )
    video_global_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="最终合成的海外版双语视频文件绝对路径"
    )
    video_duration_seconds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="最终视频时长（秒）"
    )

    # ── 状态机 ─────────────────────────────────────────────
    status: Mapped[EpisodeStatus] = mapped_column(
        Enum(EpisodeStatus, native_enum=False, length=50),
        default=EpisodeStatus.VOTING,
        nullable=False,
        index=True,
        comment="Episode 生命周期状态"
    )

    # ── 错误追踪 ────────────────────────────────────────────
    error_stage: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="失败时所处阶段（如 'image_gen', 'ffmpeg'）"
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="失败时的错误详情"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="自动重试次数"
    )

    # ── 时间戳 ─────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="成功发布至抖音的时间"
    )

    def __repr__(self) -> str:
        return (
            f"<Episode S{self.season_id:02d}E{self.episode_number:03d} "
            f"branch={self.chosen_branch!r} status={self.status.value!r}>"
        )

    @property
    def episode_tag(self) -> str:
        """返回标准集号标识，如 'S01E003'"""
        return f"S{self.season_id:02d}E{self.episode_number:03d}"


# ──────────────────────────────────────────────────────────
# SceneAsset 表 (分镜级资产缓存断点续跑)
# ──────────────────────────────────────────────────────────
class SceneAsset(Base):
    """
    SceneAsset 表：记录每个分镜的各类资产生成状态与本地路径。
    用于断点续跑，若某分镜图像已生成，则直接跳过该步骤。
    """
    __tablename__ = "scene_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, comment="关联的 Episode ID")
    scene_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="分镜序号")

    image_status: Mapped[str] = mapped_column(String(32), default="PENDING", comment="PENDING/COMPLETED/FAILED")
    image_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    audio_status: Mapped[str] = mapped_column(String(32), default="PENDING", comment="PENDING/COMPLETED/FAILED")
    audio_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    video_status: Mapped[str] = mapped_column(String(32), default="PENDING", comment="PENDING/COMPLETED/FAILED")
    video_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<SceneAsset ep={self.episode_id} scene={self.scene_index}>"

