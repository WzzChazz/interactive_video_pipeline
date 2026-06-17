"""
E24 全流程重生成脚本
- 使用用户提供的 6 张图（已拷贝到 S01E024/images/scene_0X.jpg）
- 重生成配音（语速修复：主角 1.0，克隆体 0.85）
- 用阿里云 Wanx 重生成视频
- FFmpeg 后处理：压暗背景，强化手电筒光感
"""
import json, os, subprocess, sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from loguru import logger
from database.db_session import get_session
from database.models import Episode, SceneAsset
from core.audio_gen import generate_audio
from core.video_gen import generate_video_clips
import core.video_gen, config.settings

config.settings.VIDEO_PROVIDER = "aliyun"
core.video_gen.VIDEO_PROVIDER = "aliyun"

EPISODE_TAG = "S01E024"
CLIPS_DIR   = Path(f"storage/temp/{EPISODE_TAG}/clips")
AUDIO_DIR   = Path(f"storage/temp/{EPISODE_TAG}/audio")
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_MANIFEST = {
    1: f"storage/temp/{EPISODE_TAG}/images/scene_01.png",
    2: f"storage/temp/{EPISODE_TAG}/images/scene_02.png",
    3: f"storage/temp/{EPISODE_TAG}/images/scene_03.png",
    4: f"storage/temp/{EPISODE_TAG}/images/scene_04.png",
    5: f"storage/temp/{EPISODE_TAG}/images/scene_05.png",
    6: f"storage/temp/{EPISODE_TAG}/images/scene_06.png",
}


def darken_video(src: Path, dst: Path):
    """
    后处理：极度压暗背景，保留明亮高光（手电筒光束）。
    curves 曲线：暗部压至接近 0，高光基本保留。
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", (
            # Step 1: 整体压暗 0.45 倍
            "eq=brightness=-0.15:contrast=1.3:saturation=0.7,"
            # Step 2: 曲线压暗，只让高光（>160）留亮
            "curves=master='0/0 100/20 180/120 255/210',"
            # Step 3: 加微量噪点，模拟夜间摄像质感
            "noise=alls=8:allf=t"
        ),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        str(dst)
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg darken failed: {result.stderr.decode()}")
        raise RuntimeError("darken_video failed")
    logger.success(f"Darkened: {dst.name}")


with get_session() as session:
    ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
    if not ep:
        logger.error("Episode 24 not found in database!")
        exit(1)

    script = json.loads(ep.script_json) if isinstance(ep.script_json, str) else ep.script_json
    scenes = script.get("scenes", [])

    # ── 1. 重置数据库状态 ──────────────────────────────────────────────────
    assets = session.query(SceneAsset).filter_by(episode_id=ep.id).all()
    for a in assets:
        a.audio_status = "PENDING"
        a.video_status = "PENDING"
        a.audio_path = None
        a.video_path = None
    session.commit()
    logger.info(f"Reset {len(assets)} SceneAssets to PENDING")

    # ── 2. 清理旧文件 ───────────────────────────────────────────────────────
    for f in CLIPS_DIR.glob("*.mp4"):
        f.unlink()
    for f in AUDIO_DIR.glob("scene_*_voice.mp3"):
        f.unlink()
    for f in AUDIO_DIR.glob("scene_*_voice.vtt"):
        f.unlink()
    logger.info("Old clips and audio cleared")

    episode_id = ep.id

# ── 3. 重新生成配音（语速已在 tts_engine.py 修复）──────────────────────────
logger.info("=== Step 1: 重新生成配音 ===")
with get_session() as session:
    ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
    script = json.loads(ep.script_json) if isinstance(ep.script_json, str) else ep.script_json
    scenes = script.get("scenes", [])
    episode_id = ep.id

audio_results = generate_audio(scenes, EPISODE_TAG, episode_id)
logger.success(f"Audio done: {list(audio_results.keys())}")

# ── 4. 阿里云 Wanx 图生视频 ──────────────────────────────────────────────
logger.info("=== Step 2: 阿里云 Wanx 图生视频 ===")
with get_session() as session:
    ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
    script = json.loads(ep.script_json) if isinstance(ep.script_json, str) else ep.script_json
    scenes = script.get("scenes", [])
    episode_id = ep.id

video_results = generate_video_clips(scenes, IMAGE_MANIFEST, EPISODE_TAG, episode_id)
logger.success(f"Raw clips done: {list(video_results.keys())}")

# ── 5. FFmpeg 后处理：压暗每个分镜 ───────────────────────────────────────
logger.info("=== Step 3: FFmpeg 暗化后处理 ===")
for idx, raw_path in video_results.items():
    raw = Path(raw_path)
    dark = CLIPS_DIR / f"scene_{idx:02d}_dark.mp4"
    try:
        darken_video(raw, dark)
        # 替换原始文件
        raw.unlink()
        dark.rename(raw)
        logger.info(f"Scene {idx} darkened and replaced")
    except Exception as e:
        logger.error(f"Scene {idx} darken failed: {e}")

# ── 6. 音画合流（lipsync 降级为直接合并）──────────────────────────────────
logger.info("=== Step 4: 音画合流 ===")
from core.lip_sync_engine import LipSyncEngine
engine = LipSyncEngine()

for i in range(1, 7):
    idx_str = f"{i:02d}"
    video_path = CLIPS_DIR / f"scene_{idx_str}.mp4"
    audio_path = AUDIO_DIR / f"scene_{idx_str}_voice.mp3"
    out_path   = CLIPS_DIR / f"scene_{idx_str}_lipsync.mp4"

    if video_path.exists() and audio_path.exists():
        logger.info(f"Merging audio → Scene {i}...")
        try:
            engine.generate_talking_head(str(video_path), str(audio_path), str(out_path))
            logger.success(f"Scene {i} merged: {out_path.name}")
        except Exception as e:
            logger.error(f"Scene {i} merge failed: {e}")
    else:
        logger.warning(f"Scene {i}: missing video={video_path.exists()} audio={audio_path.exists()}")

logger.success("=== 全流程完成！请在 Pipeline Dashboard 查看结果 ===")
