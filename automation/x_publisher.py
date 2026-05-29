import time
from pathlib import Path
from loguru import logger
from automation.publisher import _create_browser, _try_selectors, _screenshot, PublisherError

def build_x_tweet(episode_title: str, episode_tag: str) -> str:
    """构建推文文案"""
    return (
        f"🎬 {episode_title}\n\n"
        f"You are the director. What happens next?\n"
        f"Vote in the poll below! 👇\n\n"
        f"#AI #HorrorStory #{episode_tag}"
    )

def publish_to_x(video_path: str, tweet_text: str, poll_options: list[str]) -> str:
    """
    通过 DrissionPage 模拟 X (Twitter) 网页端发布。
    使用“推文串 (Thread)”机制：主推文带视频，第二条回复带原生投票框。
    """
    logger.info("=========================================")
    logger.info("🐦 [X/TWITTER PUBLISHER STARTED]")
    logger.info(f"Target Video: {video_path}")
    
    if not Path(video_path).exists():
        raise PublisherError(f"Video file not found: {video_path}")
        
    page = None
    try:
        page = _create_browser()
        page.get("https://x.com/compose/tweet")
        time.sleep(5)
        
        if "login" in page.url.lower():
            raise PublisherError("Not logged in to X. Please run login_matrix.py.")
            
        # 1. 填写主推文 (带视频)
        editor_1 = _try_selectors(page, "[data-testid='tweetTextarea_0']", ".DraftEditor-root", timeout=10)
        if not editor_1:
            raise PublisherError("Could not find tweet editor.")
        editor_1.click(by_js=True)
        time.sleep(0.5)
        # Twitter 的 draft.js 也可以用输入，但 input() 够用
        editor_1.input(tweet_text)
        logger.info("Main tweet text filled.")
        
        # 2. 上传视频
        logger.info("Setting upload interceptor for X...")
        page.set.upload_files(video_path)
        
        try:
            page.run_js("""document.querySelector('input[type="file"]').click()""")
            logger.info("Video attached to tweet via JS.")
        except Exception as e:
            raise PublisherError(f"Could not click media upload input via JS: {e}")
            
        time.sleep(2)
        
        # 3. 创建 Thread (推文串)，注入原生投票
        # 因为 Twitter 不允许同一个推文同时包含视频和投票
        add_tweet_btn = _try_selectors(page, "[data-testid='addTweetButton']", "[aria-label='Add another post']", timeout=5)
        if add_tweet_btn:
            add_tweet_btn.click(by_js=True)
            logger.info("Thread added for Poll.")
            time.sleep(1)
            
            # 找到第二个输入框并点击 Poll 按钮
            editor_2 = _try_selectors(page, "[data-testid='tweetTextarea_1']", timeout=5)
            if editor_2:
                editor_2.input("Cast your vote:")
                time.sleep(0.5)
                
                # 点击第二个推文区域内的 Poll 按钮
                # (我们需要找到属于第二个推文的 toolbar)
                poll_btn = page.ele("xpath:(//*[@data-testid='pollButton'])[last()]", timeout=3)
                if poll_btn:
                    poll_btn.click(by_js=True)
                    time.sleep(1)
                    
                    inputs = page.eles("[name^='Choice']")
                    if len(inputs) >= 2 and len(poll_options) >= 2:
                        inputs[0].input(poll_options[0][:25]) # Twitter 投票选项最多25字
                        inputs[1].input(poll_options[1][:25])
                        logger.success("Poll options injected to the thread!")
                else:
                    logger.warning("Could not find Poll button in the second tweet.")
        else:
            logger.warning("Could not find Add Thread button. Falling back to video-only tweet.")
            
        # 4. 等待手动发布
        logger.info("推特：视频处理较慢，请等待右下角 Post 按钮变蓝...")
        try:
            input("\n>>> [手动操作] 请在浏览器中点击 'Post' 按钮发推。发完后在此终端按回车键继续... <<<\n")
        except EOFError:
            time.sleep(15) # fallback if no terminal
            
        logger.success("✅ X Tweet manually published/confirmed!")
            
        time.sleep(5)
        logger.info("=========================================")
        return "https://x.com/home"
        
    except Exception as e:
        logger.error(f"X publish failed: {e}")
        if page: _screenshot(page, "x_publish_error")
        raise
    finally:
        if page:
            time.sleep(3)
            page.quit()
