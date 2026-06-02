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
        优雅的封装执行：生成一个临时 Python 脚本动态执行。
        底层库已经被原生修改支持 MPS，无需再污染 torch.cuda。
        """
        args_str = ", ".join([f"'{a}'" for a in args])
        
        patch_code = f"""
import sys
import torch
import gc

# 伪造命令行参数
sys.argv = ['{target_script}'] + [{args_str}]

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
            "--face_det_batch_size", "1", # 强制单帧推理，防止 Accelerate 崩溃/OOM
            "--wav2lip_batch_size", "1"
        ]
        
        patch_code = self._create_monkey_patch_script(script_path, args)
        
        # 将劫持代码保存为临时执行脚本
        runner_path = self.wav2lip_dir / "mps_runner.py"
        with open(runner_path, "w") as f:
            f.write(patch_code)
            
        import sys
        try:
            # 在 Wav2Lip 目录下执行劫持脚本
            subprocess.run(
                [sys.executable, "mps_runner.py"],
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
            "-w", "0.3",  # 降低保真度权重，强制 AI 使用最高清细节覆盖 Wav2Lip 的模糊嘴部
            "-i", str(Path(input_video).absolute()),
            "-o", str(Path(output_dir).absolute())
        ]
        
        patch_code = self._create_monkey_patch_script(script_path, args)
        runner_path = self.codeformer_dir / "mps_runner.py"
        with open(runner_path, "w") as f:
            f.write(patch_code)
            
        import sys
        try:
            subprocess.run(
                [sys.executable, "mps_runner.py"],
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
        import shutil
        import os
        
        temp_w2l_out = str(Path(final_out).with_suffix(".w2l.mp4"))
        
        # 1. Wav2Lip 合成嘴型 (会导致嘴部模糊)
        self.run_wav2lip(video_source, audio_source, temp_w2l_out)
        
        # 2. CodeFormer 画质急救 (如果没有下载完毕则跳过，防止崩溃)
        codeformer_weight = Path("/Users/mac/project/interactive_video_pipeline/local_models/CodeFormer/weights/CodeFormer/codeformer.pth")
        
        if codeformer_weight.exists() and codeformer_weight.stat().st_size > 100 * 1024 * 1024:
            logger.info("[LipSync] CodeFormer weights detected, initiating face restoration...")
            restored_dir = str(Path(final_out).parent / "restored")
            self.run_codeformer(temp_w2l_out, restored_dir)
            
            # --- 补全的获取 CodeFormer 视频逻辑 ---
            try:
                # CodeFormer 默认会在 restored_dir 下生成类似结果，我们直接搜索 mp4
                restored_videos = list(Path(restored_dir).rglob("*.mp4"))
                if restored_videos:
                    # 按照修改时间排序，拿到最新生成的视频
                    best_video = sorted(restored_videos, key=lambda x: x.stat().st_mtime, reverse=True)[0]
                    shutil.move(str(best_video), final_out)
                    logger.success(f"[LipSync] 成功输出 CodeFormer 修复后的高清视频: {final_out}")
                    
                    # 清理临时文件和目录
                    if Path(temp_w2l_out).exists():
                        os.remove(temp_w2l_out)
                    shutil.rmtree(restored_dir, ignore_errors=True)
                    return final_out
                else:
                    logger.error("[LipSync] CodeFormer 运行完毕但未找到 mp4 文件，回退至 Wav2Lip 原片。")
            except Exception as e:
                logger.error(f"[LipSync] 移动 CodeFormer 结果失败: {e}，回退至 Wav2Lip 原片。")
        else:
            logger.warning("[LipSync] CodeFormer weights missing or incomplete! Bypassing face restoration to prevent pipeline crash.")
            
        # 安全兜底：如果走到这里，说明高清修复失败或未运行
        if not Path(final_out).exists():
            shutil.move(temp_w2l_out, final_out)
            logger.success(f"[LipSync] 已输出 Wav2Lip 原始视频 (未进行画质修复) 至 {final_out}")
