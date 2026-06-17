import sys
import json
from database.db_session import get_session
from database.models import Episode
from main import stage_generate_videos, stage_generate_audio_and_compile

def run_e24():
    with get_session() as session:
        ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
        if not ep:
            print("Episode 24 not found!")
            return
        script = json.loads(ep.script_json)
        
    print("Generating Videos from the prepared collage slices...")
    stage_generate_videos(script, ep)
    
    print("Audio, Lipsync & Compile...")
    stage_generate_audio_and_compile(script, ep)
    
    print("All done!")

if __name__ == "__main__":
    run_e24()
