"""
core/ffmpeg_compiler.py
=======================
FFmpeg 工业级无头合片工具。

流程：
  1. 按分镜序号排列所有视频片段，拼接成无声主轨。
  2. 将每段配音（voice MP3）对齐到对应视频片段的时间起点，混入主音轨。
  3. 将每段环境音效（sfx MP3）以低音量混入（-8dB），与配音叠加。
  4. 将台词文本转换为带时间戳的 SRT 字幕文件，硬烧录进视频。
  5. 输出最终 MP4 成品（H.264 + AAC，抖音推荐规格）。

所有 FFmpeg 调用均通过 subprocess 执行，实时捕获 stderr 并写入日志。
若 FFmpeg 退出码非 0 则抛出 FFmpegError，由 main.py 捕获并记录 FAILED 状态。
"""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Literal

from loguru import logger

from config.settings import (
    STORAGE_OUTPUT_DIR,
    STORAGE_TEMP_DIR,
    SUBTITLE_FONT_PATH,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    CLIP_DURATION_SECONDS,
)


class FFmpegError(Exception):
    pass


# ──────────────────────────────────────────────────────────
# FFmpeg 执行辅助
# ──────────────────────────────────────────────────────────

def _run_ffmpeg(args: list[str], step_name: str = "ffmpeg") -> None:
    """
    执行 FFmpeg 命令，捕获输出并记录日志。
    非零退出码抛出 FFmpegError。
    """
    cmd = ["ffmpeg", "-y"] + args
    logger.debug("[{}] CMD: {}", step_name, " ".join(cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        logger.error("[{}] FFmpeg STDERR:\n{}", step_name, result.stderr[-3000:])
        raise FFmpegError(
            f"FFmpeg step '{step_name}' failed (exit {result.returncode}). "
            f"See logs for details."
        )
    logger.debug("[{}] Done.", step_name)

def _get_audio_duration(path: str) -> float:
    """使用 ffprobe 获取音频文件的精确时长（秒）。"""
    import subprocess
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=10)
        return float(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to get audio duration for {path}: {e}")
        return 0.0


# ──────────────────────────────────────────────────────────
# 封面生成 (Cover Generation)
# ──────────────────────────────────────────────────────────

def generate_cover(image_path: Path, title: str, sub_title: str, output_path: Path) -> Path:
    """
    使用 FFmpeg 在首帧图片上叠加高饱和度黄色大字标题，生成爆款封面。
    """
    def escape_text(t):
        return str(t).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    
    safe_title = escape_text(title)
    
    main_title = safe_title
    if "：" in main_title:
        main_title = main_title.split("：")[-1]
    main_title = main_title[:10]
    
    # 动态副标题，如果为空则默认
    if not sub_title:
        sub_title = "点击揭开真相！"
    sub_title = escape_text(f"▶ {sub_title}")
    
    cover_filter = (
        # 1. 强制压暗 + 去饱和 + 冷蓝色调（不管原图白天还是夜晚，都变成深夜恐怖氛围）
        f"eq=brightness=-0.35:contrast=1.4:saturation=0.3,"
        # 2. 叠加一层冷蓝色 tint：给 R 通道减弱，给 B 通道加强
        f"curves=red='0/0 1/0.7':blue='0/0.1 1/1.0',"
        # 3. 强力四角暗角，营造窥视感
        f"vignette=PI/2.5,"
        # 4. 主标题（白字 + 血红阴影）
        f"drawtext=fontfile='/System/Library/Fonts/Supplemental/Songti.ttc':text='{main_title}':"
        f"x=(w-text_w)/2:y=h/2-250:fontsize=130:fontcolor=white:shadowcolor=red:shadowx=6:shadowy=6,"
        # 5. 副标题引导文案（明黄色）
        f"drawtext=fontfile='/System/Library/Fonts/Supplemental/Songti.ttc':text='{sub_title}':"
        f"x=(w-text_w)/2:y=h/2-80:fontsize=70:fontcolor=yellow:shadowcolor=black:shadowx=4:shadowy=4"
    )
    
    _run_ffmpeg([
        "-i", str(image_path),
        "-vf", cover_filter,
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path)
    ], step_name="generate_cover")
    
    return output_path


# ──────────────────────────────────────────────────────────
# SRT 字幕生成
# ──────────────────────────────────────────────────────────

def _seconds_to_srt_time(seconds: float) -> str:
    """将秒数转换为 SRT 时间格式 HH:MM:SS,mmm。"""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _parse_time_to_seconds(t_str: str) -> float:
    try:
        h, m, s = t_str.strip().split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except:
        return 0.0

def _generate_srt(scenes: list[dict], scene_durations: list[float], audio_manifest: dict[int, dict[str, str]], lang: str = "cn") -> str:
    """
    根据 Edge-TTS 产出的高精度 VTT 时间戳或回退到分镜时长，生成最终 SRT 字幕内容。
    """
    lines = []
    current_time = 0.0
    srt_idx = 1
    
    for i, scene in enumerate(scenes):
        start = current_time
        dur = scene_durations[i]
        end = start + dur - 0.1
        if end <= start:
            end = start + 0.1
        current_time += dur
        
        idx = scene["scene_index"]
        text  = scene.get("dialogue", "") if lang == "cn" else scene.get("english_dialogue", "")
        if not text:
            continue
            
        audio_map = audio_manifest.get(idx, {})
        voice_path = audio_map.get("voice", "")
        vtt_path = Path(voice_path).with_suffix(".vtt") if voice_path else None
        
        if lang == "cn" and vtt_path and vtt_path.exists():
            # 使用高精度 VTT 字幕
            vtt_content = vtt_path.read_text(encoding="utf-8")
            vtt_lines = vtt_content.splitlines()
            v_i = 0
            while v_i < len(vtt_lines):
                line = vtt_lines[v_i].strip()
                if "-->" in line:
                    start_str, end_str = line.split("-->")
                    start_s = _parse_time_to_seconds(start_str) + start
                    end_s = _parse_time_to_seconds(end_str) + start
                    
                    v_i += 1
                    sub_text = ""
                    while v_i < len(vtt_lines) and vtt_lines[v_i].strip() != "":
                        sub_text += vtt_lines[v_i].strip() + "\n"
                        v_i += 1
                    sub_text = sub_text.strip()
                    
                    # 避免字幕溢出当前分镜
                    if start_s > end:
                        continue
                    end_s = min(end_s, end)
                    
                    lines.append(str(srt_idx))
                    lines.append(f"{_seconds_to_srt_time(start_s)} --> {_seconds_to_srt_time(end_s)}")
                    lines.append(sub_text)
                    lines.append("")
                    srt_idx += 1
                else:
                    v_i += 1
        else:
            # Fallback 粗糙字幕
            lines.append(str(srt_idx))
            lines.append(f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}")
            lines.append(text)
            lines.append("")
            srt_idx += 1
            
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# 步骤 1：拼接无声主视频
# ──────────────────────────────────────────────────────────

def _concat_video_clips(
    clip_paths: list[str],
    scene_durations: list[float],
    output_path: Path,
    tmp_dir: Path,
) -> Path:
    """
    使用 FFmpeg concat demuxer 将多段视频拼接为一个无声 MP4，支持动态裁剪（去水）。
    """
    concat_list = tmp_dir / "concat_list.txt"
    with open(concat_list, "w") as f:
        for p, dur in zip(clip_paths, scene_durations):
            f.write(f"file '{p}'\n")
            f.write(f"outpoint {dur:.3f}\n")

    _run_ffmpeg([
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-vf", f"eq=brightness=-0.15:contrast=1.2:saturation=0.8,"
               f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
               f"drawbox=x=w-200:y=h-70:w=200:h=70:color=black:t=fill",
        "-an",
        str(output_path),
    ], step_name="concat_clips")
    return output_path


# ──────────────────────────────────────────────────────────
# 步骤 2：构建混合音轨
# ──────────────────────────────────────────────────────────

def _build_audio_track(
    scenes: list[dict],
    audio_manifest: dict[int, dict[str, str]],
    scene_durations: list[float],
    total_duration: float,
    theme_key: str,
    output_path: Path,
    tmp_dir: Path,
) -> Optional[Path]:
    """
    将各分镜的 voice + sfx 按时间偏移混入一条完整音轨。
    """
    voice_inputs: list[str] = []
    sfx_inputs: list[str] = []
    
    # 静音基底
    silence_path = tmp_dir / "silence.mp3"
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(total_duration),
        str(silence_path),
    ], step_name="gen_silence")

    input_args: list[str] = ["-i", str(silence_path)]
    filter_parts: list[str] = []
    input_idx = 0

    filter_parts.append(f"[{input_idx}]volume=0.001[v_base]")
    filter_parts.append(f"[{input_idx}]volume=0.001[s_base]")
    voice_inputs.append("[v_base]")
    sfx_inputs.append("[s_base]")
    input_idx += 1
    
    # 注入诺兰式 45Hz 心理压抑低频轰鸣 (Sub-Bass Infrasound Drone)
    drone_path = tmp_dir / "drone.wav"
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", "sine=frequency=45:beep_factor=0",
        "-t", str(total_duration),
        str(drone_path)
    ], step_name="gen_drone")
    
    input_args += ["-i", str(drone_path)]
    # 给低频轰鸣加一个恒定的压迫音量
    filter_parts.append(f"[{input_idx}]volume=0.8[drone_base]")
    sfx_inputs.append("[drone_base]")
    input_idx += 1

    from config.themes import THEMES
    reverb_filter = THEMES.get(theme_key, {}).get("audio_reverb_filter", "")

    current_offset = 0.0
    for i, scene in enumerate(scenes):
        idx       = scene["scene_index"]
        offset    = current_offset
        current_offset += scene_durations[i]
        audio_map = audio_manifest.get(idx, {})

        # 配音轨 (应用物理空间混响)
        voice_path = audio_map.get("voice", "")
        if voice_path and Path(voice_path).exists() and Path(voice_path).stat().st_size > 0:
            input_args += ["-i", voice_path]
            label = f"v{input_idx}"
            reverb_str = f",{reverb_filter}" if reverb_filter else ""
            filter_parts.append(
                f"[{input_idx}]adelay={int(offset * 1000)}|{int(offset * 1000)},"
                f"volume=1.5{reverb_str}[{label}]"
            )
            voice_inputs.append(f"[{label}]")
            input_idx += 1

        # 音效/BGM轨
        sfx_path = audio_map.get("sfx", "")
        if sfx_path and Path(sfx_path).exists() and Path(sfx_path).stat().st_size > 0:
            input_args += ["-i", sfx_path]
            label = f"s{input_idx}"
            # 初始音量提高一点，因为后面会有闪避压缩
            filter_parts.append(
                f"[{input_idx}]adelay={int(offset * 1000)}|{int(offset * 1000)},"
                f"volume=0.4,lowpass=f=6000[{label}]"
            )
            sfx_inputs.append(f"[{label}]")
            input_idx += 1

    # 如果都没有音频，抛出错误或静音处理
    if len(voice_inputs) == 1 and len(sfx_inputs) == 1:
        logger.warning("No audio files found, output will be muted.")
        return None

    # 混音总线路由 (Buses)
    n_voices = len(voice_inputs)
    n_sfx    = len(sfx_inputs)
    
    # 1. 混合所有人声
    filter_parts.append(f"{''.join(voice_inputs)}amix=inputs={n_voices}:duration=longest:dropout_transition=0:normalize=0[voice_raw]")
    
    # 影视级声学后处理：高通滤波(切低频去手机浑浊) + 空间混响(aecho)
    reverb = "aecho=0.8:0.3:50:0.3" if "hospital" in theme_key or "school" in theme_key else ""
    eq_filter = f"highpass=f=80{',' + reverb if reverb else ''}"
    
    # 将人声分成两轨：一轨用于最终输出 [voice_main]，一轨用于触发背景音闪避 [voice_sc]
    filter_parts.append(f"[voice_raw]{eq_filter},asplit=2[voice_main][voice_sc]")
    
    # 2. 混合所有环境音效 
    filter_parts.append(f"{''.join(sfx_inputs)}amix=inputs={n_sfx}:duration=longest:dropout_transition=0:normalize=0,volume=0.35[sfx_mix]")
    
    # 3. 智能音频闪避 (Audio Ducking via Sidechain Compression)
    # 当人声 [voice_sc] 出现时，环境音 [sfx_mix] 会被按照 ratio=5 压缩降低音量
    filter_parts.append(f"[sfx_mix][voice_sc]sidechaincompress=threshold=0.03:ratio=5:attack=10:release=300[ducked_sfx]")
    
    # 4. 最终混合人声和被闪避的环境音，并做母带响度标准化 (Loudnorm)
    filter_parts.append(
        f"[voice_main][ducked_sfx]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur=3.5[aout]"
    )

    filter_complex = ";".join(filter_parts)

    _run_ffmpeg(
        input_args + [
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "192k",
            str(output_path),
        ],
        step_name="build_audio_track",
    )
    return output_path


