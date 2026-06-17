import time
from loguru import logger
from automation.publisher import _create_browser, _try_selectors

MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

def debug_edit_page():
    page = _create_browser()
    try:
        page.get(MANAGE_URL)
        time.sleep(5)
        
        # Click the first edit button
        edit_btn = _try_selectors(page, "text:编辑作品", timeout=5)
        if not edit_btn:
            logger.error("No edit button found.")
            return
            
        edit_btn.click(by_js=True)
        time.sleep(5)
        
        # Save HTML
        with open("storage/temp/debug_edit_page.html", "w", encoding="utf-8") as f:
            f.write(page.html)
        logger.success("Saved HTML to debug_edit_page.html")
        
    finally:
        page.quit()

if __name__ == "__main__":
    debug_edit_page()
