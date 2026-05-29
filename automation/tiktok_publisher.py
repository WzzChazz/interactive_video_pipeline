import time
from pathlib import Path
from loguru import logger
from automation.publisher import _create_browser, _try_selectors, _screenshot, PublisherError

def build_tiktok_caption(episode_title: str, branch_a: str, branch_b: str, episode_tag: str) -> str:
    """构建 TikTok 风格的英文文案，带极强互动引导"""
    return (
        f"{episode_title}\n\n"
        f"A deadly choice awaits...\n"
        f"🔴 A: {branch_a}\n"
        f"🔵 B: {branch_b}\n\n"
        f"👇 Drop 'A' or 'B' in the comments to decide their fate!\n\n"
        f"#InteractiveHorror #AIStory #{episode_tag} #ChooseYourOwnAdventure"
    )

def publish_to_tiktok(video_path: str, title: str, caption: str) -> str:
    """
    通过 DrissionPage 模拟 TikTok 网页端发布 (www.tiktok.com/creator-center/upload)。
    """
    logger.info("=========================================")
    logger.info("📱 [TIKTOK PUBLISHER STARTED]")
    logger.info(f"Target Video: {video_path}")
    
    if not Path(video_path).exists():
        raise PublisherError(f"Video file not found: {video_path}")
        
    page = None
    try:
        page = _create_browser()
        logger.info("Navigating to TikTok Creator Center upload page...")
        page.get('https://www.tiktok.com/creator-center/upload')
        time.sleep(5)
        
        if "login" in page.url.lower():
            raise PublisherError("Not logged in to TikTok. Please run login_matrix.py.")
            
        # 1. iframe 定位
        iframe = page.get_frame('@src*:creator-center/upload') or page
        
        # 2. 上传视频文件
        logger.info(f"Setting upload file interceptor for: {video_path}")
        page.set.upload_files(video_path)
        
        # 尝试通过 JS 强行触发隐藏的 file input
        js = """
        var inp = document.querySelector('input[type="file"]');
        if(inp) { inp.click(); return true; }
        return false;
        """
        if page.run_js(js):
            logger.info("Triggered file dialog via JS click.")
        else:
            logger.info("Direct input not found. Using file interceptor and clicking button...")
            upload_btn = _try_selectors(iframe, "text:选择视频", "text:Select video", "button:contains('Select')", ".upload-btn", timeout=10)
            if upload_btn:
                upload_btn.click(by_js=True)
            else:
                raise PublisherError("Could not find upload input or button on TikTok.")
            
        time.sleep(3)
        
        # 3. 立即填写文案 (抖音后台支持边传边写)
        caption_el = _try_selectors(iframe, ".public-DraftEditor-content", ".editor", "xpath://div[@contenteditable='true']", timeout=5)
        if caption_el:
            caption_el.click(by_js=True)
            time.sleep(1)
            iframe.run_js(
                """
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                document.execCommand('insertText', false, arguments[0]);
                """,
                caption
            )
            logger.info("Caption filled.")
        else:
            logger.warning("Could not find caption editor.")
            
        # 4. 勾选 AIGC 标识
        aigc_label = _try_selectors(iframe, "text:AI-generated content", "text:AI-generated", "text:由 AI 生成", "text:AI 生成", timeout=5)
        if aigc_label:
            try:
                switch = aigc_label.parent(2).ele("input[type='checkbox']") or aigc_label.parent(2).ele(".switch")
                if switch:
                    is_checked = switch.attr("checked") is not None or switch.attr("aria-checked") == "true"
                    if not is_checked:
                        switch.click(by_js=True)
                        logger.success("AIGC declaration enabled.")
            except Exception as e:
                logger.warning(f"Failed to toggle AIGC switch: {e}")
                
        # 5. 等待手动发布
        logger.info("TikTok：视频处理可能较慢，请等待 Post 按钮亮起...")
        try:
            input("\n>>> [手动操作] 请在浏览器中点击 'Post' 按钮发布。发完后在此终端按回车键继续... <<<\n")
        except EOFError:
            time.sleep(15)
            
        logger.success("✅ TikTok Video manually published/confirmed!")
            
        logger.info("=========================================")
        return "https://www.tiktok.com/profile"
        
    except Exception as e:
        logger.error(f"TikTok publish failed: {e}")
        if page: _screenshot(page, "tiktok_publish_error")
        raise
    finally:
        if page: 
            time.sleep(3)
            page.quit()
