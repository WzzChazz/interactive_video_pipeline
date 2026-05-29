import json
import time
from dotenv import load_dotenv
from automation.publisher import publish_to_douyin, build_douyin_caption

load_dotenv(override=True)

script_json_str = """{
  "episode_title": "储物间的秘密",
  "episode_summary": "林悦躲进储物间，发现墙上奇怪的规则和一台老式录音机，播放录音后得知医院的真实秘密，但门外的脚步声越来越近。",
  "chosen_branch": "A",
  "scenes": [],
  "next_branches": {
    "branch_a_teaser": "林悦紧握门把手，拼命顶住门。她发现储物间角落有一扇暗门，但钻进去会是一条未知的血腥通道。选择A：钻进暗门，寻找真相。",
    "branch_b_teaser": "林悦决定放手一搏，冲出去直面白大褂。她抄起一把生锈的手术刀，准备拼死一搏。选择B：冲出去，和医生正面交锋。"
  }
}"""

script = json.loads(script_json_str)

episode_title = script.get("episode_title", "S01E014")
episode_summary = script.get("episode_summary", "")
branches = script.get("next_branches", {})
branch_a_teaser = branches.get("branch_a_teaser", "")
branch_b_teaser = branches.get("branch_b_teaser", "")

caption = build_douyin_caption(
    episode_summary=episode_summary,
    branch_a_teaser=branch_a_teaser,
    branch_b_teaser=branch_b_teaser,
    episode_tag="S01E014"
)

output_path = "/Users/mac/project/interactive_video_pipeline/storage/outputs/S01E014/S01E014_final.mp4"

print(f"Uploading {output_path} to Douyin...")
try:
    video_url = publish_to_douyin(
        video_path=output_path,
        title=episode_title,
        caption=caption,
        check_aigc=True,
    )
    print(f"Success! Video URL: {video_url}")
except Exception as e:
    print(f"Failed to publish: {e}")
    print("浏览器窗口将保持开启 5 分钟，请手动在页面上点击【发布】按钮！")
    time.sleep(300)
