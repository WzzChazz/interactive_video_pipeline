import json
import time
from loguru import logger
from database.db_session import get_session
from database.models import Episode
from automation.x_publisher import publish_to_x, build_x_tweet

def resume_publish():
    with get_session() as session:
        episodes = session.query(Episode).filter(Episode.episode_number.in_([15])).order_by(Episode.episode_number).all()
        for ep in episodes:
            script_data = json.loads(ep.script_json)
            branches = script_data.get("next_branches", {})
            display_number = ep.episode_number - 12
            episode_title_en = f"EP{display_number}: AI Interactive Horror"
            branch_a_en = branches.get("english_branch_a_teaser", "Option A")
            branch_b_en = branches.get("english_branch_b_teaser", "Option B")
            tweet_text = build_x_tweet(episode_title_en, ep.episode_tag)
            
            logger.info(f"Publishing Episode {ep.episode_number} to X (Twitter)...")
            try:
                publish_to_x(ep.video_global_path, tweet_text, [branch_a_en, branch_b_en])
                logger.success(f"Successfully published Episode {ep.episode_number} to X.")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Failed to publish Episode {ep.episode_number} to X: {e}")

if __name__ == "__main__":
    resume_publish()
