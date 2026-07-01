import os
import time
import json
from loguru import logger

from database.db_session import get_session
from database.models import Episode, EpisodeStatus
from main import stage_generate_script, stage_generate_assets, stage_compile

def batch_generate(theme_key: str, count: int = 3):
    logger.info(f"🚀 启动【{theme_key}】高并发矩阵连发模式：目标 {count} 集")
    
    for i in range(count):
        with get_session() as session:
            # 找到当前未完成的一集，或者创建新的一集
            ep = session.query(Episode).filter(
                Episode.theme_key == theme_key,
                Episode.status.in_([
                    EpisodeStatus.VOTING,
                    EpisodeStatus.GENERATING_SCRIPT,
                    EpisodeStatus.PENDING_REVIEW,
                    EpisodeStatus.GENERATING_ASSETS
                ])
            ).order_by(Episode.episode_number.asc()).first()
            
            if not ep:
                last = session.query(Episode).filter(Episode.theme_key == theme_key).order_by(Episode.episode_number.desc()).first()
                next_num = last.episode_number + 1 if last else 1
                ep = Episode(season_id=1, episode_number=next_num, theme_key=theme_key, status=EpisodeStatus.VOTING)
                session.add(ep)
                session.commit()
                session.refresh(ep)
                logger.info(f"✨ 创建新集数: {ep.episode_tag}")

        logger.info(f"▶️ 开始处理: {ep.episode_tag} (当前状态: {ep.status})")
        
        try:
            # 阶段 1/2: 生成剧本 (如果还没生成)
            if ep.status in [EpisodeStatus.VOTING, EpisodeStatus.GENERATING_SCRIPT]:
                # 假设 A 赢了，或者默认走 A 分支（连贯生产）
                branch = "A" if ep.episode_number > 1 else "INIT"
                script_dict = stage_generate_script(branch, ep)
                
                with get_session() as session:
                    db_ep = session.get(Episode, ep.id)
                    db_ep.status = EpisodeStatus.PENDING_REVIEW
                    session.commit()
                    ep.status = EpisodeStatus.PENDING_REVIEW
            
            # 自动审核通过
            if ep.status == EpisodeStatus.PENDING_REVIEW:
                logger.warning(f"⚡ 自动绕过人工审核，进入资产生成阶段: {ep.episode_tag}")
                with get_session() as session:
                    db_ep = session.get(Episode, ep.id)
                    db_ep.status = EpisodeStatus.GENERATING_ASSETS
                    session.commit()
                    ep.status = EpisodeStatus.GENERATING_ASSETS
            
            # 阶段 3/4: 资产与合成
            if ep.status == EpisodeStatus.GENERATING_ASSETS:
                # 重新从 DB 拉取最新数据（script_json 可能是在另一个 session 里写入的）
                with get_session() as session:
                    ep = session.get(Episode, ep.id)
                script = json.loads(ep.script_json)
                assets = stage_generate_assets(script, ep)
                output_path = stage_compile(assets, ep)
                
                # 完成后，不立刻发布，而是标记为 COMPLETED 存入本地库
                with get_session() as session:
                    db_ep = session.get(Episode, ep.id)
                    db_ep.status = EpisodeStatus.COMPLETED
                    session.commit()
                
                logger.success(f"✅ {ep.episode_tag} 生产完毕！视频文件已保存至: {output_path}")
                
        except Exception as e:
            logger.error(f"❌ {ep.episode_tag} 生产失败: {e}")
            break

if __name__ == "__main__":
    batch_generate("deep_sea_survival", 3)
