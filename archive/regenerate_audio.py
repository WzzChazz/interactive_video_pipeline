import json
from database.db_session import get_session
from database.models import Episode
from core.audio_gen import generate_audio

with get_session() as session:
    ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
    script = json.loads(ep.script_json)
    scenes = script.get('scenes', [])
    generate_audio(scenes, ep.episode_tag, ep.id, ep.theme_key)
