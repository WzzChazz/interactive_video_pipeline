import os
import json
from automation.publisher import publish_to_douyin, build_douyin_caption
from database.db_session import get_session
from database.models import Episode
from loguru import logger

def main():
    with get_session() as session:
        episode = session.query(Episode).filter(Episode.episode_number == 17).first()
        if not episode:
            logger.error("Episode 17 not found!")
            return
            
        script = json.loads(episode.script_json)
        
        display_number = episode.episode_number - 12
        raw_title = script.get("episode_title", episode.episode_tag)
        episode_title_cn = f"第{display_number}集：{raw_title}"
        episode_summary_cn = script.get("episode_summary", "")
        branches        = script.get("next_branches", {})
        branch_a_cn = branches.get("branch_a_teaser", "")
        branch_b_cn = branches.get("branch_b_teaser", "")

        caption_cn = build_douyin_caption(
            episode_summary=episode_summary_cn,
            branch_a_teaser=branch_a_cn,
            branch_b_teaser=branch_b_cn,
            episode_tag=episode.episode_tag,
            title=episode_title_cn,
        )
        
        logger.info("Opening Douyin to manually publish Episode 17...")
        try:
            publish_to_douyin(
                video_path=episode.video_output_path,
                title=episode_title_cn,
                caption=caption_cn,
                check_aigc=True,
                branch_a_teaser=branch_a_cn,
                branch_b_teaser=branch_b_cn,
            )
            logger.success("Done!")
        except Exception as e:
            logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
