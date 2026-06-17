import sys
import os
from pathlib import Path

# Add project root to sys.path so we can import main
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from celery import Celery
from loguru import logger

# Initialize Celery app
celery_app = Celery(
    "interactive_video_pipeline",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    worker_prefetch_multiplier=1, # Important for heavy ML tasks, grab 1 at a time
    task_acks_late=True # Don't ack until task is fully finished
)

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def run_pipeline_task(self, theme_key: str = "hospital_horror"):
    """
    Celery task that executes the main video generation pipeline.
    """
    logger.info(f"[Celery] Starting pipeline task for theme: {theme_key}")
    try:
        from main import run_pipeline
        run_pipeline(theme_key)
        return {"status": "success", "theme": theme_key}
    except Exception as exc:
        logger.error(f"[Celery] Pipeline failed: {exc}")
        # Automatically retry the task if it fails (e.g. temporary API failure)
        raise self.retry(exc=exc)
