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
import redis
import json
import tempfile
from pathlib import Path
from typing import Optional, Literal
from functools import lru_cache

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


def _is_healing_theme(theme_key: str) -> bool:
    """治愈/非连载题材 → 关闭恐怖专属特效（低频轰鸣/倒叙去色/手持抖动/灯管闪烁/对口型/恐怖中插）。"""
    from config.themes import THEMES
    t = THEMES.get(theme_key, {})
    return (not t.get("is_serial", True)) or t.get("narration_mode") == "voiceover_offscreen"


def _pick_healing_bgm() -> Optional[str]:
    """从 sfx_library/bgm_healing/ 随机挑一首治愈 BGM；无文件则返回 None。"""
    import random
    bgm_dir = Path(__file__).resolve().parent.parent / "sfx_library" / "bgm_healing"
    if not bgm_dir.exists():
        return None
    files = [p for p in bgm_dir.iterdir()
             if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".flac")]
    return str(random.choice(files)) if files else None


# ──────────────────────────────────────────────────────────
# FFmpeg 执行辅助
# ──────────────────────────────────────────────────────────


def _publish_progress(episode_tag: str, step_name: str, pct: int):
    try:
        from config.settings import get_redis
        r = get_redis()
        r.publish("pipeline_progress", json.dumps({
            "active": True, "step_name": step_name, "step": 5, "total": 6, "pct": pct, "episode": episode_tag
        }))
    except:
        pass


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

@lru_cache(maxsize=1)
def _get_best_encoder() -> tuple[str, list[str]]:
    try:
        import subprocess
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                capture_output=True, text=True, timeout=10)
        stdout = result.stdout
        if "h264_videotoolbox" in stdout:
            return "h264_videotoolbox", ["-q:v", "60", "-allow_sw", "1"]
        if "h264_nvenc" in stdout:
            return "h264_nvenc", ["-preset", "p4", "-cq", "18"]
    except Exception as e:
        logger.warning(f"Failed to detect FFmpeg encoders: {e}")
    return "libx264", ["-preset", "fast", "-crf", "18"]


# ──────────────────────────────────────────────────────────
# 封面生成 (Cover Generation)
# ──────────────────────────────────────────────────────────

