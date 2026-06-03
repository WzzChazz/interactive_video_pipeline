"""
database/db_session.py
======================
数据库连接与 Session 管理模块。

设计要点：
- 使用 SQLAlchemy 2.x engine + sessionmaker 工厂。
- 提供 get_session() 上下文管理器，确保 Session 在异常时自动回滚并关闭。
- 提供 init_db() 一键建表（幂等操作，可多次安全调用）。
- 使用连接池优化（check_same_thread=False 解决 SQLite 多线程限制）。
"""

from contextlib import contextmanager
from typing import Generator

from loguru import logger
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import DATABASE_URL
from database.models import Base


# ──────────────────────────────────────────────────────────
# Engine 初始化
# ──────────────────────────────────────────────────────────
def _build_engine() -> Engine:
    """
    构建 SQLAlchemy Engine。
    SQLite 特殊配置：
      - connect_args check_same_thread=False：允许多线程共享连接
      - WAL 模式：显著提升并发读写性能，避免流水线并行写入时锁等待
      - foreign_keys=ON：启用外键约束（SQLite 默认关闭）
    """
    connect_args = {}
    if DATABASE_URL.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        echo=False,          # 生产环境关闭 SQL 日志，调试时可改为 True
        pool_pre_ping=True,  # 连接使用前自动探活
    )

    # SQLite 专属优化：在每次新连接时开启 WAL + 外键约束
    if DATABASE_URL.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA synchronous=NORMAL;")  # 性能与安全的平衡点
            cursor.close()

    return engine


# ── 全局单例 Engine ────────────────────────────────────────
_engine: Engine = _build_engine()

# ── Session 工厂 ───────────────────────────────────────────
_SessionFactory = sessionmaker(
    bind=_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # commit 后对象属性不失效，避免 lazy-load 问题
)


# ──────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────

def init_db() -> None:
    """
    幂等建表。
    首次运行时自动创建所有表；后续运行时若表已存在则跳过。
    在 main.py 启动时调用一次即可。
    """
    logger.info("Initializing database at: {}", DATABASE_URL)
    Base.metadata.create_all(bind=_engine)
    logger.success("Database initialized successfully.")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    线程安全的 Session 上下文管理器。

    用法::

        with get_session() as session:
            episode = session.get(Episode, episode_id)
            episode.status = EpisodeStatus.GENERATING
            # session.commit() 在 with 块结束时自动调用

    异常处理：
        - 若 with 块内抛出异常，自动执行 rollback。
        - 无论如何，最终执行 session.close() 归还连接池资源。
    """
    session: Session = _SessionFactory()
    try:
        yield session
    except Exception as exc:
        session.rollback()
        logger.error("Database session rolled back due to: {}", exc)
        raise
    finally:
        session.close()


def get_engine() -> Engine:
    """返回全局 Engine 实例（供 Alembic 迁移脚本使用）"""
    return _engine


def health_check() -> bool:
    """
    数据库健康检查：执行一条简单 SQL 验证连接正常。
    返回 True 表示正常，False 表示异常。
    """
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database health check failed: {}", exc)
        return False
