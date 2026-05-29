import json
import logging
from database.db_session import get_session
from database.models import Episode
from automation.publisher import publish_to_douyin

logging.basicConfig(level=logging.INFO)

def republish():
    with get_session() as session:
        episode = session.query(Episode).filter_by(episode_number=15).first()
        if not episode:
            print("Episode 15 not found.")
            return
        
        script_dict = json.loads(episode.script_json) if episode.script_json else {}
        output_path = "storage/outputs/S01E015/S01E015_final.mp4"
        
        ep_num = episode.episode_number
        ep_title = script_dict.get("episode_title", "")
        title_str = f"第{ep_num}集：{ep_title}"
        caption_str = f"【互动视频】第{ep_num}集：{ep_title}\n\n{script_dict.get('episode_summary', '')}\n\n#互动视频 #悬疑 #细思极恐 #医院"
        
        print(f"Starting manual republish for S01E015: {title_str}...")
        try:
            publish_to_douyin(output_path, title_str, caption_str)
            print("Republish complete.")
        except Exception as e:
            print(f"Error during republish: {e}")

if __name__ == "__main__":
    republish()
