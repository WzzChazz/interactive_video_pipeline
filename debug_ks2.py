from loguru import logger
from DrissionPage import WebPage

def debug_kuaishou():
    try:
        page = WebPage()
        
        divs = page.eles("xpath://div[@contenteditable='true']")
        logger.info(f"Found contenteditable divs: {len(divs)}")
        for i, div in enumerate(divs):
            try:
                logger.info(f"  [{i}] class={div.attr('class')}, placeholder={div.attr('placeholder')}, text={div.text[:20]}")
            except:
                pass
                
        # Find any element containing "标题" or "简介"
        logger.info("Elements containing '标题':")
        for el in page.eles("xpath://*[contains(text(), '标题')]"):
            logger.info(f"  tag={el.tag}, class={el.attr('class')}, text={el.text}")
            
        logger.info("Elements containing '简介':")
        for el in page.eles("xpath://*[contains(text(), '简介')]"):
            logger.info(f"  tag={el.tag}, class={el.attr('class')}, text={el.text}")
            
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == '__main__':
    debug_kuaishou()
