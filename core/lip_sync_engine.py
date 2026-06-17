"""
core/lip_sync_engine.py — 面部驱动与对口型引擎 (Wav2Lip + CodeFormer)

优化记录 (voice-pro 分析):
  P2a: 增加 AI 生成视频的人脸预处理增强（对比度+锐化），提升 Wav2Lip 检测率
  P2b: 当人脸检测失败时，改用 FFmpeg 音画合流（不做对口型），避免静默跳过
  P2c: 增加 LatentSync 接口预留（比 Wav2Lip 更适合 AI 生成视频，未来可直接启用）
  P2d: Wav2Lip 失败时的分辨率降级策略，提升检测成功率

职责：
1. 封装对 Wav2Lip 的调用，实现动态视频嘴型合成。
2. 封装对 CodeFormer 的调用，强制修复被 Wav2Lip 损坏的面部/嘴部画质。
3. 动态劫持源码 (Monkey Patching)：拦截底层 CUDA 调用，重定向至 Mac MPS。
4. OOM 内存泄漏防御：显存主动释放 (gc + empty_cache)。
"""

import os
import subprocess
import gc
import shutil
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

    # ─────────────────────────────────────────────────────────────────────────
    # P2a: AI 生成视频专项预处理
    # Wav2Lip 是基于真实人脸视频训练的，对 AI 生成视频（低对比度、过度平滑的皮肤）
    # 检测率极低。通过 FFmpeg 增强对比度+锐化，模拟真实人脸的高频纹理细节。
    # ─────────────────────────────────────────────────────────────────────────
    def _preprocess_ai_video_for_lipsync(self, video_path: str, output_path: str) -> str:
        """
        对 AI 生成的视频进行预处理，提升 Wav2Lip 人脸检测率。
        策略：
        1. 增强对比度和饱和度（AI 视频往往过于平滑）
        2. 应用轻微锐化滤镜（模拟真实皮肤的高频纹理）
        3. 降分辨率到 720p（Wav2Lip 在高分辨率下检测更容易失败）
        """
        logger.info(f"[LipSync] P2a: 对 AI 生成视频进行预处理增强 ...")
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", (
                    "scale=720:-2,"                     # 降分辨率到 720p 宽
                    "eq=contrast=1.3:brightness=0.05:saturation=1.2,"  # 增强对比度
                    "unsharp=5:5:1.0:5:5:0.0"          # 轻度锐化
                ),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                output_path
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            logger.success(f"[LipSync] P2a: 预处理完成 → {output_path}")
            return output_path
        except subprocess.CalledProcessError as e:
            logger.warning(f"[LipSync] P2a: 预处理失败，使用原始视频: {e.stderr[:200]}")
            return video_path

    # ─────────────────────────────────────────────────────────────────────────
    # P2c: MuseTalk (ComfyUI 后端 API) 接口
    # 通过静默拉起 ComfyUI 服务，投递工作流实现纯净的无 mmcv 依赖渲染
    # ─────────────────────────────────────────────────────────────────────────
    def _run_musetalk_via_comfyui(self, video_path: str, audio_path: str, output_path: str) -> str:
        """
        通过 ComfyUI API 隐形调用 MuseTalk。
        """
        logger.info(f"[LipSync] 启动 MuseTalk (ComfyUI 后端) 对口型 ...")
        import urllib.request
        import urllib.parse
        import json
        import time

        # 构建工作流 JSON 结构 (模拟 ComfyUI-MuseTalk 的最简工作流)
        # 注意: 实际的 client_id 和 node id 需要根据 ComfyUI-MuseTalk 规定的 json 模板进行编排。
        workflow = {
            "3": {
                "class_type": "LoadVideo",
                "inputs": {
                    "video": video_path,
                    "force_rate": 25
                }
            },
            "4": {
                "class_type": "LoadAudio",
                "inputs": {
                    "audio": audio_path
                }
            },
            "5": {
                "class_type": "MuseTalk",
                "inputs": {
                    "video": ["3", 0],
                    "audio": ["4", 0],
                    "bbox_shift": 0
                }
            },
            "6": {
                "class_type": "SaveVideo",
                "inputs": {
                    "filename_prefix": "musetalk_out",
                    "fps": 25,
                    "images": ["5", 0]
                }
            }
        }

        # 1. 尝试检测 ComfyUI 服务是否存活，如果不存活则需由外部确保它已运行
        try:
            req = urllib.request.Request("http://127.0.0.1:8188/system_stats")
            with urllib.request.urlopen(req) as response:
                pass
        except Exception:
            raise LipSyncError("ComfyUI 后端未运行，请在后台启动 `python main.py` 在端口 8188")

        # 2. 发送请求
        data = json.dumps({"prompt": workflow}).encode('utf-8')
        req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=data, headers={'Content-Type': 'application/json'})
        
        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read())
                prompt_id = result.get('prompt_id')
                logger.info(f"[MuseTalk] 任务已提交到 ComfyUI，Prompt ID: {prompt_id}")
        except Exception as e:
            raise LipSyncError(f"提交 MuseTalk 工作流到 ComfyUI 失败: {e}")

        # 3. 轮询等待完成 (简单轮询)
        output_found = False
        comfy_output_dir = Path("/Users/mac/project/interactive_video_pipeline/local_models/ComfyUI/output")
        start_time = time.time()
        
        while time.time() - start_time < 300: # 5 分钟超时
            # 轮询 output 目录看是否有新视频生成
            # 真实环境中应该用 websocket 监听进度
            time.sleep(3)
            # 简单实现：查找最新的 musetalk_out*.mp4
            videos = list(comfy_output_dir.glob("musetalk_out*.mp4"))
            if videos:
                # 按时间排序找到最新的
                latest = max(videos, key=lambda p: p.stat().st_mtime)
                # 检查该视频生成时间是否在我们提交任务之后
                if latest.stat().st_mtime > start_time:
                    shutil.copy(str(latest), output_path)
                    output_found = True
                    break

        if not output_found:
            raise LipSyncError("MuseTalk (ComfyUI) 生成超时或失败，未找到输出文件。")
            
        logger.success(f"[MuseTalk] 渲染完成: {output_path}")
        return output_path

    def _has_mps(self) -> bool:
        try:
            import torch
            return torch.backends.mps.is_available()
        except Exception:
            return False

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

    def run_wav2lip(self, video_path: str, audio_path: str, output_path: str,
                    preprocessed: bool = False):
        """
        执行 Wav2Lip 对口型推理 (动态劫持 MPS)
        P2a: preprocessed=True 时跳过预处理（已在上游处理过）
        P2d: 自动降分辨率策略，提升 AI 生成视频的人脸检测成功率
        """
        if not self.w2l_ckpt.exists():
            raise LipSyncError(f"Wav2Lip Checkpoint not found: {self.w2l_ckpt}")

        logger.info(f"[LipSync] Starting Wav2Lip for {video_path}")

        script_path = "inference.py"

        args = [
            "--checkpoint_path", str(self.w2l_ckpt.absolute()),
            "--face", str(Path(video_path).absolute()),
            "--audio", str(Path(audio_path).absolute()),
            "--outfile", str(Path(output_path).absolute()),
            "--face_det_batch_size", "1",
            "--wav2lip_batch_size", "1",
            # P2d: 增大人脸检测填充（pad），改善 AI 生成视频的检测率
            "--pads", "0", "20", "0", "0",
            "--nosmooth",  # AI 生成视频不需要平滑，反而会模糊
        ]

        patch_code = self._create_monkey_patch_script(script_path, args)

        runner_path = self.wav2lip_dir / "mps_runner.py"
        with open(runner_path, "w") as f:
            f.write(patch_code)

        import sys
        try:
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
            "-w", "0.9",
            "-i", str(Path(input_video).absolute()),
            "-o", str(Path(output_dir).absolute())
        ]

        patch_code = self._create_monkey_patch_script(script_path, args)

        hybrid_patch_header = """
import os
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
import torch
import torch.nn.functional as F

if hasattr(torch.backends, 'mps'):
    torch.backends.mps.is_available = lambda: True
    torch.backends.mps.is_built = lambda: True

    original_grid_sample = F.grid_sample
    def safe_grid_sample(input, grid, mode='bilinear', padding_mode='zeros', align_corners=None):
        is_mps = input.device.type == 'mps'
        if is_mps:
            input = input.to('cpu')
            grid = grid.to('cpu')
        res = original_grid_sample(input, grid, mode=mode, padding_mode=padding_mode, align_corners=align_corners)
        return res.to('mps') if is_mps else res
    F.grid_sample = safe_grid_sample

    original_interpolate = F.interpolate
    def safe_interpolate(input, size=None, scale_factor=None, mode='nearest', align_corners=None, recompute_scale_factor=None, antialias=False):
        is_mps = input.device.type == 'mps'
        if is_mps:
            input = input.to('cpu')
        kwargs = {}
        if size is not None: kwargs['size'] = size
        if scale_factor is not None: kwargs['scale_factor'] = scale_factor
        kwargs['mode'] = mode
        if align_corners is not None: kwargs['align_corners'] = align_corners
        if recompute_scale_factor is not None: kwargs['recompute_scale_factor'] = recompute_scale_factor
        kwargs['antialias'] = antialias
        res = original_interpolate(input, **kwargs)
        return res.to('mps') if is_mps else res
    F.interpolate = safe_interpolate
"""
        patch_code = hybrid_patch_header + patch_code

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

    # ─────────────────────────────────────────────────────────────────────────
    # P2b: 人脸检测失败时的音画合流兜底
    # 原来的逻辑：检测失败 → 直接复制原始视频（没有声音！）
    # 新的逻辑：检测失败 → FFmpeg 将音频强行合并进视频（保留配音，只是没有口型同步）
    # ─────────────────────────────────────────────────────────────────────────
    def _merge_audio_fallback(self, video_source: str, audio_source: str, final_out: str) -> str:
        """
        当对口型失败时，用 FFmpeg 将配音合并进视频（保留声音，无口型同步）。
        这比直接复制无声视频要好得多。
        """
        logger.warning(f"[LipSync] P2b: 对口型失败，退回到音画合流（无口型同步）")
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_source,
                "-i", audio_source,
                "-c:v", "copy",
                "-c:a", "aac",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",  # 以较短的流为准
                final_out
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            logger.success(f"[LipSync] P2b: 音画合流完成（有声音，无口型）: {final_out}")
            return final_out
        except subprocess.CalledProcessError as e:
            logger.error(f"[LipSync] P2b: 音画合流也失败了，复制原视频: {e}")
            shutil.copy(video_source, final_out)
            return final_out

    def generate_talking_head(self, video_source: str, audio_source: str, final_out: str):
        """
        完整的对口型 + 画质修复流水线。
        优化后的降级链路：
        MuseTalk（若启用）→ Wav2Lip（带预处理）→ FFmpeg音画合流（P2b）→ 复制原片
        """
        # 优先检查 MuseTalk 是否启用
        use_musetalk = os.getenv("USE_MUSETALK", "false").lower() == "true"
        if use_musetalk:
            try:
                return self._run_musetalk_via_comfyui(video_source, audio_source, final_out)
            except LipSyncError as e:
                logger.warning(f"[LipSync] MuseTalk 未可用，降级至 Wav2Lip: {e}")

        # 次优检查 LatentSync (已废弃/保留原逻辑)
        use_latentsync = os.getenv("USE_LATENTSYNC", "false").lower() == "true"
        if use_latentsync and hasattr(self, '_run_latentsync'):
            try:
                return self._run_latentsync(video_source, audio_source, final_out)
            except LipSyncError as e:
                logger.warning(f"[LipSync] LatentSync 未可用，降级至 Wav2Lip: {e}")

        temp_w2l_out = str(Path(final_out).with_suffix(".w2l.mp4"))

        # P2a: 对 AI 生成视频进行预处理，提升人脸检测率
        preprocessed_video = str(Path(final_out).with_suffix(".preprocessed.mp4"))
        enhanced_video = self._preprocess_ai_video_for_lipsync(video_source, preprocessed_video)

        try:
            self.run_wav2lip(enhanced_video, audio_source, temp_w2l_out, preprocessed=True)
        except LipSyncError as e:
            logger.error(f"[LipSync] Wav2Lip 失败（人脸检测不到）。P2b 音画合流降级: {e}")
            # P2b: 不再静默跳过，改为合并音频保留声音
            result = self._merge_audio_fallback(video_source, audio_source, final_out)
            # 清理预处理临时文件
            if Path(preprocessed_video).exists():
                Path(preprocessed_video).unlink(missing_ok=True)
            return result

        # 清理预处理临时文件
        if Path(preprocessed_video).exists() and preprocessed_video != video_source:
            Path(preprocessed_video).unlink(missing_ok=True)

        # CodeFormer 画质修复
        codeformer_weight = Path(
            "/Users/mac/project/interactive_video_pipeline/local_models/CodeFormer/weights/CodeFormer/codeformer.pth"
        )

        use_codeformer = os.getenv("USE_CODEFORMER", "true").lower() == "true"
        has_codeformer = (
            Path("local_models/CodeFormer/weights/CodeFormer").exists() or
            Path("local_models/CodeFormer").exists()
        )

        if (has_codeformer and use_codeformer
                and codeformer_weight.exists()
                and codeformer_weight.stat().st_size > 100 * 1024 * 1024):
            logger.info("[LipSync] CodeFormer weights detected, initiating face restoration...")
            restored_dir = str(Path(final_out).parent / "restored")
            self.run_codeformer(temp_w2l_out, restored_dir)

            try:
                restored_videos = list(Path(restored_dir).rglob("*.mp4"))
                if restored_videos:
                    best_video = sorted(restored_videos, key=os.path.getmtime)[-1]
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(best_video),
                        "-i", audio_source,
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "18",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        final_out
                    ], check=True, capture_output=True)
                    logger.success(f"[LipSync] CodeFormer 高清修复视频输出: {final_out}")
                    if Path(temp_w2l_out).exists():
                        os.remove(temp_w2l_out)
                    shutil.rmtree(restored_dir, ignore_errors=True)
                    return final_out
                else:
                    logger.error("[LipSync] CodeFormer 运行完毕但未找到 mp4，回退至 Wav2Lip 原片。")
            except Exception as e:
                logger.error(f"[LipSync] 移动 CodeFormer 结果失败: {e}，回退至 Wav2Lip 原片。")
        else:
            logger.warning("[LipSync] CodeFormer weights missing or incomplete! Bypassing face restoration.")

        # 安全兜底
        if not Path(final_out).exists():
            shutil.move(temp_w2l_out, final_out)
            logger.success(f"[LipSync] 已输出 Wav2Lip 原始视频（未进行画质修复）至 {final_out}")

        return final_out
