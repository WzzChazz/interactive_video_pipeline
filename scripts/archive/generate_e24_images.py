import sys
import json
import shutil
from pathlib import Path
from database.db_session import get_session
from database.models import Episode, SceneAsset, EpisodeStatus
from main import stage_generate_images

def run():
    with get_session() as session:
        ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
        if not ep:
            print("Episode not found")
            return
            
        # Reset image status so it redraws
        assets = session.query(SceneAsset).filter_by(episode_id=ep.id).all()
        for a in assets:
            a.image_status = "PENDING"
        ep.status = EpisodeStatus.GENERATING_IMAGES
        session.commit()
        script = json.loads(ep.script_json)
        
    print("Generating Images natively via Zhipu...")
    stage_generate_images(script, ep)
    print("Image generation complete.")

if __name__ == "__main__":
    run()
