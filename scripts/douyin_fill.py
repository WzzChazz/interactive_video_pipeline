"""
scripts/douyin_fill.py — 抖音「只填不发」:自动装载当天(或最新)产出的成片+发布物料,
上传+填好标题文案后停在发布前,你审核后手动点【发布】。双平台日更的抖音腿。
用法: python3 scripts/douyin_fill.py            # 最新一集
      python3 scripts/douyin_fill.py CAPY_20260704   # 指定某集
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loguru import logger

import automation.publisher as pub
from config.settings import DOUYIN_CREATOR_URL, STORAGE_OUTPUT_DIR


def _latest_episode() -> Path:
    dirs = sorted([d for d in STORAGE_OUTPUT_DIR.glob("CAPY_2*") if d.is_dir()])
    if not dirs:
        raise SystemExit("storage/outputs/ 下没有 CAPY_日期 产出目录")
    return dirs[-1]


def _load_materials(ep_dir: Path) -> tuple[Path, str, str]:
    video = next(iter(ep_dir.glob("*_kuaishou.mp4")), None) or next(iter(ep_dir.glob("*.mp4")), None)
    if not video:
        raise SystemExit(f"{ep_dir} 里没有成片 mp4")
    txts = list(ep_dir.glob("*发布物料.txt"))
    title, caption = "", ""
    if txts:
        raw = txts[0].read_text(encoding="utf-8")
        m = re.search(r"标题:\s*\n(.+)", raw)
        title = (m.group(1).strip() if m else "")[:55]
        m = re.search(r"文案:\s*\n(.*?)(?:\n\s*BGM情绪:|\n\s*发布清单:|\Z)", raw, re.DOTALL)
        caption = (m.group(1).strip() if m else "")[:1000]
    if not title:
        title = "团团的晚安治愈"
    if not caption:
        caption = "「团团的晚安治愈」每晚更新\n#水豚 #治愈 #晚安"
    return video, title, caption


def main() -> int:
    ep_dir = (STORAGE_OUTPUT_DIR / sys.argv[1]) if len(sys.argv) > 1 else _latest_episode()
    video, title, caption = _load_materials(ep_dir)
    logger.info(f"装载: {video.name}\n标题: {title}\n文案:\n{caption}")

    pub.BROWSER_HEADLESS = False
    page = pub._create_browser()
    page.get("about:blank"); time.sleep(1)
    page.get(DOUYIN_CREATOR_URL); time.sleep(3)

    waited = 0
    while "login" in page.url.lower() or "passport" in page.url.lower():
        if waited == 0:
            logger.info(">>> 请在弹出的 Chrome 里扫码登录抖音,登录后自动继续 <<<")
        time.sleep(5); waited += 5
        if waited >= 300:
            logger.error("等登录超时(5分钟),请重跑"); return 1

    pub._upload_video(page, str(video)); time.sleep(2)
    pub._fill_caption(page, title, caption); time.sleep(1)
    logger.success("✅ 已填好,【没点发布】。请检查(封面/AIGC声明/文案)后自己点【发布】。浏览器保持15分钟。")
    time.sleep(900)
    return 0


if __name__ == "__main__":
    sys.exit(main())
