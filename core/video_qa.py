"""
渲染后自动自检(借鉴 OpenMontage 的 post-render review)。
出片后用 ffprobe/ffmpeg 自动检查成片,把"配音静音/音量过低/黑帧/音画不齐/分辨率错"
这类需要肉眼审的问题自动报出来,免得一帧帧看视频找 bug。纯本地,不烧 API。
"""
import json
import re
import subprocess
from pathlib import Path


def _ffprobe(path) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def _volume(path):
    """返回 (mean_dB, max_dB)。"""
    try:
        r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60)
        mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
        return (float(mean.group(1)) if mean else None, float(mx.group(1)) if mx else None)
    except Exception:
        return (None, None)


def _brightness(path, t) -> float | None:
    try:
        r = subprocess.run(
            ["ffmpeg", "-ss", str(max(0, t)), "-i", str(path), "-frames:v", "1",
             "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
            capture_output=True, text=True, timeout=20)
        m = re.search(r"YAVG=([\d.]+)", r.stderr)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def qa_check_video(video_path, expect_w: int = 1080, expect_h: int = 1920) -> list:
    """检查最终成片,返回问题列表 [{level, msg}]。"""
    path = Path(video_path)
    if not path.exists():
        return [{"level": "❌", "msg": "成片文件不存在"}]
    issues = []
    info = _ffprobe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    vdur = float(info.get("format", {}).get("duration", 0) or 0)

    w, h = v.get("width"), v.get("height")
    if w and (w, h) != (expect_w, expect_h):
        issues.append({"level": "⚠️", "msg": f"分辨率 {w}x{h} ≠ 预期 {expect_w}x{expect_h}"})

    if vdur and vdur < 8:
        issues.append({"level": "⚠️", "msg": f"视频偏短({vdur:.1f}s),完播体验可能太单薄"})

    if a is None:
        issues.append({"level": "❌", "msg": "成片没有音频轨(整条静音!)"})
    else:
        adur = float(a.get("duration", vdur) or vdur)
        if vdur and abs(adur - vdur) > 1.5:
            issues.append({"level": "⚠️", "msg": f"音画时长不齐:视频 {vdur:.1f}s / 音频 {adur:.1f}s"})
        mean, mx = _volume(path)
        if mean is not None and mean < -28:
            issues.append({"level": "⚠️", "msg": f"整体音量偏低(mean {mean:.1f}dB)——配音可能太小或被BGM埋"})
        if mx is not None and mx > -0.2:
            issues.append({"level": "⚠️", "msg": f"音量爆顶(max {mx:.1f}dB),可能破音"})

    # 黑帧/暗帧采样(治愈本应明亮，偏暗=异常)
    for label, t in [("开头", 0.3), ("中段", vdur / 2 if vdur else 5), ("结尾", vdur - 1 if vdur else 10)]:
        y = _brightness(path, t)
        if y is not None and y < 28:
            issues.append({"level": "⚠️", "msg": f"{label}({t:.0f}s)画面偏暗/黑帧(亮度 {y:.0f}，治愈应明亮)"})

    return issues


def qa_check_voices(audio_manifest: dict, scenes: list = None) -> list:
    """检查"有台词的分镜"配音是不是静音(TTS失败会退化成静音=-91dB,这次就踩过)。
    纯空镜(无台词)静音是正常的,不报。"""
    issues = []
    # 哪些分镜有台词
    has_dia = {}
    for sc in (scenes or []):
        has_dia[sc.get("scene_index")] = bool((sc.get("dialogue") or "").strip())
    for idx, am in (audio_manifest or {}).items():
        try:
            sidx = int(idx)
        except Exception:
            sidx = idx
        # 有 scenes 信息时,只查有台词的分镜
        if scenes is not None and not has_dia.get(sidx, True):
            continue
        vp = (am or {}).get("voice", "")
        if not vp or not Path(vp).exists():
            continue
        mean, _ = _volume(vp)
        if mean is not None and mean < -50:
            issues.append({"level": "⚠️", "msg": f"分镜{sidx}有台词却几乎静音(mean {mean:.0f}dB)——TTS可能失败了"})
    return issues


def format_qa_report(issues: list) -> str:
    if not issues:
        return "✅ 渲染后自检通过:音量正常、无黑帧、音画对齐、分辨率正确、配音有声。"
    lines = ["⚠️ 渲染后自检发现问题(建议处理后再发):"]
    for it in issues:
        lines.append(f"   {it['level']} {it['msg']}")
    return "\n".join(lines)
