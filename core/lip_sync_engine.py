"""
core/lip_sync_engine.py — 面部驱动与对口型引擎 (Wav2Lip + CodeFormer)

职责：
1. 封装对 Wav2Lip 的调用，实现动态视频嘴型合成。
2. 封装对 CodeFormer 的调用，强制修复被 Wav2Lip 损坏的面部/嘴部画质。
3. 动态劫持源码 (Monkey Patching)：拦截底层 CUDA 调用，重定向至 Mac MPS。
4. OOM 内存泄漏防御：显存主动释放 (gc + empty_cache)。
"""

import os
import subprocess
import gc
from pathlib import Path
from loguru import logger

class LipSyncError(Exception):
    pass

class LipSyncEngine:
    def __init__(self, models_dir: str = "./local_models"):
        self.models_dir = Path(models_dir)
        self.wav2lip_dir = self.models_dir / "Wav2Lip"
        self.codeformer_dir = self.models_dir / "CodeFormer"
        
        # 预设权重路径
        self.w2l_ckpt = self.wav2lip_dir / "checkpoints" / "wav2lip_gan.pth"
        
    def _create_monkey_patch_script(self, target_script: str, args: list) -> str:
        """
        优雅的源码劫持：生成一个临时 Python 脚本，
        在执行真正的推理前，动态篡改 torch.cuda 的底层指向。
        """
        args_str = ", ".join([f"'{a}'" for a in args])
        
        # 使用 sys.argv 欺骗 argparse
        patch_code = f"""
import sys
import torch
import gc

# [Monkey Patch] 劫持 CUDA 为 MPS
if not hasattr(torch, 'cuda') or not torch.cuda.is_available():
    torch.cuda.is_available = lambda: True
    torch.device = lambda dev: torch.device("mps") if "cuda" in str(dev) else torch.device(dev)
    
# 伪造命令行参数
sys.argv = ['{target_script}'] + [{args_str}]

print("[Monkey Patch] Successfully injected MPS redirect for CUDA!")

try:
    # 动态执行原脚本
    with open('{target_script}', 'r') as f:
        exec(f.read(), globals())
finally:
    # [OOM 防御] 强制垃圾回收和 MPS 显存清空
    gc.collect()
    if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        torch.mps.empty_cache()
        print("[Memory Management] MPS cache cleared.")
"""
        return patch_code

    def run_wav2lip(self, video_path: str, audio_path: str, output_path: str):
        """执行 Wav2Lip 对口型推理 (动态劫持 MPS)"""
        logger.info(f"[LipSync] Starting Wav2Lip for {video_path}")
        
        # 必须跳进目标目录才能找到依赖模块
        script_path = "inference.py"
        
        args = [
            "--checkpoint_path", str(self.w2l_ckpt.absolute()),
            "--face", str(Path(video_path).absolute()),
            "--audio", str(Path(audio_path).absolute()),
            "--outfile", str(Path(output_path).absolute()),
            "--nosmooth" # 防止过度平滑导致画面撕裂
        ]
        
        patch_code = self._create_monkey_patch_script(script_path, args)
        
        # 将劫持代码保存为临时执行脚本
        runner_path = self.wav2lip_dir / "mps_runner.py"
        with open(runner_path, "w") as f:
            f.write(patch_code)
            
        try:
            # 在 Wav2Lip 目录下执行劫持脚本
            subprocess.run(
                ["python", "mps_runner.py"],
                cwd=self.wav2lip_dir,
                check=True,
                capture_output=True,
                text=True
            )
            logger.success(f"[LipSync] Wav2Lip completed: {output_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Wav2Lip failed: {e.stderr}")
            raise LipSyncError(f"Wav2Lip Inference Error: {e.stderr}")
            
        return output_path

    def run_codeformer(self, input_video: str, output_dir: str):
        """执行 CodeFormer 面部修复"""
        logger.info(f"[LipSync] Starting CodeFormer restoration for {input_video}")
        
        script_path = "inference_codeformer.py"
        
        args = [
            "-w", "0.7",  # 保留一部分原脸特征，防止修复过度
            "-i", str(Path(input_video).absolute()),
            "-o", str(Path(output_dir).absolute()),
            "--bg_upsampler", "realesrgan"
        ]
        
        patch_code = self._create_monkey_patch_script(script_path, args)
        runner_path = self.codeformer_dir / "mps_runner.py"
        with open(runner_path, "w") as f:
            f.write(patch_code)
            
        try:
            subprocess.run(
                ["python", "mps_runner.py"],
                cwd=self.codeformer_dir,
                check=True,
                capture_output=True,
                text=True
            )
            logger.success(f"[LipSync] CodeFormer completed in {output_dir}")
        except subprocess.CalledProcessError as e:
            logger.error(f"CodeFormer failed: {e.stderr}")
            raise LipSyncError(f"CodeFormer Inference Error: {e.stderr}")

    def generate_talking_head(self, video_source: str, audio_source: str, final_out: str):
        """完整的对口型 + 画质修复流水线"""
        temp_w2l_out = str(Path(final_out).with_suffix(".w2l.mp4"))
        
        # 1. Wav2Lip 合成嘴型 (会导致嘴部模糊)
        self.run_wav2lip(video_source, audio_source, temp_w2l_out)
        
        # 2. CodeFormer 画质急救 (输出通常带一个复杂后缀名)
        restored_dir = str(Path(final_out).parent / "restored")
        self.run_codeformer(temp_w2l_out, restored_dir)
        
        # TODO: 把 CodeFormer 目录里的最终修复结果移回 final_out
        pass