def generate_cover(image_path: Path, title: str, sub_title: str, output_path: Path, theme_key: str = "hospital_horror") -> Path:
    """
    使用 FFmpeg 在首帧图片上叠加大字标题，生成爆款封面。
    恐怖题材 → 冷蓝压暗 + 血红阴影；治愈题材 → 暖亮软萌 + 柔粉描边。
    """
    healing = _is_healing_theme(theme_key)

    def escape_text(t):
        return str(t).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    
    safe_title = escape_text(title)
    
    main_title = safe_title
    if "：" in main_title:
        main_title = main_title.split("：")[-1]
    main_title = main_title[:10]
    
    # 动态副标题，如果为空则默认
    if not sub_title:
        sub_title = "今天也要开心鸭～" if healing else "点击揭开真相！"
    sub_title = escape_text(f"▶ {sub_title}")

    from config.settings import get_chinese_font
    font_path = get_chinese_font() or "/System/Library/Fonts/Supplemental/Songti.ttc"

    if healing:
        cover_filter = (
            # 1. 微提亮 + 暖调 + 略增饱和（温暖治愈，绝不压暗）
            f"eq=brightness=0.04:contrast=1.05:saturation=1.18,"
            # 2. 暖色 tint：R 通道略提、B 通道略压
            f"curves=red='0/0.03 1/1.0':blue='0/0 1/0.92',"
            # 3. 主标题（白字 + 柔粉描边，圆润可爱）
            f"drawtext=fontfile='{font_path}':text='{main_title}':"
            f"x=(w-text_w)/2:y=h/2-250:fontsize=120:fontcolor=white:bordercolor=0xB18FFF:borderw=6,"
            # 4. 副标题引导文案（暖白字 + 浅棕柔影）
            f"drawtext=fontfile='{font_path}':text='{sub_title}':"
            f"x=(w-text_w)/2:y=h/2-90:fontsize=66:fontcolor=0xFFF6E5:shadowcolor=0x7A5230:shadowx=3:shadowy=3"
        )
    else:
        cover_filter = (
            # 1. 强制压暗 + 去饱和 + 冷蓝色调（不管原图白天还是夜晚，都变成深夜恐怖氛围）
            f"eq=brightness=-0.35:contrast=1.4:saturation=0.3,"
            # 2. 叠加一层冷蓝色 tint：给 R 通道减弱，给 B 通道加强
            f"curves=red='0/0 1/0.7':blue='0/0.1 1/1.0',"
            # 3. 强力四角暗角，营造窥视感
            f"vignette=PI/2.5,"
            # 4. 主标题（白字 + 血红阴影）
            f"drawtext=fontfile='{font_path}':text='{main_title}':"
            f"x=(w-text_w)/2:y=h/2-250:fontsize=130:fontcolor=white:shadowcolor=red:shadowx=6:shadowy=6,"
            # 5. 副标题引导文案（明黄色）
            f"drawtext=fontfile='{font_path}':text='{sub_title}':"
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
        # 同时支持 SRT（HH:MM:SS,mmm）和 VTT（HH:MM:SS.mmm）两种格式
        t_str = t_str.strip().replace(",", ".")
        h, m, s = t_str.split(':')
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
            
        # 直接用剧本原台词【整句】显示——不用 whisper 逐词转写（它会一个字一个字蹦，
        # 还会把"团团"听成"囤囤"、"动"听成"洞"）。整句 = 不蹦字、不错字、保证简体。
        import re as _re
        sub = text.strip()
        # 去掉开头的 "角色名：" 前缀（如"林溪："），字幕只显示台词本身
        sub = _re.sub(r'^[^：:，。!！?？\s]{1,8}[：:]\s*', '', sub)
        if lang == "cn":
            try:
                import zhconv
                sub = zhconv.convert(sub, "zh-cn")
            except Exception:
                pass
        lines.append(str(srt_idx))
        lines.append(f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}")
        lines.append(sub)
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

    vcodec, vargs = _get_best_encoder()
    _run_ffmpeg([
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c:v", vcodec,
        *vargs,
        "-vf", f"fps=30,setsar=1,crop=iw*0.9:ih*0.9:(iw-ow)/2:(ih-oh)/2,"
               f"eq=brightness=-0.05:contrast=1.15:saturation=0.8,"
               f"curves=red='0/0 1/0.95':blue='0/0.02 1/1.0',"
               f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black",
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
        "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
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
    
    # 注入诺兰式 45Hz 心理压抑低频轰鸣 (Sub-Bass Infrasound Drone) —— 仅恐怖/悬疑题材
    if not _is_healing_theme(theme_key):
        drone_path = tmp_dir / "drone.wav"
        _run_ffmpeg([
            "-f", "lavfi",
            "-i", "sine=frequency=45:beep_factor=0",
            "-t", str(total_duration),
            str(drone_path)
        ], step_name="gen_drone")

        input_args += ["-i", str(drone_path)]
        filter_parts.append(f"[{input_idx}]volume=0.8[drone_base]")
        sfx_inputs.append("[drone_base]")
        input_idx += 1

    # ── 治愈 BGM 音乐底层（capybara_healing 等治愈题材专用）────────────────
    if _is_healing_theme(theme_key):
        _bgm = _pick_healing_bgm()
        if _bgm:
            fade_out_st = max(0.0, total_duration - 1.5)
            input_args += ["-i", _bgm]
            filter_parts.append(
                f"[{input_idx}]aresample=48000,"
                f"aloop=loop=-1:size=2000000000,"      # 无限循环铺满全片
                f"atrim=duration={total_duration},"    # 截到正好一条时长
                f"afade=t=in:st=0:d=1,"                # 开头淡入
                f"afade=t=out:st={fade_out_st}:d=1.5," # 结尾淡出
                f"volume=0.12[bgm_heal]"               # 压低，绝不抢拟人对话（配音才是主角）
            )
            sfx_inputs.append("[bgm_heal]")
            input_idx += 1
            logger.info(f"[AudioTrack] 治愈 BGM 已接入: {Path(_bgm).name}")
        else:
            logger.warning("[AudioTrack] 治愈题材但 sfx_library/bgm_healing/ 无音乐文件，无 BGM")

    # ── ElevenLabs 整集环境音底层（免费账号最佳用法）──────────────────────
    # 调用缓存优先的生成器，第一次消耗 1-2 次 API 额度，之后命中缓存免费复用
    try:
        from core.audio_gen import generate_episode_ambient_sfx
        # 治愈题材跳过恐怖环境音，只用上面的 BGM 音乐
        ambient_path = None if _is_healing_theme(theme_key) else generate_episode_ambient_sfx(theme_key=theme_key, duration_seconds=40)
        if ambient_path and ambient_path.exists() and ambient_path.stat().st_size > 10_000:
            input_args += ["-i", str(ambient_path)]
            # 无限循环铺满整集，音量 -12dB（不抢人声主角地位）
            filter_parts.append(
                f"[{input_idx}]aresample=48000,"
                f"aloop=loop=-1:size=2000000000,"   # 无限循环
                f"atrim=duration={total_duration},"  # 截断到正好一集时长
                f"volume=0.25[ambient_el]"           # -12dB 底层音量
            )
            sfx_inputs.append("[ambient_el]")
            input_idx += 1
            logger.info(f"[AudioTrack] ElevenLabs 整集环境音已接入: {ambient_path.name}")
        else:
            logger.info("[AudioTrack] 无 ElevenLabs 环境音可用，仅使用 45Hz drone")
    except Exception as e:
        logger.warning(f"[AudioTrack] 加载整集环境音失败（不影响主流程）: {e}")
    # ────────────────────────────────────────────────────────────────────────


    from config.themes import THEMES
    reverb_filter = THEMES.get(theme_key, {}).get("audio_reverb_filter", "")

    current_offset = 0.0
    for i, scene in enumerate(scenes):
        idx       = scene["scene_index"]
        offset    = current_offset
        current_offset += scene_durations[i]
        audio_map = audio_manifest.get(idx, audio_manifest.get(str(idx), {}))

        # 配音轨 (应用物理空间混响)
        voice_path = audio_map.get("voice", "")
        if voice_path and Path(voice_path).exists() and Path(voice_path).stat().st_size > 0:
            input_args += ["-i", voice_path]
            label = f"v{input_idx}"
            reverb_str = f",{reverb_filter}" if reverb_filter else ""
            filter_parts.append(
                f"[{input_idx}]adelay={int(offset * 1000)}|{int(offset * 1000)},"
                f"volume=2.8{reverb_str}[{label}]"
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
            "-ar", "48000",
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
    last_image_path: Optional[str] = None,
    theme_key: str = "hospital_horror",
) -> Path:
    """将拼接好的视频与混音轨、字幕进行最终封装，并叠加双轨专属特效。"""

    healing = _is_healing_theme(theme_key)
    from config.settings import get_chinese_font
    font_path = get_chinese_font() or "/System/Library/Fonts/Supplemental/Songti.ttc"
    
    # 防风控滤镜：去重与随机时间戳
    import time
    import random
    anti_duplicate_filter = f"drawtext=fontfile='{font_path}':text='{int(time.time())}':x=w-50:y=h-50:fontsize=1:fontcolor=black@0.01,"
    
    # 伪纪录片手持摄像机震动算法 (Handheld Camera Shake)
    # 裁切掉 3% 的边缘（等同于缩放放大防搬运），并用高频三角函数做不规则剧烈抖动
    shake_x = "iw*0.015+sin(t*13)*5"
    shake_y = "ih*0.015+cos(t*17)*5"
    camera_shake_filter = f"crop=iw*0.97:ih*0.97:{shake_x}:{shake_y},"
    
    # 物理电压光影不稳闪烁算法 (Dynamic Lighting Flicker)
    # 模拟坏掉的灯管，亮度随时间做极细微的三角函数跳动
    lighting_flicker_filter = "eq=brightness='0.02*sin(t*15*random(1))':gamma='1.0+0.05*cos(t*11*random(1))',"

    # 治愈题材：关闭手持抖动与灯管闪烁（温柔稳定画面），仅保留 3% 防搬运裁切
    if healing:
        camera_shake_filter = "crop=iw*0.97:ih*0.97,"
        lighting_flicker_filter = ""
    
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

        # Mid-roll engagement prompt for Kuaishou (恐怖题材专用；治愈题材跳过这句吓人中插)
        if platform == "kuaishou" and not healing:
            mid_time = max(2, total_duration / 2)
            mid_end = mid_time + 4
            ab_text_filter += (
                f",drawtext=fontfile='{font_path}':text='如果你是她，你敢进去吗？':"
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

    vcodec, vargs = _get_best_encoder()
    # Need to override CRF for muxing if we are using libx264
    if vcodec == "libx264":
        vargs = ["-preset", "medium", "-crf", "20"]
        
    output_args = [
        "-vf", font_spec,
        "-c:v", vcodec,
        *vargs,
        "-profile:v", "high",
        "-level", "4.1",
        "-movflags", "+faststart",  # 首帧快速加载（抖音要求）
        "-pix_fmt", "yuv420p",
    ]

    if audio_path and audio_path.exists():
        pitch_shift = random.uniform(1.001, 1.005)
        output_args += [
            "-af", f"asetrate=48000*{pitch_shift},aresample=48000",
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
        
        # 【核心修复】：动态从刚生成的主视频末尾截取一帧作为片尾底图
        extracted_bg = output_path.with_name(f"extracted_bg_{platform}.jpg")
        try:
            # -sseof -0.5 表示截取视频倒数第 0.5 秒的画面，保证绝对是视频最后一幕的脸
            _run_ffmpeg([
                "-sseof", "-0.5",
                "-i", str(video_path),
                "-frames:v", "1",
                "-update", "1",
                "-y",
                str(extracted_bg)
            ], step_name="extract_last_frame")
            actual_bg_path = str(extracted_bg)
        except Exception as e:
            logger.warning(f"无法提取主视频尾帧: {e}，将回退使用默认/备用图")
            actual_bg_path = last_image_path

        tmp_endcard = output_path.with_name(f"tmp_endcard_{platform}.mp4")
        
        # 将真实截屏路径传入打字机引擎
        generate_typewriter_endcard(
            branch_a,
            branch_b,
            tmp_endcard,
            fps=30,
            duration_sec=6.0,
            chars_per_sec=12.0,
            background_image_path=actual_bg_path,
            healing=healing,
        )
        
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
        extracted_bg.unlink(missing_ok=True) # 清理截图
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
    image_manifest: dict[int, str],
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
            generate_cover(cover_frame_path, banner_text, cover_teaser, cover_path, theme_key=theme_key)
        
        cold_open_dur = 1.5
        if len(sorted_scenes) >= 3 and not _is_healing_theme(theme_key):
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

        last_image_path = None
        if sorted_scenes:
            last_scene_idx = sorted_scenes[-1]["scene_index"]
            last_image_path = image_manifest.get(last_scene_idx)
            logger.info(f"[DEBUG] last_scene_idx={last_scene_idx}, image_manifest keys={list(image_manifest.keys())}, last_image_path={last_image_path}")

        import concurrent.futures
        from config.settings import USE_LIPSYNC
        
        def _process_scene_lipsync(scene: dict) -> tuple[int, str]:
            s_idx = scene["scene_index"]
            s_path = clip_manifest.get(s_idx, "")
            if not s_path or not Path(s_path).exists():
                raise FFmpegError(f"Scene {s_idx} clip not found: '{s_path}'")
                
            a_map = audio_manifest.get(s_idx, audio_manifest.get(str(s_idx), {}))
            v_path = a_map.get("voice", "")
            
            # --- START VIDEO EXTENSION (PREVENT AUDIO OVERLAP) ---
            if v_path and Path(v_path).exists() and Path(v_path).stat().st_size > 0:
                v_dur = _get_audio_duration(v_path)
                vid_dur = _get_audio_duration(s_path) # Gets video container duration
                if v_dur > vid_dur:
                    logger.info("Scene {} audio ({:.2f}s) is longer than video ({:.2f}s). Extending video...", s_idx, v_dur, vid_dur)
                    extend_sec = v_dur - vid_dur + 0.3 # Add 0.3s safety margin
                    extended_path = tmp_dir / f"extended_scene_{s_idx:02d}.mp4"
                    try:
                        _run_ffmpeg([
                            "-i", str(s_path),
                            "-vf", f"tpad=stop_mode=clone:stop_duration={extend_sec}",
                            "-c:a", "copy",
                            "-y", str(extended_path)
                        ], step_name=f"extend_video_{s_idx}")
                        s_path = str(extended_path)
                    except Exception as e:
                        logger.warning("Failed to extend video for scene {}: {}", s_idx, e)
            # --- END VIDEO EXTENSION ---

            # --- START LIPSYNC INJECTION ---
            if s_idx != -1:
                from config.settings import USE_LIPSYNC
                
                if USE_LIPSYNC and not _is_healing_theme(theme_key) and v_path and Path(v_path).exists() and Path(v_path).stat().st_size > 0 and scene.get("dialogue"):
                    logger.info("Applying LipSync + CodeFormer to Scene {}...", s_idx)
                    _publish_progress(episode_tag, f"唇形增强 (片段 {s_idx}/6)", 45 + s_idx * 4)
                    try:
                        from core.lip_sync_engine import LipSyncEngine
                        ls_engine = LipSyncEngine()
                        f_lipsync_path = tmp_dir / f"lipsync_scene_{s_idx:02d}.mp4"
                        # If it succeeds, replace s_path with the lipsynced version
                        s_path = ls_engine.generate_talking_head(s_path, v_path, str(f_lipsync_path))
                    except Exception as e:
                        logger.error(f"LipSync failed for Scene {s_idx}, falling back to original video. Error: {e}")
            # --- END LIPSYNC INJECTION ---
            return s_idx, s_path

        processed_paths = {}
        # Concurrently process LipSync for all scenes
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future_to_idx = {executor.submit(_process_scene_lipsync, sc): sc["scene_index"] for sc in sorted_scenes}
            for future in concurrent.futures.as_completed(future_to_idx):
                s_idx, final_path = future.result()
                processed_paths[s_idx] = final_path

        clip_paths = []
        scene_durations = []
        for scene in sorted_scenes:
            idx  = scene["scene_index"]
            path = processed_paths[idx]
            
            clip_paths.append(path)
            
            if idx == -1:
                dur = cold_open_dur
            else:
                # 读取配音时长进行动态去水裁剪
                audio_map = audio_manifest.get(str(idx), audio_manifest.get(idx, {}))
                voice_path = audio_map.get("voice", "")
                if voice_path and Path(voice_path).exists() and Path(voice_path).stat().st_size > 0:
                    dur = _get_audio_duration(voice_path) + 0.3
                    dur = min(dur, float(CLIP_DURATION_SECONDS))
                    dur = max(dur, 2.0)
                    
                    # Wav2Lip trims the video to the audio length, so we must not ask FFmpeg to concat beyond the EOF.
                    # 治愈线不过 Wav2Lip（767行已门控跳过），这里若仍按"已裁剪"逻辑去截会错误截短片段 → 必须同样排除治愈线。
                    if USE_LIPSYNC and not _is_healing_theme(theme_key) and scene.get("dialogue"):
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
            _mux_final_video(raw_video, mixed_audio if audio_result else None, srt_cn_path, final_douyin, total_duration, next_branches, banner_text=banner_text, platform="douyin", last_image_path=last_image_path, theme_key=theme_key)
            result_paths["douyin"] = str(final_douyin)
            
        if render_mode in ("all", "kuaishou_only"):
            final_ks = out_dir / f"{episode_tag}_kuaishou.mp4"
            _mux_final_video(raw_video, mixed_audio if audio_result else None, srt_cn_path, final_ks, total_duration, next_branches, banner_text=banner_text, platform="kuaishou", last_image_path=last_image_path, theme_key=theme_key)
            result_paths["kuaishou"] = str(final_ks)
            
        if render_mode in ("all", "global_only"):
            srt_en_content = _generate_srt(sorted_scenes, scene_durations, audio_manifest, lang="en")
            srt_en_path = tmp_dir / "subs_en.srt"
            with open(srt_en_path, "w", encoding="utf-8") as f:
                f.write(srt_en_content)

            final_global = out_dir / f"{episode_tag}_global.mp4"
            _mux_final_video(raw_video, mixed_audio if audio_result else None, srt_en_path, final_global, total_duration, next_branches, banner_text=banner_text, platform="global", last_image_path=last_image_path, theme_key=theme_key)
            result_paths["global"] = str(final_global)

    logger.success("FFmpeg Compilation COMPLETED: {}", result_paths)
    return result_paths