# ──────────────────────────────────────────────────────────
# 步骤 3：合并视频 + 音轨 + 字幕
# ──────────────────────────────────────────────────────────

def _mux_final_video(
    video_path: Path,
    audio_path: Optional[Path],
    srt_path: Path,
    output_path: Path,
    total_duration: float,
    next_branches: dict = None,
    banner_text: str = "",
    platform: str = "douyin",
) -> Path:
    """将拼接好的视频与混音轨、字幕进行最终封装，并叠加双轨专属特效。"""
    
    # 防风控滤镜：去重与随机时间戳
    import time
    import random
    anti_duplicate_filter = f"drawtext=fontfile='/System/Library/Fonts/Supplemental/Songti.ttc':text='{int(time.time())}':x=w-50:y=h-50:fontsize=1:fontcolor=black@0.01,"
    
    # 伪纪录片手持摄像机震动算法 (Handheld Camera Shake)
    # 裁切掉 3% 的边缘（等同于缩放放大防搬运），并用高频三角函数做不规则剧烈抖动
    shake_x = "iw*0.015+sin(t*13)*5"
    shake_y = "ih*0.015+cos(t*17)*5"
    camera_shake_filter = f"crop=iw*0.97:ih*0.97:{shake_x}:{shake_y},"
    
    # 物理电压光影不稳闪烁算法 (Dynamic Lighting Flicker)
    # 模拟坏掉的灯管，亮度随时间做极细微的三角函数跳动
    lighting_flicker_filter = "eq=brightness='0.02*sin(t*15*random(1))':gamma='1.0+0.05*cos(t*11*random(1))',"
    
    # 倒计时特效 (最后 5 秒)
    countdown_start = max(0, total_duration - 5)
    
    ab_text_filter = ""
    hook_filter = ""
    branch_a = ""
    branch_b = ""
    
    if next_branches:
        if platform == "douyin":
            branch_a = next_branches.get("douyin_branch_a") or next_branches.get("branch_a_teaser") or ""
            branch_b = next_branches.get("douyin_branch_b") or next_branches.get("branch_b_teaser") or ""
        elif platform == "kuaishou":
            branch_a = next_branches.get("kuaishou_branch_a") or next_branches.get("branch_a_teaser") or ""
            branch_b = next_branches.get("kuaishou_branch_b") or next_branches.get("branch_b_teaser") or ""
        else: # global
            branch_a = next_branches.get("english_branch_a_teaser") or ""
            branch_b = next_branches.get("english_branch_b_teaser") or ""

        # Mid-roll engagement prompt for Kuaishou (keep this!)
        if platform == "kuaishou":
            mid_time = max(2, total_duration / 2)
            mid_end = mid_time + 4
            ab_text_filter += (
                f",drawtext=fontfile='/System/Library/Fonts/Supplemental/Songti.ttc':text='如果你是她，你敢进去吗？':"
                f"enable='between(t,{mid_time},{mid_end})':"
                f"x=(w-text_w)/2:y=h/2-150:fontsize=64:fontcolor=yellow:shadowcolor=black:shadowx=3:shadowy=3:alpha='if(lt(t,{mid_time}+0.5),(t-{mid_time})/0.5,if(gt(t,{mid_end}-0.5),({mid_end}-t)/0.5,1))'"
            )
            
    # 全局视觉特效流组合
    font_spec = (
        f"{anti_duplicate_filter}"
        f"subtitles='{srt_path}':"
        f"force_style='FontName=PingFang SC,"
        f"FontSize=18,PrimaryColour=&HFFFFFF,"
        f"OutlineColour=&H000000,Outline=2,"
        f"Alignment=2,MarginV=40'"
        f"{ab_text_filter}"
    )

    input_args = ["-i", str(video_path)]
    if audio_path and audio_path.exists():
        input_args += ["-i", str(audio_path)]

    output_args = [
        "-vf", font_spec,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-profile:v", "high",
        "-level", "4.1",
        "-movflags", "+faststart",  # 首帧快速加载（抖音要求）
        "-pix_fmt", "yuv420p",
    ]

    if audio_path and audio_path.exists():
        pitch_shift = random.uniform(1.001, 1.005)
        output_args += [
            "-af", f"asetrate=44100*{pitch_shift},aresample=44100",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v",
            "-map", "1:a",
        ]
    else:
        output_args += ["-an"]

    # 1. 临时生成主视频
    tmp_main = output_path.with_name(f"tmp_main_{platform}.mp4")
    _run_ffmpeg(input_args + output_args + ["-y", str(tmp_main)], step_name="mux_main")
    
    # 2. 如果存在分支，则生成独立打字机片尾并拼接
    if branch_a and branch_b:
        from core.endcard_generator import generate_typewriter_endcard
        tmp_endcard = output_path.with_name(f"tmp_endcard_{platform}.mp4")
        generate_typewriter_endcard(branch_a, branch_b, tmp_endcard, fps=30, duration_sec=6.0, chars_per_sec=12.0)
        
        # 拼接
        concat_list = output_path.with_name(f"concat_list_final_{platform}.txt")
        with open(concat_list, "w") as f:
            f.write(f"file '{tmp_main}'\n")
            f.write(f"file '{tmp_endcard}'\n")
            
        _run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "copy",
            "-c:a", "aac",
            "-y",
            str(output_path)
        ], step_name="concat_final_endcard")
        
        # 清理
        tmp_main.unlink(missing_ok=True)
        tmp_endcard.unlink(missing_ok=True)
        concat_list.unlink(missing_ok=True)
    else:
        # 如果没有分支，直接重命名
        tmp_main.rename(output_path)

    return output_path


