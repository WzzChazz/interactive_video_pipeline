import time
import re
from loguru import logger
from automation.publisher import _create_browser, _try_selectors, _screenshot

EPISODES_TO_CLEAN = [
    {"keyword": "白袍下的窥视", "label": "E17"},
    {"keyword": "储物间的低语", "label": "E15"},
    {"keyword": "储物间的秘密", "label": "E14"},
    {"keyword": "禁忌之门", "label": "E13"},
    {"keyword": "门后的凝视", "label": "E13_alt"},
]

MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

def clean_description():
    logger.info("=" * 55)
    logger.info("🧹 抖音描述重复标题清理脚本启动")
    logger.info("=" * 55)

    page = None
    try:
        page = _create_browser()
        page.get(MANAGE_URL)
        time.sleep(5)

        for cfg in EPISODES_TO_CLEAN:
            keyword = cfg["keyword"]
            label = cfg["label"]
            
            logger.info(f"\n── 处理 {label}：{keyword} ──")
            page.get(MANAGE_URL)
            time.sleep(4)
            
            # Find the card containing this keyword
            match_el = _try_selectors(
                page,
                f"text:{keyword}",
                f"xpath://*[contains(text(), '{keyword}')]",
                timeout=5,
            )
            
            edit_btn = None
            if match_el:
                logger.info(f"找到关键词 '{keyword}'，尝试定位编辑按钮...")
                for depth in range(1, 8):
                    parent = match_el.parent(depth)
                    if parent:
                        try:
                            edit_candidate = parent.ele("text:编辑作品", timeout=1)
                            if edit_candidate:
                                edit_btn = edit_candidate
                                logger.success(f"在第 {depth} 层父节点找到'编辑作品'按钮！")
                                break
                        except:
                            pass

            if not edit_btn:
                logger.warning(f"⚠️ {label}：未能定位到'编辑作品'按钮，跳过。")
                continue

            edit_btn.click(by_js=True)
            logger.info("已点击'编辑作品'，等待加载...")
            time.sleep(5)
            
            # Find description box
            desc_box = _try_selectors(
                page,
                "[data-e2e='caption-editor']",
                ".caption-editor [contenteditable]",
                ".zone-container",
                ".DraftEditor-root",
                "textarea[placeholder*='作品描述']",
                timeout=5
            )
            
            if not desc_box:
                logger.error(f"❌ {label}：未找到作品描述框")
                page.run_js('window.onbeforeunload = null;')
                continue
                
            current_text = desc_box.text
            if not current_text:
                logger.info(f"{label}：描述为空，跳过。")
                page.run_js('window.onbeforeunload = null;')
                continue
                
            # Clean prefixes like 【第4集：白袍下的窥视】 or 【白袍下的窥视】
            clean_text = current_text
            while True:
                new_text = re.sub(r'^(?:【.*?】|第\d+集：.*?\||第\d+集：.*?)\s*', '', clean_text).strip()
                if new_text == clean_text:
                    break
                clean_text = new_text
                
            if clean_text != current_text.strip():
                logger.info(f"原描述：{current_text[:30]}...")
                logger.info(f"新描述：{clean_text[:30]}...")
                
                desc_box.clear()
                time.sleep(0.5)
                desc_box.input(clean_text)
                time.sleep(1)
                logger.success(f"✅ {label}：成功注入干净描述。")
                
                # Save
                save_btn = _try_selectors(
                    page,
                    "text:提交修改",
                    "text:保存",
                    "text:发布",
                    timeout=5,
                )
                if save_btn:
                    save_btn.click(by_js=True)
                    logger.info("✅ 点击了保存按钮")
                    time.sleep(4)
                else:
                    logger.error("❌ 未找到保存按钮！")
            else:
                logger.info(f"✅ {label}：描述很干净，无需修改。")
            
            # Clear onbeforeunload so we can navigate back
            page.run_js('window.onbeforeunload = null;')
            
    except Exception as e:
        logger.error(f"异常：{e}")
    finally:
        if page:
            page.quit()

if __name__ == "__main__":
    clean_description()
