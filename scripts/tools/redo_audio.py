from database.models import Episode, SceneAsset, EpisodeStatus
from database.db_session import get_session
import json

with get_session() as session:
    ep = session.query(Episode).order_by(Episode.id.desc()).first()
    if ep:
        print(f"Targeting Episode: {ep.episode_tag}")
        # Reset episode status to GENERATING_ASSETS so the pipeline will redo assets
        ep.status = EpisodeStatus.GENERATING_ASSETS
        
        # Reset ONLY audio_status in SceneAsset table
        assets = session.query(SceneAsset).filter_by(episode_id=ep.id).all()
        for asset in assets:
            asset.audio_status = "PENDING"
            # NOTE: We keep video_status and image_status as COMPLETED!
        
        session.commit()
        print("Database updated! The pipeline will now skip images & videos, and ONLY redo audio.")
    else:
        print("No episode found!")
