import json
from database.db_session import get_session
from database.models import Episode
from automation.tiktok_publisher import build_tiktok_caption

def generate_manual_tiktok_data():
    with get_session() as session:
        # Get E14, E15, E16, E17
        episodes = session.query(Episode).filter(Episode.episode_number.in_([14, 15, 16, 17])).order_by(Episode.episode_number).all()
        
        print("\n" + "="*60)
        print("TIKTOK MANUAL UPLOAD DATA FOR EPISODES 14, 15, 16, 17")
        print("="*60 + "\n")
        
        for ep in episodes:
            if not ep.video_global_path:
                continue
                
            script_data = json.loads(ep.script_json)
            branches = script_data.get("next_branches", {})
            display_number = ep.episode_number - 12
            episode_title_en = f"EP{display_number}: AI Interactive Horror"
            branch_a_en = branches.get("english_branch_a_teaser", "Option A")
            branch_b_en = branches.get("english_branch_b_teaser", "Option B")
            caption_text = build_tiktok_caption(episode_title_en, branch_a_en, branch_b_en, ep.episode_tag)
            
            print(f"🎬 第 {display_number} 集 (对应系统 E{ep.episode_number})")
            print(f"📁 视频文件路径: {ep.video_global_path}")
            print(f"📝 英文互动文案 (直接复制):")
            print("-" * 40)
            print(caption_text)
            print("-" * 40)
            print("\n")

if __name__ == "__main__":
    generate_manual_tiktok_data()
