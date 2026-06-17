"""
fix_douyin_titles.py
====================
自动修正抖音上已发布视频的标题。

操作目标（按发布时间倒序）：
  E17 [05/25 17:31]  第4集→第5集：白袍下的窥视
  E15 [05/25 13:45]  林悦再次来到储物间... → 第3集：储物间的低语
  E14 [?]            储物间的秘密 → 第2集：储物间的秘密
  E13 [05/22 17:23]  门后的凝视 → 第1集：门后的凝视

策略：
  1. 打开作品管理页，抓取所有作品的 item_id 和当前标题/描述片段
  2. 通过描述关键词匹配到对应集数
  3. 进入编辑页，用 JS 注入新标题
  4. 点击保存

E16 (陷阱中的真相，不适宜公开) 由 republish_douyin_e16.py 单独重新发布，此处不处理。
"""

import time
import json
from loguru import logger
from automation.publisher import _create_browser, _try_selectors, _screenshot, PublisherError

TITLE_FIX_MAP = [
    {
        "match_keywords": ["白袍下的窥视", "第5集：白袍下的窥视", "第4集：白袍"],
        "new_title": "第5集：白袍下的窥视",
        "episode": "E17",
    },
    {
        "match_keywords": ["储物间的低语", "第3集：储物间的低语", "第2集：储物"],
        "new_title": "第3集：储物间的低语",
        "episode": "E15",
    },
    {
        "match_keywords": ["最后通牒", "S01E014", "储物间的秘密", "第2集：储物间的秘密", "第1集：门后的凝视", "躲进储物间", "老式录音机"],
        "new_title": "第2集：储物间的秘密",
        "episode": "E14",
    },
    {
        "match_keywords": ["禁忌之门", "主角在废弃医院", "规则说不能开门", "门后的凝视", "第1集：禁忌之门"],
        "new_title": "第1集：禁忌之门",
        "episode": "E13",
    },
]

MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"


def _inject_title(page, new_title: str) -> bool:
    """用 React 兼容 JS 注入新标题到标题 input。"""
    js = f"""
    var inp = document.querySelector("input[placeholder*='标题']") ||
              document.querySelector("[data-e2e='video-title'] input") ||
              document.querySelector(".title-input input");
    if (inp) {{
        let lastValue = inp.value;
        inp.value = "{new_title}";
        let tracker = inp._valueTracker;
        if (tracker) tracker.setValue(lastValue);
        inp.dispatchEvent(new Event('input', {{bubbles: true}}));
        inp.dispatchEvent(new Event('change', {{bubbles: true}}));
        return inp.value;
    }}
    return null;
    """
    result = page.run_js(js)
    if result:
        logger.success(f"标题已注入：{result!r}")
        return True
    # 备用：直接用 DrissionPage input
    inp = _try_selectors(
        page,
        "@placeholder*:标题",
        "[data-e2e='video-title'] input",
        ".title-input input",
        timeout=5,
    )
    if inp:
        inp.clear()
        time.sleep(0.3)
        inp.input(new_title)
        logger.success(f"标题已通过 native input 写入：{new_title!r}")
        return True
    logger.error("未找到标题输入框！")
    return False


def _click_save(page) -> bool:
    """点击提交修改/保存/发布按钮。"""
    save_btn = _try_selectors(
        page,
        "xpath://button[text()='提交修改']",
        "xpath://button[contains(text(),'提交修改')]",
        "xpath://button[text()='保存']",
        "xpath://button[contains(text(),'保存')]",
        "xpath://button[text()='发布']",
        "xpath://button[contains(text(),'发布')]",
        "[data-e2e='publish-btn']",
        timeout=10,
    )
    if not save_btn:
        # JS 兜底：直接通过按钮文本内容点击
        result = page.run_js("""
        var btns = Array.from(document.querySelectorAll('button'));
        var btn = btns.find(b => b.textContent.trim() === '提交修改');
        if (btn) { btn.click(); return '提交修改'; }
        btn = btns.find(b => b.textContent.trim() === '保存');
        if (btn) { btn.click(); return '保存'; }
        return null;
        """)
        if result:
            logger.success(f"JS 兜底点击了 '{result}' 按钮。")
            return True
        logger.error("未找到保存/提交修改按钮！")
        return False
    save_btn.click(by_js=True)
    logger.info("已点击提交修改按钮。")
    return True


