"""
automation/scraper.py
=====================
抖音评论区 A/B 投票抓取模块（基于 DrissionPage）。

核心逻辑：
  1. 使用持久化 Chrome User Data 目录（保持抖音登录 Session，无需每次扫码）。
  2. 打开指定抖音视频页，自动滚动评论区直到加载完毕或达到上限。
  3. 正则匹配评论文本中的 "A"/"B" 关键词（大小写不敏感，含全角字符）。
  4. 权重统计：精确投票（"选A"/"投A"/"A"）> 普通出现（含字母A的其他词）。
  5. 返回胜出分支字符串 "A" 或 "B"（平局时返回 "A"，可配置）。
  6. 将投票详情写回 DB（vote_a_count / vote_b_count）。

DrissionPage 优势：
  - 基于真实 Chromium，绕过抖音的 JS 反爬检测。
  - 支持 user-data-dir 持久化登录态，无需每次人工扫码。
  - CSS/XPath 元素定位 + 原生滚动，稳定性远超 Selenium。
"""

import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import (
    BROWSER_USER_DATA_DIR,
    BROWSER_HEADLESS,
    DOUYIN_TARGET_VIDEO_URL,
)


# ──────────────────────────────────────────────────────────
# 投票关键词正则
# ──────────────────────────────────────────────────────────

# 高权重：明确投票意图（"选A" "投A" "支持A" "AAAAAA" 全大写连续）
_RE_VOTE_A_HIGH = re.compile(
    r"(?:选|投|支持|要|我选|我投)\s*[Aa\uFF21]"  # 选A / 投A / 支持A / 我选A
    r"|[Aa\uFF21]{2,}",                           # AAAA（连续，热情投票）
    re.IGNORECASE,
)
_RE_VOTE_B_HIGH = re.compile(
    r"(?:选|投|支持|要|我选|我投)\s*[Bb\uFF22]"
    r"|[Bb\uFF22]{2,}",
    re.IGNORECASE,
)

# 低权重：评论中单独出现字母 A/B（可能是正常用语，权重 0.5）
_RE_VOTE_A_LOW = re.compile(r"\b[Aa\uFF21]\b", re.IGNORECASE)
_RE_VOTE_B_LOW = re.compile(r"\b[Bb\uFF22]\b", re.IGNORECASE)

# 排除干扰：包含 A/B 但明显不是投票（如 "API" "NBA" "baby" 等）
_RE_NOISE = re.compile(
    r"\b(?:API|NBA|baby|abc|abcd|AB[Cs]?|above|about|able|basic|back)\b",
    re.IGNORECASE,
)


class ScraperError(Exception):
    pass


# ──────────────────────────────────────────────────────────
# 浏览器工厂
# ──────────────────────────────────────────────────────────

def _create_browser():
    """
    创建并配置 DrissionPage Chromium 浏览器实例。
    使用持久化 User Data 目录，保持抖音登录态。
    """
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except ImportError:
        raise ScraperError(
            "DrissionPage not installed. Run: pip install DrissionPage"
        )

    opts = ChromiumOptions()
    # 持久化登录 Session
    opts.set_user_data_path(BROWSER_USER_DATA_DIR)
    # 无头模式（生产环境建议开启，首次登录需关闭手动扫码）
    if BROWSER_HEADLESS:
        opts.headless(True)
    # 反检测基础配置
    opts.set_argument("--disable-blink-features=AutomationControlled")
    opts.set_argument("--no-sandbox")
    opts.set_argument("--disable-dev-shm-usage")
    # 移动端 UA（抖音移动版评论加载更稳定）
    opts.set_user_agent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )

    page = ChromiumPage(addr_or_opts=opts)
    return page


# ──────────────────────────────────────────────────────────
# 评论权重计算
# ──────────────────────────────────────────────────────────

def _score_comment(text: str) -> tuple[float, float]:
    """
    给单条评论计算 A/B 得分。

    Returns:
        (score_a, score_b)
        高权重匹配 = 1.0 分，低权重匹配 = 0.5 分，噪声词排除
    """
    # 去除已知噪声词
    clean = _RE_NOISE.sub("", text)

    score_a = score_b = 0.0

    if _RE_VOTE_A_HIGH.search(clean):
        score_a += 1.0
    elif _RE_VOTE_A_LOW.search(clean):
        score_a += 0.5

    if _RE_VOTE_B_HIGH.search(clean):
        score_b += 1.0
    elif _RE_VOTE_B_LOW.search(clean):
        score_b += 0.5

    return score_a, score_b


# ──────────────────────────────────────────────────────────
# 评论抓取核心
# ──────────────────────────────────────────────────────────

