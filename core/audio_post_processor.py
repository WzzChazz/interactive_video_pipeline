from pathlib import Path
import shutil
from loguru import logger

try:
    from pedalboard import Pedalboard, Reverb, Compressor, HighpassFilter, Gain, Chorus, LowpassFilter
    from pedalboard.io import AudioFile
    _HAS_PEDALBOARD = True
except ImportError:
    _HAS_PEDALBOARD = False
    logger.warning("pedalboard not installed. Audio post-processing will be bypassed.")

HORROR_PRESETS = {}
if _HAS_PEDALBOARD:
    HORROR_PRESETS = {
        "protagonist_fearful": Pedalboard([
            HighpassFilter(cutoff_frequency_hz=120),
            Reverb(room_size=0.3, wet_level=0.15),
            Compressor(threshold_db=-18, ratio=3.0),
            Gain(gain_db=2),
        ]),
        "clone_robotic": Pedalboard([
            Chorus(rate_hz=1.2, depth=0.4, centre_delay_ms=7),
            Compressor(threshold_db=-12, ratio=8.0),
            LowpassFilter(cutoff_frequency_hz=4000),
            Gain(gain_db=3),
        ]),
        "narrator": Pedalboard([
            HighpassFilter(cutoff_frequency_hz=80),
            Compressor(threshold_db=-20, ratio=2.0),
            Gain(gain_db=1),
        ]),
    }

def apply_audio_preset(input_path: Path, output_path: Path, emotion: str, role: str) -> Path:
    """Apply post-processing effects based on character role and emotion."""
    if not _HAS_PEDALBOARD:
        shutil.copy(input_path, output_path)
        return output_path
        
    preset_key = "narrator"
    if role and "克隆" in role:
        preset_key = "clone_robotic"
    elif emotion in ("fearful", "terrified", "shocked", "nervous"):
        preset_key = "protagonist_fearful"
        
    board = HORROR_PRESETS.get(preset_key)
    if not board:
        shutil.copy(input_path, output_path)
        return output_path

    logger.info(f"[AudioPostProcessor] Applying '{preset_key}' preset to {input_path.name}")
    try:
        with AudioFile(str(input_path)) as f:
            audio = f.read(f.frames)
            sr = f.samplerate
            
        processed = board(audio, sr)
        
        with AudioFile(str(output_path), 'w', sr, audio.shape[0]) as f:
            f.write(processed)
            
        return output_path
    except Exception as e:
        logger.error(f"[AudioPostProcessor] Failed to apply preset: {e}")
        shutil.copy(input_path, output_path)
        return output_path
