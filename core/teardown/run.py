"""
core/teardown/run.py
====================
CLI：对一批视频跑拆解，输出 dna.json + report.md。

用法（在 interactive_video_pipeline 目录下）：
    python -m core.teardown.run [视频文件/文件夹/链接...] [--links 链接文件] \
        [-o 输出目录] [--cookies-from-browser chrome] [--no-vision] [--no-transcript]

例：
    # 已下载好的本地视频
    python -m core.teardown.run ./爆款样本/ -o ./teardown_out
    # 一条龙：粘贴抖音链接 → 自动下载 → 自动拆解
    python -m core.teardown.run https://v.douyin.com/xxxx/ https://v.douyin.com/yyyy/
    # 链接放文件里批量；快手受限视频借用浏览器登录态
    python -m core.teardown.run --links links.txt --cookies-from-browser chrome
"""
import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from core.teardown.analyzer import analyze_video
from core.teardown.report import render_report
from core.teardown.downloader import download_videos, read_links

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
    ap.add_argument("inputs", nargs="*", help="视频文件/文件夹，或直接粘贴 http 链接")
    ap.add_argument("--links", help="链接文件（每行一个或粘贴的分享文本，自动抽链接）")
    ap.add_argument("--cookies-from-browser", help="借用浏览器登录态下载受限视频，如 chrome / edge / safari")
    ap.add_argument("-o", "--out", default="./teardown_out", help="输出目录")
    ap.add_argument("--no-vision", action="store_true", help="跳过视觉钩子分析（不调 DashScope）")
    ap.add_argument("--no-captions", action="store_true", help="跳过全片字幕轨 OCR（省 API）")
    ap.add_argument("--no-transcript", action="store_true", help="跳过 whisper 转写")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 分流：positional 里 http 开头的当链接，其余当本地文件/文件夹
    url_inputs = [s for s in args.inputs if s.lower().startswith("http")]
    path_inputs = [s for s in args.inputs if not s.lower().startswith("http")]
    urls = list(url_inputs)
    if args.links:
        urls += read_links(args.links)

    videos = _collect(path_inputs)
    if urls:
        logger.info(f"待下载 {len(urls)} 条链接 → {out_dir/'_downloads'}")
        videos += download_videos(urls, out_dir / "_downloads",
                                  cookies_from_browser=args.cookies_from_browser)

    if not videos:
        logger.error("没有可拆解的视频（本地文件为空且链接均下载失败）")
        return 1

    logger.info(f"待拆解 {len(videos)} 条 → 输出到 {out_dir}")

    dna_list: list[dict] = []
    for i, v in enumerate(videos, 1):
        logger.info(f"[{i}/{len(videos)}] {v.name}")
        try:
            dna_list.append(analyze_video(
                str(v),
                do_vision=not args.no_vision,
                do_transcript=not args.no_transcript,
                do_captions=not args.no_captions,
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
