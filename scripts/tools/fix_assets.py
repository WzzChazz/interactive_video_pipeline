import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

from database.db_session import get_session
from database.models import Episode, SceneAsset

with get_session() as session:
    # Find the episode we just inserted (Episode 28)
    ep = session.query(Episode).filter_by(episode_number=28).first()
    if not ep:
        print("Episode 28 not found!")
        sys.exit(1)
        
    print(f"Fixing assets for Episode ID: {ep.id}")
    
    # Delete the corrupted scene assets
    session.query(SceneAsset).filter_by(episode_id=ep.id).delete()
    
    # Insert the correct scene assets pointing to S01E028 files!
    for idx in range(1, 8):
        asset = SceneAsset(
            episode_id=ep.id,
            scene_index=idx,
            image_status="COMPLETED",
            image_path=str(_PROJECT_ROOT / f"storage/temp/S01E028/images/scene_{idx:02d}.png"),
            audio_status="COMPLETED",
            audio_path=str(_PROJECT_ROOT / f"storage/temp/S01E028/audio/scene_{idx:02d}_voice.mp3"),
            video_status="COMPLETED",
            video_path=str(_PROJECT_ROOT / f"storage/temp/S01E028/clips/scene_{idx:02d}.mp4")
        )
        session.add(asset)
        
    session.commit()
    print("Successfully fixed Scene Assets! The pipeline will now skip the cloud rendering!")
