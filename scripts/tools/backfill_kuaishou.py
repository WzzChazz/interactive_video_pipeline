"""
backfill_kuaishou.py
====================
快手历史集数补发脚本。
将数据库中所有已有本地视频的集数（video_output_path 不为空）
依次发布到快手创作者平台。

用法：
    PYTHONPATH=. .venv/bin/python backfill_kuaishou.py
    PYTHONPATH=. .venv/bin/python backfill_kuaishou.py --start 12 --end 17

注意：快手不支持多账号并发，脚本依次串行执行。每集之间等待 10 秒冷却。
"""

import argparse
import json
import os
import time
from pathlib import Path

from loguru import logger
from database.db_session import get_session
from database.models import Episode
from automation.kuaishou_publisher import publish_to_kuaishou, build_kuaishou_caption


# 没有 video_output_path 的集数，手动指定备用路径映射
_FALLBACK_PATHS = {
    17: "/Users/mac/project/interactive_video_pipeline/storage/outputs/S01E017/S01E017_douyin.mp4",
    12: "/Users/mac/project/interactive_video_pipeline/storage/outputs/S01E012/S01E012_final.mp4",
    13: "/Users/mac/project/interactive_video_pipeline/storage/outputs/S01E013/S01E013_final.mp4",
}


def _get_video_path(ep: Episode) -> str | None:
    """取视频路径，优先 video_output_path，其次用备用映射。"""
    if ep.video_output_path and Path(ep.video_output_path).exists():
        return ep.video_output_path
    fallback = _FALLBACK_PATHS.get(ep.episode_number)
    if fallback and Path(fallback).exists():
        return fallback
    return None


def _build_title(ep: Episode, script: dict) -> str:
    """构建快手标题（≤30字）"""
    display_number = ep.episode_number - 12
    raw_title = script.get("episode_title", ep.episode_tag)
    return f"第{display_number}集：{raw_title}"[:30]


def backfill():
    """
    补发逻辑：仅补发抖音上已发布的《储物间的秘密》系列
    即 episode_number 13-17（对应播出第 1-5 集）。
    目前 E13 已经发完，接下来发 E14 到 E17。
    """
    EPISODES_TO_PUBLISH = [14, 15, 16, 17]
    logger.info(f"=== 快手历史补发开始（《储物间的秘密》第 1-5 集）===")

    with get_session() as session:
        episodes = (
            session.query(Episode)
            .filter(Episode.episode_number.in_(EPISODES_TO_PUBLISH))
            .order_by(Episode.episode_number)
            .all()
        )

    logger.info(f"待补发集数：{[e.episode_number for e in episodes]}")

    total = len(episodes)
    success_count = 0
    skip_count = 0
    fail_list = []

    for idx, ep in enumerate(episodes, 1):
        ep_num = ep.episode_number
        logger.info(f"\n[{idx}/{total}] 正在处理第 {ep_num} 集...")

        video_path = _get_video_path(ep)
        if not video_path:
            logger.warning(f"第 {ep_num} 集：视频文件不存在，跳过。")
            skip_count += 1
            continue

        try:
            script = json.loads(ep.script_json) if ep.script_json else {}
        except Exception:
            script = {}

        title = _build_title(ep, script)
        branches = script.get("next_branches", {})
        caption = build_kuaishou_caption(
            episode_summary=script.get("episode_summary", ""),
            branch_a_teaser=branches.get("branch_a_teaser", ""),
            branch_b_teaser=branches.get("branch_b_teaser", ""),
            episode_tag=ep.episode_tag,
            title=title,
        )

        logger.info(f"标题：{title}")
        logger.info(f"视频：{video_path}")

        try:
            publish_to_kuaishou(
                video_path=video_path,
                title=title,
                caption=caption,
            )
            success_count += 1
            logger.success(f"✅ 第 {ep_num} 集快手发布成功！")
        except Exception as e:
            logger.error(f"❌ 第 {ep_num} 集快手发布失败：{e}")
            fail_list.append(ep_num)

        if idx < total:
            logger.info("等待 10 秒后处理下一集...")
            time.sleep(10)

    logger.info("\n" + "=" * 50)
    logger.info(f"快手历史补发完成！")
    logger.info(f"  成功：{success_count} 集")
    logger.info(f"  跳过（无视频文件）：{skip_count} 集")
    logger.info(f"  失败：{len(fail_list)} 集 → {fail_list}")
    logger.info("=" * 50)


if __name__ == "__main__":
    backfill()