# ──────────────────────────────────────────────────────────
# 公开入口：compile_video
# ──────────────────────────────────────────────────────────

def compile_video(
    scenes: list[dict],
    clip_manifest: dict[int, str],
    audio_manifest: dict[int, dict[str, str]],
    episode_tag: str,
    theme_key: str = "hospital_horror",
    render_mode: Literal["all", "douyin_only", "kuaishou_only", "global_only"] = "all",
    next_branches: dict = None,
    banner_text: str = "",
    cover_teaser: str = "",
) -> dict[str, str]:
    """
    将所有资产合成为最终成品视频（双轨渲染）。
    """
    logger.info("Starting FFmpeg compilation for {} (Dual-Render)...", episode_tag)

    # 输出目录
    out_dir = STORAGE_OUTPUT_DIR / episode_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        dir=str(STORAGE_TEMP_DIR), prefix=f"{episode_tag}_"
    ) as tmp_str:
        tmp_dir = Path(tmp_str)

        # ── 1. 排序分镜，计算动态时长并注入高能倒叙 ───────────────────────
        sorted_scenes = sorted(scenes, key=lambda s: s["scene_index"])
        
        # [Cold-Open] 寻找倒数第二个高潮片段
        # [封面] 优先选 LLM 标注的最高潮分镜，若无标注则取最后一幕
        cover_path = out_dir / f"{episode_tag}_cover.jpg"
        climax_scene = next(
            (s for s in sorted_scenes if s.get("is_climax")),
            sorted_scenes[-1]  # 兜底：没标注就取最后一幕
        )
        cover_scene_idx = climax_scene["scene_index"]
        cover_frame_path = clip_manifest.get(cover_scene_idx, "")
        logger.info("Cover will use scene {} (is_climax={})", cover_scene_idx, climax_scene.get("is_climax", False))
        if cover_frame_path and Path(cover_frame_path).exists():
            generate_cover(cover_frame_path, banner_text, cover_teaser, cover_path)
        
        cold_open_dur = 1.5
        if len(sorted_scenes) >= 3:
            high_tension_idx = sorted_scenes[-2]["scene_index"]
            raw_path = clip_manifest.get(high_tension_idx)
            if raw_path and Path(raw_path).exists():
                cold_open_path = tmp_dir / "cold_open.mp4"
                _run_ffmpeg([
                    "-i", raw_path,
                    "-t", str(cold_open_dur),
                    "-vf", "hue=s=0,eq=contrast=1.5",
                    "-c:a", "copy",
                    "-y",
                    str(cold_open_path)
                ], step_name="gen_cold_open")
                
                # 注入时间轴最前方
                sorted_scenes.insert(0, {"scene_index": -1, "dialogue": "", "english_dialogue": ""})
                clip_manifest[-1] = str(cold_open_path)
                audio_manifest[-1] = {"sfx": ""} 
                logger.info("Cold-Open flash-forward inserted successfully.")

        clip_paths = []
        scene_durations = []
        for scene in sorted_scenes:
            idx  = scene["scene_index"]
            path = clip_manifest.get(idx, "")
            if not path or not Path(path).exists():
                raise FFmpegError(f"Scene {idx} clip not found: '{path}'")
            
            # --- START LIPSYNC INJECTION ---
            if idx != -1:
                from config.settings import USE_LIPSYNC
                audio_map = audio_manifest.get(idx, {})
                voice_path = audio_map.get("voice", "")
                
                # Check if lipsync is enabled, voice exists, and it's not a reaction shot (empty dialogue)
                if USE_LIPSYNC and voice_path and Path(voice_path).exists() and Path(voice_path).stat().st_size > 0 and scene.get("dialogue"):
                    logger.info("Applying LipSync + CodeFormer to Scene {}...", idx)
                    from core.lip_sync_engine import LipSyncEngine
                    lipsync = LipSyncEngine()
                    final_lipsync_path = tmp_dir / f"lipsync_scene_{idx:02d}.mp4"
                    path = lipsync.generate_talking_head(path, voice_path, str(final_lipsync_path))
            # --- END LIPSYNC INJECTION ---
            
            clip_paths.append(path)
            
            if idx == -1:
                dur = cold_open_dur
            else:
                # 读取配音时长进行动态去水裁剪
                audio_map = audio_manifest.get(idx, {})
                voice_path = audio_map.get("voice", "")
                if voice_path and Path(voice_path).exists() and Path(voice_path).stat().st_size > 0:
                    dur = _get_audio_duration(voice_path) + 0.3
                    dur = min(dur, float(CLIP_DURATION_SECONDS))
                    dur = max(dur, 2.0)
                    
                    # Wav2Lip trims the video to the audio length, so we must not ask FFmpeg to concat beyond the EOF
                    if USE_LIPSYNC and scene.get("dialogue"):
                        actual_vid_dur = _get_audio_duration(path)
                        # We use the actual duration minus a tiny safety margin to prevent concat demuxer EOF errors
                        dur = min(dur, actual_vid_dur - 0.03)
                        dur = max(dur, 0.5) # ensure minimum
                else:
                    dur = float(CLIP_DURATION_SECONDS)
            scene_durations.append(dur)

        total_duration = sum(scene_durations)

        # ── 2. 拼接主视频轨 ───────────────────────────────────
        raw_video = tmp_dir / "raw_video.mp4"
        _concat_video_clips(clip_paths, scene_durations, raw_video, tmp_dir)
        logger.info("Step 1/3 done: video concat ({} clips, {:.1f}s)", len(clip_paths), total_duration)

        # ── 3. 生成混合音轨 ───────────────────────────────────
        mixed_audio = tmp_dir / "mixed_audio.aac"
        audio_result = _build_audio_track(
            sorted_scenes, audio_manifest, scene_durations, total_duration, theme_key, mixed_audio, tmp_dir
        )
        logger.info("Step 2/3 done: audio track built (has_audio={})", audio_result is not None)

        # ── 4. 生成字幕并合片 (双轨专属渲染) ───────────────────────────────
        result_paths = {}
        
        # 中文字幕通用 (传递 audio_manifest 用于获取 VTT 时间轴)
        srt_cn_content = _generate_srt(sorted_scenes, scene_durations, audio_manifest, lang="cn")
        srt_cn_path = tmp_dir / "subs_cn.srt"
        with open(srt_cn_path, "w", encoding="utf-8") as f:
            f.write(srt_cn_content)
        
        if render_mode in ("all", "douyin_only"):
            final_douyin = out_dir / f"{episode_tag}_douyin.mp4"
            _mux_final_video(raw_video, mixed_audio if audio_result else None, srt_cn_path, final_douyin, total_duration, next_branches, banner_text=banner_text, platform="douyin")
            result_paths["douyin"] = str(final_douyin)
            
        if render_mode in ("all", "kuaishou_only"):
            final_ks = out_dir / f"{episode_tag}_kuaishou.mp4"
            _mux_final_video(raw_video, mixed_audio if audio_result else None, srt_cn_path, final_ks, total_duration, next_branches, banner_text=banner_text, platform="kuaishou")
            result_paths["kuaishou"] = str(final_ks)
            
        if render_mode in ("all", "global_only"):
            srt_en_content = _generate_srt(sorted_scenes, scene_durations, audio_manifest, lang="en")
            srt_en_path = tmp_dir / "subs_en.srt"
            with open(srt_en_path, "w", encoding="utf-8") as f:
                f.write(srt_en_content)

            final_global = out_dir / f"{episode_tag}_global.mp4"
            _mux_final_video(raw_video, mixed_audio if audio_result else None, srt_en_path, final_global, total_duration, next_branches, banner_text=banner_text, platform="global")
            result_paths["global"] = str(final_global)

    logger.success("FFmpeg Compilation COMPLETED: {}", result_paths)
    return result_paths
