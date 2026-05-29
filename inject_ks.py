import json
from loguru import logger
from DrissionPage import WebPage, ChromiumOptions
from database.db_session import get_session
from database.models import Episode
from automation.kuaishou_publisher import _fill_caption_ks, build_kuaishou_caption

def inject():
    try:
        logger.info("Connecting to active browser...")
        co = ChromiumOptions().set_local_port(9222)
        page = WebPage(chromium_options=co)
        
        with get_session() as session:
            ep = session.query(Episode).filter(Episode.episode_number == 13).first()
            
        script = json.loads(ep.script_json) if ep.script_json else {}
        display_number = ep.episode_number - 12
        raw_title = script.get("episode_title", ep.episode_tag)
        title = f"第{display_number}集：{raw_title}"[:30]
        
        branches = script.get("next_branches", {})
        caption = build_kuaishou_caption(
            episode_summary=script.get("episode_summary", ""),
            branch_a_teaser=branches.get("branch_a_teaser", ""),
            branch_b_teaser=branches.get("branch_b_teaser", ""),
            episode_tag=ep.episode_tag,
            title=title,
        )
        
        combined_text = f"{title}\n\n{caption}"
        logger.info("Injecting text...")
        _fill_caption_ks(page, combined_text)
        logger.success("Text successfully injected!")
        
    except Exception as e:
        logger.error(f"Injection failed: {e}")

if __name__ == "__main__":
    inject()