def fix_titles():
    logger.info("=" * 55)
    logger.info("🔧 抖音标题批量修正脚本启动")
    logger.info("=" * 55)

    page = None
    try:
        page = _create_browser()
        page.get(MANAGE_URL)
        time.sleep(5)
        _screenshot(page, "title_fix_start")

        if "login" in page.url.lower():
            raise PublisherError("未登录抖音创作者中心！")

        # 抓取所有作品卡片，提取 href（编辑链接）和描述文字
        logger.info("正在抓取作品列表...")
        
        # 等待作品卡片加载
        time.sleep(3)

        for fix in TITLE_FIX_MAP:
            episode  = fix["episode"]
            keywords = fix["match_keywords"]
            new_title = fix["new_title"]

            logger.info(f"\n── 处理 {episode}：{new_title} ──")

            # 回到作品管理页
            page.get(MANAGE_URL)
            time.sleep(4)

            # 在页面文本中查找匹配关键词的作品，然后找其同级的"编辑作品"按钮
            edit_btn = None
            for keyword in keywords:
                # 找包含关键词的文本元素，然后向上找到作品卡片容器，再找编辑按钮
                match_el = _try_selectors(
                    page,
                    f"text:{keyword}",
                    f"xpath://*[contains(text(), '{keyword}')]",
                    timeout=5,
                )
                if match_el:
                    logger.info(f"找到关键词 '{keyword}'，尝试定位编辑按钮...")
                    # 向上遍历父节点，找到包含"编辑作品"的容器
                    try:
                        # 尝试在该元素的祖先容器内找"编辑作品"
                        for depth in range(1, 8):
                            parent = match_el.parent(depth)
                            if parent:
                                edit_candidate = parent.ele("text:编辑作品", timeout=1)
                                if edit_candidate:
                                    edit_btn = edit_candidate
                                    logger.success(f"在第 {depth} 层父节点找到'编辑作品'按钮！")
                                    break
                        if edit_btn:
                            break
                    except Exception as e:
                        logger.debug(f"查找编辑按钮时异常：{e}")
                        continue

            if not edit_btn:
                logger.warning(f"⚠️ {episode}：未能通过关键词定位到'编辑作品'按钮，跳过。")
                logger.warning(f"  尝试的关键词：{keywords}")
                _screenshot(page, f"title_fix_{episode}_not_found")
                continue

            # 点击"编辑作品"
            edit_btn.click(by_js=True)
            logger.info("已点击'编辑作品'，等待编辑页加载...")
            time.sleep(5)
            _screenshot(page, f"title_fix_{episode}_edit_page")

            # 注入新标题
            if not _inject_title(page, new_title):
                logger.error(f"❌ {episode}：标题注入失败！")
                _screenshot(page, f"title_fix_{episode}_inject_fail")
                continue

            time.sleep(1)

            # 点击保存
            if _click_save(page):
                time.sleep(3)
                _screenshot(page, f"title_fix_{episode}_saved")
                logger.success(f"✅ {episode} 标题已修改为：{new_title}")
            else:
                logger.error(f"❌ {episode}：保存失败！")
                _screenshot(page, f"title_fix_{episode}_save_fail")

            time.sleep(3)

    except Exception as e:
        logger.error(f"脚本异常退出：{e}")
        if page:
            _screenshot(page, "title_fix_error")
        raise
    finally:
        if page:
            time.sleep(2)
            page.quit()

    logger.info("\n✅ 标题修正脚本执行完毕！")


if __name__ == "__main__":
    fix_titles()