def _fetch_comments(page, video_url: str, max_scroll: int = 30) -> list[str]:
    """
    打开抖音视频页，滚动加载评论区，返回所有评论文本列表。

    Args:
        page:       DrissionPage 实例
        video_url:  抖音视频 URL（PC 端或移动端均可）
        max_scroll: 最大滚动次数（控制抓取深度，避免超时）

    Returns:
        评论文本列表（去重）
    """
    logger.info("Opening Douyin video: {}", video_url)
    page.get(video_url)
    time.sleep(3)  # 等待基础渲染

    # 等待评论区容器出现
    comment_selectors = [
        ".comment-item",
        "[data-e2e='comment-item']",
        ".CommentItem",
        ".commentItem",
    ]

    # 尝试关闭可能出现的登录弹窗
    try:
        close_btn = page.ele(".login-dialog-close", timeout=3)
        if close_btn:
            close_btn.click()
            time.sleep(1)
    except Exception:
        pass

    collected: set[str] = set()

    for scroll_i in range(max_scroll):
        # 尝试多个选择器定位评论元素
        comment_els = []
        for sel in comment_selectors:
            try:
                els = page.eles(sel)
                if els:
                    comment_els = els
                    break
            except Exception:
                continue

        # 提取文本
        for el in comment_els:
            try:
                text = el.text.strip()
                if text and len(text) > 0:
                    collected.add(text)
            except Exception:
                continue

        logger.debug(
            "Scroll {}/{}: {} unique comments so far",
            scroll_i + 1, max_scroll, len(collected)
        )

        # 滚动加载更多
        page.scroll.down(600)
        time.sleep(1.5)

        # 若连续两次滚动后评论数无增长则提前退出
        if scroll_i > 3 and len(collected) == _fetch_comments._last_count:
            logger.info("Comment loading stabilized at {}. Stopping scroll.", len(collected))
            break
        _fetch_comments._last_count = len(collected)

    logger.info("Total comments fetched: {}", len(collected))
    return list(collected)


# 静态变量用于增量检测
_fetch_comments._last_count = 0


# ──────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────

def scrape_votes(
    video_url: Optional[str] = None,
    max_scroll: int = 30,
) -> tuple[str, int, int]:
    """
    抓取指定抖音视频的评论投票，返回胜出分支及计票结果。

    Args:
        video_url:  目标视频 URL；为 None 时使用 settings.DOUYIN_TARGET_VIDEO_URL
        max_scroll: 评论区最大滚动次数

    Returns:
        (branch, vote_a_count, vote_b_count)
        branch = "A" 或 "B"

    Raises:
        ScraperError: 浏览器打开失败或无法定位评论区
    """
    url = video_url or DOUYIN_TARGET_VIDEO_URL
    if not url:
        raise ScraperError(
            "No video URL provided. Set DOUYIN_TARGET_VIDEO_URL in .env "
            "or pass video_url argument."
        )

    page = None
    try:
        page = _create_browser()
        comments = _fetch_comments(page, url, max_scroll=max_scroll)
    except ScraperError:
        raise
    except Exception as e:
        raise ScraperError(f"Browser error during scraping: {e}") from e
    finally:
        if page:
            try:
                page.quit()
            except Exception:
                pass

    if not comments:
        logger.warning("No comments found. Defaulting to branch A.")
        return "A", 0, 0

    # 计票
    total_a = 0.0
    total_b = 0.0
    for comment in comments:
        sa, sb = _score_comment(comment)
        total_a += sa
        total_b += sb

    vote_a = int(total_a)
    vote_b = int(total_b)

    # 决定胜者（平局 → A）
    branch = "A" if total_a >= total_b else "B"

    logger.success(
        "Vote result: A={} vs B={} → Branch {} wins.",
        vote_a, vote_b, branch
    )
    return branch, vote_a, vote_b


# ──────────────────────────────────────────────────────────
# 调试工具：仅抓取不入库
# ──────────────────────────────────────────────────────────

def debug_scrape(video_url: str, max_scroll: int = 10) -> None:
    """仅打印投票结果，不写 DB。用于调试和验证抓取逻辑。"""
    branch, a, b = scrape_votes(video_url, max_scroll)
    print(f"\n{'='*40}")
    print(f"  视频: {video_url}")
    print(f"  A 票: {a}")
    print(f"  B 票: {b}")
    print(f"  胜出: 分支 {branch}")
    print(f"{'='*40}\n")


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else DOUYIN_TARGET_VIDEO_URL
    debug_scrape(url, max_scroll=15)
