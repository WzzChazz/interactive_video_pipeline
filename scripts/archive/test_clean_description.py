import time
import re
from loguru import logger
from automation.publisher import _create_browser, _try_selectors, _screenshot

def main():
    page = _create_browser()
    try:
        page.get("https://creator.douyin.com/creator-micro/content/manage")
        time.sleep(5)
        
        # Click the first "编辑作品" button
        edit_btn = _try_selectors(page, "text:编辑作品", "xpath://*[text()='编辑作品']")
        if not edit_btn:
            logger.error("No edit button found!")
            return
            
        edit_btn.click(by_js=True)
        time.sleep(5)
        _screenshot(page, "test_clean_edit_page")
        
        # Find description box
        desc_box = _try_selectors(
            page,
            ".zone-container",
            ".DraftEditor-root",
            "textarea[placeholder*='作品描述']",
            timeout=5
        )
        if not desc_box:
            logger.error("Description box not found")
            return
            
        current_text = desc_box.text
        logger.info(f"Current description: {current_text}")
        
        # Strip prefix
        clean_text = re.sub(r'^【.*?】\s*', '', current_text)
        logger.info(f"Clean description: {clean_text}")
        
        if clean_text != current_text:
            # Clear and input
            desc_box.clear()
            time.sleep(0.5)
            desc_box.input(clean_text)
            logger.success("Injected clean text!")
            _screenshot(page, "test_clean_after_inject")
        else:
            logger.info("No cleaning needed.")
            
    finally:
        time.sleep(2)
        page.quit()

if __name__ == "__main__":
    main()
