"""
core/teardown/downloader.py
===========================
批量下载抖音/快手分享链接 → mp4。复用已安装的 yt_dlp 模块（`python3 -m yt_dlp`），无需另装 CLI。

支持直接粘贴 App 的分享文本（含说明文字也行，自动抽链接）。抖音公开视频多数免 cookie；
快手/部分受限视频可加 --cookies-from-browser 用你已登录的浏览器会话绕过限制。
"""
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

_URL_RE = re.compile(r"https?://[^\s,，、　]+")
_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


def extract_urls(text: str) -> list[str]:
    """从一坨粘贴文本里抽出所有视频链接，去重保序、剥掉尾部标点。"""
    seen: set[str] = set()
    out: list[str] = []
    for u in _URL_RE.findall(text):
        u = u.rstrip("/。.,，、)）]】>")
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def read_links(source: str) -> list[str]:
    """source 可以是 links.txt 文件路径，也可以是直接粘贴的一段文本。"""
    p = Path(source)
    text = p.read_text(encoding="utf-8") if p.is_file() else source
    return extract_urls(text)


def download_videos(
    urls: list[str],
    out_dir: Path,
    cookies_from_browser: Optional[str] = None,
    timeout: int = 300,
) -> list[Path]:
    """逐条下载（逐条更好定位失败）。返回成功下载的文件路径列表。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    for i, url in enumerate(urls, 1):
        logger.info(f"[下载 {i}/{len(urls)}] {url}")
        if "kuaishou.com" in url or "kwai" in url:
            logger.warning("  ⚠ yt-dlp 不支持快手(无解析器)→ 请改用抖音链接,或对快手视频走屏录。跳过。")
            continue
        before = set(out_dir.glob("*"))
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist", "--no-warnings", "--quiet",
            "-f", "bv*+ba/b", "--merge-output-format", "mp4",
            "-o", str(out_dir / "%(title).60B_%(id)s.%(ext)s"),
        ]
        if cookies_from_browser:
            cmd += ["--cookies-from-browser", cookies_from_browser]
        cmd.append(url)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                logger.warning(f"  ✗ 失败: {(r.stderr or '').strip()[-300:]}")
                continue
            new = [p for p in out_dir.glob("*")
                   if p not in before and p.suffix.lower() in _VIDEO_EXT]
            for p in new:
                logger.success(f"  ✓ {p.name}")
            downloaded += new
        except subprocess.TimeoutExpired:
            logger.warning(f"  ✗ 超时(>{timeout}s): {url}")
        except Exception as e:
            logger.warning(f"  ✗ 异常: {e}")

    logger.info(f"下载完成 {len(downloaded)}/{len(urls)} 条 → {out_dir}")
    return downloaded
