import os
import subprocess
from pathlib import Path
from loguru import logger

def isolate_vocals(reference_audio: str | Path, output_dir: str | Path = None) -> Path:
    """
    使用 Demucs (htdemucs) 从给定的参考音频中提取纯净人声 (vocals.wav)。
    用于提升声音克隆的质量（去除背景杂音/BGM）。
    
    Args:
        reference_audio: 包含人声和杂音的原始音频路径。
        output_dir: 结果保存目录，如果为空则保存在源文件同级的 demucs_out 目录下。
        
    Returns:
        vocals_path: 纯净人声的文件路径。如果失败则返回原路径。
    """
    ref_path = Path(reference_audio)
    if not ref_path.exists():
        logger.warning(f"[Demucs] Reference audio not found: {ref_path}")
        return ref_path
        
    if output_dir is None:
        output_dir = ref_path.parent / "demucs_out"
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # htdemucs 会将结果输出到 output_dir/htdemucs/文件名/vocals.wav
    stem = ref_path.stem
    expected_out = output_dir / "htdemucs" / stem / "vocals.wav"
    
    # 如果已经提取过，直接返回缓存
    if expected_out.exists() and expected_out.stat().st_size > 0:
        logger.info(f"[Demucs] Using cached isolated vocals: {expected_out}")
        return expected_out
        
    logger.info(f"[Demucs] Starting vocal isolation for {ref_path.name}...")
    
    cmd = [
        "demucs",
        "-n", "htdemucs",
        "-o", str(output_dir),
        str(ref_path)
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        if expected_out.exists():
            logger.success(f"[Demucs] Vocal isolation completed: {expected_out}")
            return expected_out
        else:
            logger.error(f"[Demucs] Expected output not found: {expected_out}")
            return ref_path
    except subprocess.CalledProcessError as e:
        logger.error(f"[Demucs] Failed to isolate vocals: {e.stderr}")
        return ref_path
    except Exception as e:
        logger.error(f"[Demucs] Unexpected error: {e}")
        return ref_path
