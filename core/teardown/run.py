"""
core/teardown/run.py
====================
CLI：对一批视频跑拆解，输出 dna.json + report.md。

用法（在 interactive_video_pipeline 目录下）：
    python -m core.teardown.run <视频文件或文件夹> [更多文件...] \
        [-o 输出目录] [--no-vision] [--no-transcript]

例：
    python -m core.teardown.run ./爆款样本/ -o ./teardown_out
    python -m core.teardown.run a.mp4 b.mp4 --no-transcript
"""
import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from core.teardown.analyzer import analyze_video
from core.teardown.report import render_report

_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(q for q in p.rglob("*") if q.suffix.lower() in _VIDEO_EXT)
        elif p.is_file() and p.suffix.lower() in _VIDEO_EXT:
            files.append(p)
        else:
            logger.warning(f"跳过（非视频/不存在）：{raw}")
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="爆款拆解器 MVP")
    ap.add_argument("inputs", nargs="+", help="视频文件或文件夹")
    ap.add_argument("-o", "--out", default="./teardown_out", help="输出目录")
    ap.add_argument("--no-vision", action="store_true", help="跳过视觉钩子分析（不调 DashScope）")
    ap.add_argument("--no-transcript", action="store_true", help="跳过 whisper 转写")
    args = ap.parse_args(argv)

    videos = _collect(args.inputs)
    if not videos:
        logger.error("没有找到可拆解的视频文件")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"待拆解 {len(videos)} 条 → 输出到 {out_dir}")

    dna_list: list[dict] = []
    for i, v in enumerate(videos, 1):
        logger.info(f"[{i}/{len(videos)}] {v.name}")
        try:
            dna_list.append(analyze_video(
                str(v),
                do_vision=not args.no_vision,
                do_transcript=not args.no_transcript,
                work_dir=out_dir / "_frames",
            ))
        except Exception as e:
            logger.error(f"拆解失败 {v.name}: {e}")

    (out_dir / "dna.json").write_text(json.dumps(dna_list, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(dna_list), encoding="utf-8")
    logger.success(f"完成：{out_dir/'dna.json'} + {out_dir/'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
