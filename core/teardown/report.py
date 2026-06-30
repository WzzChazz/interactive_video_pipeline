"""
core/teardown/report.py
=======================
DNA 列表 → 人类可读的对比 Markdown。把"爆款怎么做的"摆成一张可横向对比的表，
重点列就是我们要验证的几条：时长、前2秒切镜数、开场类型、语速。
"""
from typing import Optional


def _g(d: Optional[dict], *keys, default="—"):
    """安全取嵌套字段。"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur in (None, "") else cur


def render_report(dna_list: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# 爆款拆解 DNA 报告\n")
    lines.append(f"共 {len(dna_list)} 条。重点看：**时长 / 前2秒切镜数 / 开场类型 / 语速**——它们直接对应你的完播打法。\n")

    # 横向对比表
    lines.append("## 横向对比\n")
    header = ("| 文件 | 时长(s) | 镜头数 | 均镜长(s) | 首镜(s) | **前2s切镜** | 开场类型 | 抓拇指分 | 语速(字/s) | 配音字数 |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    lines.append(header)
    lines.append(sep)
    for d in dna_list:
        lines.append("| {f} | {dur} | {sc} | {avg} | {fs} | **{c2}** | {oo} | {ts} | {sr} | {cc} |".format(
            f=_g(d, "file"),
            dur=_g(d, "duration_sec"),
            sc=_g(d, "rhythm", "shot_count"),
            avg=_g(d, "rhythm", "avg_shot_sec"),
            fs=_g(d, "rhythm", "first_shot_sec"),
            c2=_g(d, "rhythm", "cuts_in_first_2s"),
            oo=_g(d, "hook", "opens_on"),
            ts=_g(d, "hook", "thumb_stopper_score"),
            sr=_g(d, "voice", "speech_rate_cps"),
            cc=_g(d, "voice", "char_count"),
        ))
    lines.append("")

    # 逐条明细
    lines.append("## 逐条明细\n")
    for d in dna_list:
        lines.append(f"### {_g(d, 'file')}\n")
        lines.append(f"- **画面**：{_g(d, 'hook', 'first_frame_desc')}（画风 {_g(d, 'hook', 'art_style')}；{_g(d, 'hook', 'character_setup')}）")
        lines.append(f"- **开场**：{_g(d, 'hook', 'opens_on')}　抓拇指分 {_g(d, 'hook', 'thumb_stopper_score')}/5")
        lines.append(f"- **屏幕字幕**：{_g(d, 'hook', 'on_screen_text')}")
        lines.append(f"- **节奏**：{_g(d, 'rhythm', 'shot_count')} 镜 / 均 {_g(d, 'rhythm', 'avg_shot_sec')}s，首镜 {_g(d, 'rhythm', 'first_shot_sec')}s，前2秒切 {_g(d, 'rhythm', 'cuts_in_first_2s')} 刀")
        track = d.get("caption_track")
        if track:
            cap_str = " ｜ ".join(f"{c.get('t')}s: {c.get('text')}" for c in track)
            lines.append(f"- **字幕文案轨**：{cap_str}")
        else:
            lines.append(f"- **字幕文案轨**：（无）")
        lines.append(f"- **配音/BGM全文**：{_g(d, 'voice', 'transcript')}")
        lines.append("")

    return "\n".join(lines)
