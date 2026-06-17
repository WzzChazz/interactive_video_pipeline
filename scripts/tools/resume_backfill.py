import json
import time
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from automation.tiktok_publisher import publish_to_tiktok, build_tiktok_caption
from automation.x_publisher import publish_to_x, build_x_tweet

def resume_publish():
    with get_session() as session:
        episodes = session.query(Episode).filter(Episode.episode_number.in_([14, 15, 16, 17])).order_by(Episode.episode_number).all()
        for ep in episodes:
            script_data = json.loads(ep.script_json)
            branches = script_data.get("next_branches", {})
            display_number = ep.episode_number - 12
            episode_title_en = f"EP{display_number}: AI Interactive Horror"
            branch_a_en = branches.get("english_branch_a_teaser", "Option A")
            branch_b_en = branches.get("english_branch_b_teaser", "Option B")
            caption_en_tiktok = build_tiktok_caption(episode_title_en, branch_a_en, branch_b_en, ep.episode_tag)
            tweet_text = build_x_tweet(episode_title_en, ep.episode_tag)
            
            logger.info(f"Publishing Episode {ep.episode_number} to Global Matrix...")
            try:
                if ep.episode_number == 14:
                    # TikTok 已经被用户手动发布
                    logger.info("Episode 14 TikTok already published by user. Publishing to X only.")
                    publish_to_x(ep.video_global_path, tweet_text, [branch_a_en, branch_b_en])
                else:
                    publish_to_tiktok(ep.video_global_path, episode_title_en, caption_en_tiktok)
                    publish_to_x(ep.video_global_path, tweet_text, [branch_a_en, branch_b_en])
                time.sleep(5)
            except Exception as e:
                logger.error(f"Failed to publish Episode {ep.episode_number}: {e}")

if __name__ == "__main__":
    resume_publish()
