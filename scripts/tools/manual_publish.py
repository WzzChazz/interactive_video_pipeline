import json
from database.db_session import get_session
from database.models import Episode
from automation.publisher import publish_to_douyin, build_douyin_caption

with get_session() as session:
    ep = session.get(Episode, 12)
    script = json.loads(ep.script_json)
    
    episode_title   = script.get("episode_title", ep.episode_tag)
    episode_summary = script.get("episode_summary", "")
    branches        = script.get("next_branches", {})
    branch_a_teaser = branches.get("branch_a_teaser", "")
    branch_b_teaser = branches.get("branch_b_teaser", "")

    caption = build_douyin_caption(
        episode_summary=episode_summary,
        branch_a_teaser=branch_a_teaser,
        branch_b_teaser=branch_b_teaser,
        episode_tag=ep.episode_tag,
    )
    
    print(f"Publishing {ep.video_output_path}...")
    try:
        url = publish_to_douyin(ep.video_output_path, episode_title, caption)
        print("Success URL:", url)
        ep.status = "PUBLISHED"
        ep.douyin_video_url = url
        session.commit()
    except Exception as e:
        print("Failed:", e)
