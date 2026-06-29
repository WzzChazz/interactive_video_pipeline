"""
core/teardown/analyzer.py
=========================
单条视频 → 结构化 DNA。三层，逐层可降级：
  1. 节奏层（纯 ffmpeg，永远可跑）：时长/镜头数/均镜长/前2秒切镜数/首镜时长
  2. 钩子层（视觉 LLM，复用 DashScope qwen-vl-max；无 key 自动跳过）：开场类型/首帧描述/画风/字幕
  3. 配音层（faster-whisper，复用项目已有；失败自动跳过）：转写/首句/字数/语速
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger


# ──────────────────────────────────────────────────────────
# 1. 节奏层（纯 ffmpeg / ffprobe）
# ──────────────────────────────────────────────────────────

def _ffprobe_meta(path: str) -> dict:
    """时长、fps、分辨率。"""
    out = {"duration_sec": None, "fps": None, "width": None, "height": None}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(r.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        out["width"] = stream.get("width")
        out["height"] = stream.get("height")
        rate = stream.get("r_frame_rate", "0/1")
        if "/" in rate:
            num, den = rate.split("/")
            out["fps"] = round(float(num) / float(den), 2) if float(den) else None
        dur = (data.get("format") or {}).get("duration")
        out["duration_sec"] = round(float(dur), 2) if dur else None
    except Exception as e:
        logger.warning(f"[Teardown] ffprobe 失败: {e}")
    return out


def _detect_cuts(path: str, threshold: float = 0.30) -> list[float]:
    """用 ffmpeg scene 检测切镜点，返回切镜时间戳列表（秒）。纯 ffmpeg，无额外依赖。"""
    cuts: list[float] = []
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", path, "-filter:v",
             f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        # showinfo 把通过 select 的帧信息打到 stderr，每个 pts_time 即一个切镜点
        for m in re.finditer(r"pts_time:([0-9.]+)", r.stderr or ""):
            t = float(m.group(1))
            if t > 0.05:  # 跳过第0帧本身
                cuts.append(round(t, 3))
    except Exception as e:
        logger.warning(f"[Teardown] 切镜检测失败: {e}")
    return sorted(set(cuts))


def _shot_stats(cuts: list[float], duration: Optional[float]) -> dict:
    """由切镜点推导镜头节奏指标。"""
    n_cuts = len(cuts)
    shot_count = n_cuts + 1  # N 个切点 → N+1 个镜头
    first_shot_sec = round(cuts[0], 2) if cuts else duration
    avg = round(duration / shot_count, 2) if duration and shot_count else None
    return {
        "shot_count": shot_count,
        "avg_shot_sec": avg,
        "first_shot_sec": first_shot_sec,                       # 第1镜停留时长（越短越快）
        "cuts_in_first_2s": sum(1 for t in cuts if t <= 2.0),   # ★ 直接验证"前2秒快切"论
        "cuts_in_first_3s": sum(1 for t in cuts if t <= 3.0),
        "cut_timestamps": cuts,
    }


def _extract_frame(path: str, t: float, out_path: Path) -> Optional[Path]:
    """抽取 t 秒处一帧。"""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", path,
             "-frames:v", "1", "-q:v", "3", str(out_path)],
            capture_output=True, timeout=30, check=True,
        )
        return out_path if out_path.exists() and out_path.stat().st_size > 0 else None
    except Exception as e:
        logger.warning(f"[Teardown] 抽帧 {t}s 失败: {e}")
        return None


# ──────────────────────────────────────────────────────────
# 2. 钩子层（视觉 LLM，复用 DashScope qwen-vl-max）
# ──────────────────────────────────────────────────────────

_VISION_PROMPT = """你是一个短视频「开场钩子」拆解专家。看这张视频首帧（约第0.5秒画面），严格只输出一个 JSON 对象，不要任何多余文字：
{
  "opens_on": "脸特写 | 动作中 | 文字/字幕 | 空镜/场景 | 产品 | 人物全身 之一",
  "first_frame_desc": "≤25字，客观描述首帧画面",
  "art_style": "≤15字，画风（如 日系动漫/写实/3D渲染/实拍）",
  "character_setup": "≤20字，画面里有谁、在干嘛",
  "on_screen_text": "屏幕上出现的文字/标题原文，没有则空字符串",
  "thumb_stopper_score": "1-5，这一帧在0.5秒内抓住拇指的强度，5=极强"
}"""


def _vision_hook(frame_path: Path) -> Optional[dict]:
    """首帧钩子分析。复用 image_gen 同款 DashScope qwen-vl-max；无 key/失败返回 None。"""
    try:
        from config.settings import DASHSCOPE_API_KEY
        if not DASHSCOPE_API_KEY:
            logger.info("[Teardown] 无 DASHSCOPE_API_KEY，跳过视觉钩子分析")
            return None
        import dashscope
        dashscope.api_key = DASHSCOPE_API_KEY
        resp = dashscope.MultiModalConversation.call(
            model="qwen-vl-max",
            messages=[{"role": "user", "content": [
                {"image": f"file://{frame_path.absolute()}"},
                {"text": _VISION_PROMPT},
            ]}],
        )
        if resp.status_code != 200:
            logger.warning(f"[Teardown] qwen-vl 错误: {resp.code} - {resp.message}")
            return None
        text = resp.output.choices[0].message.content[0]["text"]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0)) if m else {"raw": text.strip()}
    except Exception as e:
        logger.warning(f"[Teardown] 视觉钩子分析失败: {e}")
        return None


# ──────────────────────────────────────────────────────────
# 3. 配音层（faster-whisper，复用项目已有）
# ──────────────────────────────────────────────────────────

def _transcribe(path: str) -> Optional[dict]:
    """转写 + 语速。复用 whisper_aligner 的模型；失败返回 None。"""
    try:
        from core.whisper_aligner import _get_whisper_model
        model = _get_whisper_model()
        segments, info = model.transcribe(path, language="zh")
        segs = list(segments)
        full = "".join(s.text for s in segs).strip()
        if not segs:
            return {"transcript": "", "first_line": "", "char_count": 0, "speech_rate_cps": None}
        speech_dur = max(0.1, segs[-1].end - segs[0].start)
        chars = len(full.replace(" ", ""))
        return {
            "transcript": full,
            "first_line": segs[0].text.strip(),
            "char_count": chars,
            "speech_rate_cps": round(chars / speech_dur, 2),  # 字/秒，反映语速/信息密度
        }
    except Exception as e:
        logger.warning(f"[Teardown] 转写失败（可跳过）: {e}")
        return None


# ──────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────

def analyze_video(
    path: str,
    do_vision: bool = True,
    do_transcript: bool = True,
    work_dir: Optional[Path] = None,
) -> dict:
    """拆解单条视频 → DNA dict。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    logger.info(f"[Teardown] 拆解: {p.name}")

    work_dir = work_dir or Path(tempfile.gettempdir())
    work_dir.mkdir(parents=True, exist_ok=True)

    meta = _ffprobe_meta(str(p))
    cuts = _detect_cuts(str(p))
    rhythm = _shot_stats(cuts, meta.get("duration_sec"))

    dna: dict = {
        "file": p.name,
        "path": str(p),
        **meta,
        "rhythm": rhythm,
        "hook": None,
        "voice": None,
    }

    if do_vision:
        frame = _extract_frame(str(p), 0.5, work_dir / f"{p.stem}_hook.jpg")
        if frame:
            dna["hook"] = _vision_hook(frame)

    if do_transcript:
        dna["voice"] = _transcribe(str(p))

    return dna
