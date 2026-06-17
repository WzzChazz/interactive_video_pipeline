"""
test_audio_pipeline.py
======================
单独测试最新的音频混音引擎 (Edge-TTS + 尖叫拦截 + 混音闪避 + 精准字幕)，不生成视频。
"""

import sys
import tempfile
from pathlib import Path
from loguru import logger
from core.audio_gen import generate_audio
from core.ffmpeg_compiler import _build_audio_track, _generate_srt

logger.remove()
logger.add(sys.stderr, level="DEBUG")

def run_test():
    # Mocking scenes with extreme emotions and different characters
    scenes = [
        {
            "scene_index": 1,
            "speaker": "医生",
            "dialogue": "这是什么东西？[pause:1.5s]我们必须马上离开这里。",
            "sfx_prompt": "creepy hospital ambient sounds, distant dripping, low rumble"
        },
        {
            "scene_index": 2,
            "speaker": "护士",
            "dialogue": "啊啊啊！！！救命啊！", # Should trigger Scream Fallback
            "sfx_prompt": "heavy footsteps approaching fast, terrifying chase music"
        },
        {
            "scene_index": 3,
            "speaker": "反派",
            "dialogue": "跑吧，[pause:1s]这里是没有出口的。",
            "sfx_prompt": "eerie cinematic drone, deep horror bass drop"
        }
    ]
    
    # 模拟每个片段的长度
    scene_durations = [5.0, 3.0, 6.0]
    total_duration = sum(scene_durations)
    
    episode_tag = "test_audio_v2"
    theme_key = "hospital_horror"
    
    logger.info("=== 1. Testing Audio Gen (Edge-TTS & Scream Fallback) ===")
    audio_manifest = generate_audio(scenes, episode_tag, episode_id=None, theme_key=theme_key)
    logger.success(f"Audio Manifest: {audio_manifest}")
    
    # Create a temp dir for FFmpeg test
    desktop = Path("/Users/mac/Desktop/test_pipeline")
    desktop.mkdir(parents=True, exist_ok=True)
    
    mixed_audio_out = desktop / "mixed_audio_ducking_test.aac"
    
    logger.info("=== 2. Testing FFmpeg Compiler (Ducking & Spatial Reverb) ===")
    try:
        _build_audio_track(
            scenes=scenes,
            audio_manifest=audio_manifest,
            scene_durations=scene_durations,
            total_duration=total_duration,
            theme_key=theme_key,
            output_path=mixed_audio_out,
            tmp_dir=desktop
        )
        logger.success(f"Cinematic Mixed Audio generated: {mixed_audio_out}")
    except Exception as e:
        logger.error(f"FFmpeg Compilation failed: {e}")
        
    logger.info("=== 3. Testing Precision Subtitles ===")
    srt_content = _generate_srt(scenes, scene_durations, audio_manifest, lang="cn")
    srt_out = desktop / "precision_subtitles.srt"
    with open(srt_out, "w", encoding="utf-8") as f:
        f.write(srt_content)
    logger.success(f"Precision SRT generated: {srt_out}")
    
if __name__ == "__main__":
    run_test()
