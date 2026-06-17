from loguru import logger
from DrissionPage import WebPage

def debug_kuaishou():
    try:
        page = WebPage()
        logger.info(f"Current URL: {page.url}")
        logger.info(f"Page Title: {page.title}")
        
        # Check if title input exists
        title_inputs = page.eles("xpath://input[contains(@placeholder,'填写标题')]")
        logger.info(f"Found placeholder '填写标题' inputs: {len(title_inputs)}")
        
        title_classes = page.eles("xpath://input[contains(@class,'title')]")
        logger.info(f"Found class 'title' inputs: {len(title_classes)}")
        
        # Check all inputs
        inputs = page.eles("tag:input")
        logger.info("All inputs on page:")
        for i, inp in enumerate(inputs[:20]):
            try:
                logger.info(f"  [{i}] type={inp.attr('type')}, class={inp.attr('class')}, placeholder={inp.attr('placeholder')}")
            except:
                pass
                
        # Check text content for clues
        if page.ele("text:发布成功"):
            logger.info("Found '发布成功'")
        if page.ele("text:上传成功"):
            logger.info("Found '上传成功'")
            
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == '__main__':
    debug_kuaishou()
