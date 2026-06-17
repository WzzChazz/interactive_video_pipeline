"""
publish_e14_and_e16.py
======================
1. 发布 E14（储物间的秘密，第2集）— 从未发布到抖音
2. 重新发布 E16（陷阱中的真相，第4集）— 被标为"不适宜公开"

用法：
    PYTHONPATH=. .venv/bin/python publish_e14_and_e16.py
"""

import json
from pathlib import Path
from loguru import logger

from database.db_session import get_session
from database.models import Episode
from automation.publisher import publish_to_douyin, build_douyin_caption


EPISODES = [
    {
        "episode_number": 14,
        "display_number": 2,   # 14 - 12 = 2
from automation.publisher import publish_to_douyin

DB_PATH = "storage/pipeline.db"

def get_episode_data(ep_num: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT episode_number, title, script_json, douyin_video_id, douyin_video_url, status "
        "FROM episodes WHERE episode_number=?",
        (ep_num,)
    )
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def main():
    logger.info("=" * 55)
    logger.info("🚀 开始重新发布 E16 (第4集)")
    logger.info("=" * 55)
    
    # ---------------------------------
    # 发布 E16
    # ---------------------------------
    e16 = get_episode_data(16)
    if not e16:
        logger.error("找不到 E16 数据")
        return
        
    script16 = json.loads(e16["script_json"])
    # Offset -12 => 16 - 12 = 4
    display_number = 4
    raw_title = script16.get("episode_title", e16["title"])
    title_cn = f"第{display_number}集：{raw_title}"
    summary_cn = script16.get("episode_summary", "")
    
    branches = script16.get("next_branches", {})
    branch_a = branches.get("branch_a_teaser", "选项A")
    branch_b = branches.get("branch_b_teaser", "选项B")
    
    final_caption = f"【{title_cn}】{summary_cn} \n\n下一集剧情，你来决定！👉 评论区回复「A」或「B」影响后续剧情发展！\n\nA: {branch_a}\nB: {branch_b}\n\n#AI共创 #互动短剧 #悬疑"
    
    video_path_16 = "storage/outputs/S01E016/S01E016_final.mp4"
    logger.info(f"📤 正在发布 E16: {title_cn}")
    
    try:
        video_id, url = publish_to_douyin(video_path_16, final_caption)
        logger.success(f"✅ E16 发布成功！ID: {video_id}, URL: {url}")
        
        # Update DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "UPDATE episodes SET douyin_video_id=?, douyin_video_url=?, status='PUBLISHED' WHERE episode_number=16",
            (video_id, url)
        )
        conn.commit()
        conn.close()
        logger.info("✅ 数据库已更新 E16 状态")
    except Exception as e:
        logger.error(f"❌ E16 发布失败: {e}")

if __name__ == "__main__":
    main()
