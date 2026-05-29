import json
from loguru import logger
from database.db_session import get_session
from database.models import Episode

from automation.tiktok_publisher import publish_to_tiktok, build_tiktok_caption
from automation.x_publisher import publish_to_x, build_x_tweet
from automation.kuaishou_publisher import publish_to_kuaishou, build_kuaishou_caption

def publish_e18_matrix():
    with get_session() as session:
        ep = session.query(Episode).filter_by(episode_number=18, theme_key='hospital_horror').first()
        if not ep:
            logger.error("Episode 18 not found!")
            return
            
        script = json.loads(ep.script_json)
        
        display_number = ep.episode_number - 12
        branches = script.get("next_branches", {})
        
        global_path = ep.video_global_path
        douyin_path = ep.video_output_path
        
        # 1. 发布到 TikTok (海外视频)
        episode_title_en = f"EP{display_number}: AI Interactive Horror"
        branch_a_en = branches.get("english_branch_a_teaser", "Option A")
        branch_b_en = branches.get("english_branch_b_teaser", "Option B")
        caption_en_tiktok = build_tiktok_caption(episode_title_en, branch_a_en, branch_b_en, ep.episode_tag)
        
        logger.info(f"Publishing {ep.episode_tag} to TikTok...")
        publish_to_tiktok(
            video_path=global_path,
            title=episode_title_en,
            caption=caption_en_tiktok,
        )
        logger.success("TikTok publish successful.")
        
        # 2. 发布到 X
        tweet_text = build_x_tweet(episode_title_en, ep.episode_tag)
        logger.info(f"Publishing {ep.episode_tag} to X...")
        publish_to_x(
            video_path=global_path,
            tweet_text=tweet_text,
            poll_options=["Option A", "Option B"]
        )
        logger.success("X publish successful.")
        
        # 3. 发布到快手
        raw_title = script.get("episode_title", ep.episode_tag)
        episode_title_cn = f"第{display_number}集：{raw_title}"
        branch_a_cn = branches.get("branch_a_teaser", "")
        branch_b_cn = branches.get("branch_b_teaser", "")
        caption_kuaishou = build_kuaishou_caption(script.get("episode_summary", ""), branch_a_cn, branch_b_cn)
        
        logger.info(f"Publishing {ep.episode_tag} to Kuaishou...")
        publish_to_kuaishou(
            video_path=douyin_path,
            title=episode_title_cn,
            caption=caption_kuaishou,
        )
        logger.success("Kuaishou publish successful.")

if __name__ == "__main__":
    publish_e18_matrix()
