"""
core/ffmpeg_renderer.py — 无头自动化渲染器 (Headless FFmpeg Renderer)

职责：
1. 解决「音画时长不等」的经典死穴：自动计算差值，利用 tpad clone 冻结视频最后一帧。
2. 将混音后的 AAC 与对口型后的无声视频强力缝合。
3. 动态生成 .srt 字幕文件。
4. 利用 subtitles 滤镜进行硬编码烧录，输出标准的竖屏 1080x1920 高清短视频。
"""

import subprocess
import json
from pathlib import Path
from loguru import logger

class FFmpegRendererError(Exception):
    pass

class FFmpegRenderer:
    def __init__(self, work_dir: str):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        
    def _get_duration(self, filepath: Path) -> float:
        """精准提取媒体文件长度"""
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(filepath)
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return float(result.stdout.strip())
        except Exception as e:
            logger.error(f"Failed to get duration for {filepath}: {e}")
            return 0.0

    def _generate_srt(self, text: str, duration: float, srt_path: Path):
        """生成极简的单条字幕 SRT 文件 (支持多语言或仅中文)"""
        # 由于我们每一幕拆得很细，整句话可以贯穿整个场景
        
        def format_time(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            
        start_time = format_time(0.0)
        end_time = format_time(duration)
        
        # 净化可能带入的 SSML 或括号注释
        clean_text = text.replace("......", "...")
        if "(" in clean_text and ")" in clean_text:
            clean_text = clean_text.split(")", 1)[-1].strip()
            
        srt_content = f"1\n{start_time} --> {end_time}\n{clean_text}\n"
        
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
            
        return srt_path

    def render_scene(self, 
                     video_path: str, 
                     mixed_audio_path: str, 
                     dialogue_text: str, 
                     output_path: str):
        """
        核心缝合引擎：时空冻结 + 字幕烧录 + 最终压制
        """
        v_path = Path(video_path)
        a_path = Path(mixed_audio_path)
        out_path = Path(output_path)
        
        if not v_path.exists() or not a_path.exists():
            raise FFmpegRendererError("Video or Audio source missing for rendering.")
            
        v_dur = self._get_duration(v_path)
        a_dur = self._get_duration(a_path)
        
        logger.info(f"[Renderer] Syncing Scene - Video: {v_dur:.2f}s, Audio: {a_dur:.2f}s")
        
        srt_path = self.work_dir / f"{out_path.stem}.srt"
        self._generate_srt(dialogue_text, a_dur, srt_path)
        
        # 构建滤镜链
        filter_complex = ""
        
        if a_dur > v_dur + 0.1:
            # 视频短于音频：冻结最后一帧
            diff = a_dur - v_dur
            logger.warning(f"[Renderer] Video is shorter by {diff:.2f}s. Activating Freeze-Frame.")
            # tpad stop_mode=clone 复制最后一帧
            filter_complex += f"[0:v]tpad=stop_mode=clone:stop_duration={diff}[padded];"
            video_input = "[padded]"
        else:
            video_input = "[0:v]"
            
        # 标准化缩放，压暗背景，并打上字幕
        # force_original_aspect_ratio=decrease 确保不变形，周边黑边
        sub_filter = f"subtitles='{srt_path.absolute()}':force_style='FontName=PingFang SC,FontSize=18,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Alignment=2,MarginV=40'"
        
        filter_complex += f"{video_input}scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,{sub_filter}[final_v]"
        
        cmd = [
            "ffmpeg", "-y",
            "-i", str(v_path),
            "-i", str(a_path),
            "-filter_complex", filter_complex,
            "-map", "[final_v]",
            "-map", "1:a",
            "-t", str(a_dur),  # 严格对齐音频长度
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ]
        
        logger.debug(f"[Renderer] Execution CMD ready.")
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.success(f"[Renderer] Completed Scene Render: {out_path.name}")
        except subprocess.CalledProcessError as e:
            logger.error(f"[Renderer] FFmpeg Error: {e.stderr}")
            raise FFmpegRendererError(f"Rendering failed: {e.stderr}")
            
        return out_path
