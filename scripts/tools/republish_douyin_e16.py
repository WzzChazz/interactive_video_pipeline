"""
republish_douyin_e16.py
=======================
专门重新发布第4集（陷阱中的真相，E16）到抖音。

原视频被标记为"不适宜公开"，需要：
1. （可选）手动在抖音后台将原视频删除
2. 用此脚本全新上传一遍，带上正确的标题和文案

用法：
    PYTHONPATH=. .venv/bin/python republish_douyin_e16.py
"""

import json
from pathlib import Path
from loguru import logger

from database.db_session import get_session
from database.models import Episode
from automation.publisher import publish_to_douyin, build_douyin_caption


EPISODE_NUMBER = 16
VIDEO_PATH = "/Users/mac/project/interactive_video_pipeline/storage/outputs/S01E016/S01E016_final.mp4"


def main():
    logger.info("=" * 55)
    logger.info("🔁 第4集（陷阱中的真相）抖音重新发布")
    logger.info("=" * 55)

    if not Path(VIDEO_PATH).exists():
        logger.error(f"视频文件不存在：{VIDEO_PATH}")
        return

    with get_session() as session:
        ep = session.query(Episode).filter(Episode.episode_number == EPISODE_NUMBER).first()
        if not ep:
            logger.error(f"数据库中找不到第 {EPISODE_NUMBER} 集！")
            return

        try:
            script = json.loads(ep.script_json) if ep.script_json else {}
        except Exception:
            script = {}

    display_number = EPISODE_NUMBER - 12  # 正确偏移：E16 = 第4集
    raw_title    = script.get("episode_title", "陷阱中的真相")
    episode_title = f"第{display_number}集：{raw_title}"

    branches     = script.get("next_branches", {})
    branch_a     = branches.get("branch_a_teaser", "")
    branch_b     = branches.get("branch_b_teaser", "")
    summary      = script.get("episode_summary", "")

    caption = build_douyin_caption(
        episode_summary=summary,
        branch_a_teaser=branch_a,
        branch_b_teaser=branch_b,
        episode_tag=ep.episode_tag,
        title=episode_title,
    )

    logger.info(f"标题：{episode_title}")
    logger.info(f"文案长度：{len(caption)} 字")
    logger.info(f"视频：{VIDEO_PATH}")

    try:
        url = publish_to_douyin(
            video_path=VIDEO_PATH,
            title=episode_title,
            caption=caption,
            check_aigc=True,
            branch_a_teaser=branch_a,
            branch_b_teaser=branch_b,
        )
        logger.success(f"✅ 第4集重新发布成功！URL: {url}")
    except Exception as e:
        logger.error(f"❌ 重新发布失败：{e}")


if __name__ == "__main__":
    main()
