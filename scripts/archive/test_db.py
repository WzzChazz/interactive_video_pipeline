from database.db_session import get_session
from database.models import Episode
with get_session() as session:
    ep = session.query(Episode).order_by(Episode.id.desc()).first()
    if ep:
        print(f"Latest Episode: {ep.episode_tag}, Status: {ep.status}")
        ep.status = "GENERATING_ASSETS"
        session.commit()
        print(f"Set to GENERATING_ASSETS to prevent script overwrite!")
