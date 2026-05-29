import json
from loguru import logger
from database.db_session import get_session
from database.models import Episode

from automation.publisher import publish_to_douyin, build_douyin_caption
from automation.kuaishou_publisher import publish_to_kuaishou, build_kuaishou_caption

def publish_e22():
    with get_session() as session:
        ep = session.query(Episode).filter_by(episode_number=22).first()
        if not ep:
            logger.error("Episode 22 not found!")
            return
            
        script = json.loads(ep.script_json)
        branches = script.get("next_branches", {})
        
        douyin_path = ep.video_output_path
        
        raw_title = script.get("episode_title", ep.episode_tag)
        # Assuming internal E22 is public E19 (as discussed)
        display_title = f"第19集：{raw_title}"
        
        # 1. 抖音发布
        logger.info(f"Publishing {ep.episode_tag} to Douyin...")
        
        custom_caption = (
            "深夜的地下档案室，林悦终于找到了违规实验的致命铁证。可就在她准备带走病历的那一刻，那道最不该出现的身影，死死堵住了唯一的出口，并且按下了反锁键……\n\n"
            "是当场销毁证据换取活命，还是拼死护住真相？\n\n"
            "【紧急投票】现在她该怎么办？\n"
            "留在原地销毁证据保命扣1，带上病历硬拼到底扣2！\n\n"
            "#密室逃脱 #档案室的秘密 #微短剧 #悬疑推理 #人性测试 （本故事纯属虚构，切勿模仿）"
        )
        
        douyin_caption = custom_caption
        # 1. 抖音发布 (已完成，跳过)
        # logger.info(f"Publishing {ep.episode_tag} to Douyin...")
        # try:
        #     publish_to_douyin(
        #         video_path=douyin_path,
        #         title=display_title,
        #         caption=douyin_caption
        #     )
        #     logger.success("Douyin publish successful.")
        # except Exception as e:
        #     logger.error(f"Douyin publish failed: {e}")

        # 2. 快手发布
        logger.info(f"Publishing {ep.episode_tag} to Kuaishou...")
        ks_caption = custom_caption
        try:
            publish_to_kuaishou(
                video_path=douyin_path,
                title=display_title,
                caption=ks_caption
            )
            logger.success("Kuaishou publish successful.")
        except Exception as e:
            logger.error(f"Kuaishou publish failed: {e}")

if __name__ == "__main__":
    publish_e22()
