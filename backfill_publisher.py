import json
import time
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from automation.tiktok_publisher import publish_to_tiktok, build_tiktok_caption
from automation.x_publisher import publish_to_x, build_x_tweet

def backfill_publish():
    logger.info("Starting legacy backfill publishing to TikTok and X for episodes 14-16...")
    with get_session() as session:
        episodes = session.query(Episode).filter(Episode.episode_number.in_([14, 15, 16])).order_by(Episode.episode_number).all()
        for ep in episodes:
            if not ep.video_global_path or not ep.script_json:
                logger.warning(f"Skipping Episode {ep.episode_number}: Missing global video path or script.")
                continue
                
            script_data = json.loads(ep.script_json)
            branches = script_data.get("next_branches", {})
            
            display_number = ep.episode_number - 12
            episode_title_en = f"EP{display_number}: AI Interactive Horror"
            branch_a_en = branches.get("english_branch_a_teaser", "Option A")
            branch_b_en = branches.get("english_branch_b_teaser", "Option B")
            
            caption_en_tiktok = build_tiktok_caption(episode_title_en, branch_a_en, branch_b_en, ep.episode_tag)
            tweet_text = build_x_tweet(episode_title_en, ep.episode_tag)
            
            logger.info(f"Publishing Episode {ep.episode_number} (Display EP{display_number}) to Global Matrix...")
            
            try:
                # 1. TikTok
                publish_to_tiktok(
                    video_path=ep.video_global_path,
                    title=episode_title_en,
                    caption=caption_en_tiktok,
                )
                
                # 2. X (Twitter)
                publish_to_x(
                    video_path=ep.video_global_path,
                    tweet_text=tweet_text,
                    poll_options=["Option A", "Option B"]
                )
                
                logger.success(f"Episode {ep.episode_number} matrix publish successful!")
                time.sleep(5)  # 稍微等待防止频率过快
                
            except Exception as e:
                logger.error(f"Failed to publish Episode {ep.episode_number}: {e}")

if __name__ == "__main__":
    backfill_publish()
