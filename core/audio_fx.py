import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import soundfile as sf
    from pedalboard import Pedalboard, Reverb, Chorus, Delay, PitchShift, Compressor, HighpassFilter
except ImportError:
    logger.warning("pedalboard or soundfile not installed. Audio FX will be disabled.")
    sf = None
    Pedalboard = None

class AudioEffectsEngine:
    @staticmethod
    def apply_environment_reverb(audio_path: Path, out_path: Path) -> Path:
        """应用空旷走廊/医院的环境混响"""
        if not Pedalboard:
            return audio_path
            
        try:
            audio, sample_rate = sf.read(str(audio_path))
            
            # 配置真实感强烈的环境混响（例如废弃医院）
            board = Pedalboard([
                HighpassFilter(cutoff_frequency_hz=80),
                Compressor(threshold_db=-20, ratio=2.5),
                Reverb(
                    room_size=0.6,
                    damping=0.5,
                    wet_level=0.35,
                    dry_level=0.7,
                    width=0.8
                )
            ])
            
            effected = board(audio, sample_rate)
            sf.write(str(out_path), effected, sample_rate)
            return out_path
        except Exception as e:
            logger.error(f"[AudioFX] Reverb failed: {e}")
            return audio_path

    @staticmethod
    def apply_clone_distortion(audio_path: Path, out_path: Path) -> Path:
        """应用克隆人的冷酷无机质/机械感失真"""
        if not Pedalboard:
            return audio_path
            
        try:
            audio, sample_rate = sf.read(str(audio_path))
            
            # 配置赛博/机械感
            board = Pedalboard([
                PitchShift(semitones=-0.3), # 极轻微降调，避免变成怪物音
                Chorus(
                    rate_hz=1.5, 
                    depth=0.15, 
                    centre_delay_ms=5.0, 
                    feedback=0.05, 

                    mix=0.4
                ),
                Delay(delay_seconds=0.03, feedback=0.1, mix=0.2), # 极短的延迟产生金属敲击般的共鸣
                Compressor(threshold_db=-15, ratio=4) # 压缩让人声显得很近
            ])
            
            effected = board(audio, sample_rate)
            sf.write(str(out_path), effected, sample_rate)
            return out_path
        except Exception as e:
            logger.error(f"[AudioFX] Clone distortion failed: {e}")
            return audio_path
