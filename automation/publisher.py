"""
automation/publisher.py
=======================
抖音创作者中心全自动视频发布模块（基于 DrissionPage）。

核心流程：
  1. 使用持久化 Chrome User Data 目录，保持创作者中心登录 Session。
  2. 打开抖音创作者中心上传页，上传本地 MP4 成品文件。
  3. 等待上传进度条完成（轮询检测）。
  4. 自动填写视频标题 + 带话题标签的文案。
  5. ✅ 核心要求：自动定位并勾选「人工智能生成内容（AIGC）」标识多选框。
  6. 点击发布按钮，等待成功提示。
  7. 抓取发布后的视频 URL，写回 DB（video_output_path / douyin_video_url）。

元素定位策略：
  - 优先使用 data-e2e 属性（抖音官方 E2E 测试标识，最稳定）。
  - 备用 aria-label / placeholder / text 文本匹配。
  - 关键步骤加入显式等待 + 截图留档（便于人工复查）。

⚠️ 注意：
  - 首次运行需人工打开浏览器登录（关闭 BROWSER_HEADLESS）。
  - 抖音创作者中心 UI 可能不定期更新，定位失败时修改下方 _SELECTORS 字典即可。
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import (
    BROWSER_USER_DATA_DIR,
    BROWSER_HEADLESS,
    DOUYIN_CREATOR_URL,
    STORAGE_TEMP_DIR,
)


# ──────────────────────────────────────────────────────────
# 元素定位配置（集中管理，方便 UI 更新时快速修改）
# ──────────────────────────────────────────────────────────

_SELECTORS = {
    # 上传区域（文件选择 input 或拖拽区）
    "upload_input":   "input[type='file']",
    "upload_area":    "[data-e2e='upload-drag-area']",

    # 上传进度
    "upload_progress": "[data-e2e='upload-progress']",
    "upload_done":     "[data-e2e='upload-success'], .upload-success, .success-icon",

    # 标题/文案输入框
    "title_input":    "[data-e2e='video-title'], .title-input input, input[placeholder*='标题'], input[class*='title']",
    "caption_editor": "[data-e2e='caption-editor'], .caption-editor [contenteditable]",

    # 人工智能生成标识相关（AIGC 声明）
    # 抖音创作者中心在「更多设置」或「高级设置」中有 AIGC 勾选框
    "aigc_section":   "[data-e2e='aigc-section'], .aigc-wrapper, .ai-generate-wrapper",
    "aigc_checkbox":  "[data-e2e='aigc-checkbox'] input, .aigc-checkbox input[type='checkbox']",
    "aigc_switch":    "[data-e2e='aigc-switch'], .ai-content-label",
    # 备用：通过文本内容定位
    "aigc_text":      "人工智能生成",

    # 更多设置折叠按钮
    "more_settings":  "[data-e2e='more-settings'], .more-settings-btn, .expand-btn",

    # 发布按钮
    "publish_btn":    "[data-e2e='publish-btn'], .publish-btn, button@text():发布, text=发布",

    # 发布成功提示
    "publish_success": "[data-e2e='publish-success'], .publish-success, .success-toast",
}

# 等待超时（秒）
_UPLOAD_TIMEOUT  = 600   # 文件上传最长 10 分钟（大文件）
_ACTION_TIMEOUT  = 30    # 元素操作超时
_PUBLISH_TIMEOUT = 60    # 发布响应超时


class PublisherError(Exception):
    pass


# ──────────────────────────────────────────────────────────
# 浏览器工厂（与 scraper 共享逻辑）
# ──────────────────────────────────────────────────────────

def _create_browser():
    """创建 DrissionPage 浏览器实例（桌面端 UA，适配创作者中心）。"""
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except ImportError:
        raise PublisherError(
            "DrissionPage not installed. Run: pip install DrissionPage"
        )

    opts = ChromiumOptions()
    opts.set_user_data_path(BROWSER_USER_DATA_DIR)
    if BROWSER_HEADLESS:
        opts.headless(True)
    opts.set_argument("--disable-blink-features=AutomationControlled")
    opts.set_argument("--no-sandbox")
    opts.set_argument("--disable-dev-shm-usage")
    # 桌面端 UA（创作者中心不支持移动端）
    opts.set_user_agent(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    # 禁用通知弹窗（避免干扰自动化）
    opts.set_pref("profile.default_content_setting_values.notifications", 2)

    page = ChromiumPage(addr_or_opts=opts)
    try:
        page.set.window.normal()
        page.set.window.max()
    except Exception:
        try:
            page.set.window.size(1920, 1080)
        except Exception:
            pass  # 忽略窗口大小设置错误，防止阻断主流程
    return page


# ──────────────────────────────────────────────────────────
# 操作辅助函数
# ──────────────────────────────────────────────────────────

def _wait_for_element(page, selector: str, timeout: int = _ACTION_TIMEOUT):
    """等待元素出现并返回，超时抛出 PublisherError。"""
    el = page.ele(selector, timeout=timeout)
    if not el:
        raise PublisherError(f"Element not found (timeout={timeout}s): {selector}")
    return el


def _try_selectors(page, *selectors: str, timeout: int = 10):
    """依次尝试多个选择器，返回第一个找到的元素，全部失败返回 None。"""
    for sel in selectors:
        try:
            el = page.ele(sel, timeout=timeout)
            if el:
                return el
        except Exception:
            continue
    return None


def _screenshot(page, name: str) -> None:
    """截图保存至 storage/temp/screenshots/ 便于人工复查。"""
    try:
        shot_dir = STORAGE_TEMP_DIR / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        path = shot_dir / f"{name}_{datetime.now().strftime('%H%M%S')}.png"
        page.get_screenshot(path=str(path))
        logger.debug("Screenshot saved: {}", path)
    except Exception as e:
        logger.warning("Screenshot failed: {}", e)


# ──────────────────────────────────────────────────────────
# Step 3.5：勾选西瓜/头条同步
# ──────────────────────────────────────────────────────────
def _check_xigua_sync(page) -> None:
    """勾选 '同步至西瓜视频/今日头条' 以获取中视频计划收益。"""
    logger.info("Locating Xigua sync checkbox...")
    sync_checkbox = _try_selectors(
        page,
        "text:同步至西瓜",
        "text:西瓜视频",
        "text:同步",
        timeout=5,
    )
    if sync_checkbox:
        logger.info("Found Xigua sync option, clicking it...")
        sync_checkbox.click(by_js=True)
    else:
        logger.warning("Could not find Xigua sync checkbox. It might be already checked or unavailable.")

# ──────────────────────────────────────────────────────────
# Step 3：AIGC 标识
# ──────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────
# Step 1：上传视频文件
# ──────────────────────────────────────────────────────────

def _upload_video(page, video_path: str) -> None:
    """
    定位文件上传 input，设置文件路径，等待上传完成。
    抖音创作者中心使用隐藏的 <input type="file">，
    DrissionPage 可直接 set.input_files() 绕过文件对话框。
    """
    logger.info("Uploading video: {}", video_path)

    if not Path(video_path).exists():
        raise PublisherError(f"Video file not found: {video_path}")

    # 尝试直接寻找上传 input
    upload_input = _try_selectors(
        page,
        _SELECTORS["upload_input"],
        "input[accept*='video']",
        "input[accept*='mp4']",
        timeout=20,
    )
    
    if upload_input:
        # 如果能找到，直接填入
        upload_input.input(video_path)
    else:
        # 如果找不到，使用 DrissionPage 的对话框拦截功能并点击“上传”按钮
        logger.warning("Upload input not found. Using file dialog interception...")
        
        # 可能是页面还没跳转到上传页
        if "upload" not in page.url:
            from config.settings import DOUYIN_CREATOR_URL
            page.get(DOUYIN_CREATOR_URL)
            time.sleep(4)

        upload_btn = _try_selectors(
            page,
            "text:上传视频",
            "text:点击上传",
            ".upload-btn",
            timeout=10,
        )
        if not upload_btn:
            raise PublisherError("Cannot locate upload button (上传视频) on creator page.")
            
        logger.info("Setting upload file interceptor for: {}", video_path)
        page.set.upload_files(str(video_path))
        
        logger.info("Clicking upload button to trigger file dialog...")
        upload_btn.click(by_js=True)
        
    logger.info("File path set, waiting for upload to complete...")
    _screenshot(page, "after_upload_start")

    # 轮询等待上传完成
    deadline = time.time() + _UPLOAD_TIMEOUT
    while time.time() < deadline:
        # 判断上传成功的标志（多种可能）
        success_el = _try_selectors(
            page,
            _SELECTORS["upload_done"],
            "[data-e2e='upload-complete']",
            ".upload-complete",
            ".video-preview",   # 上传成功后出现预览区
            "text:预览视频",      # 根据截图新增的稳定文本选择器
            "text:基础信息",
            "text:设置封面",
            timeout=3,
        )
        if success_el:
            logger.success("Video upload completed.")
            _screenshot(page, "upload_complete")
            return

        # 检查是否出现错误提示
        error_el = _try_selectors(
            page,
            ".upload-error",
            "[data-e2e='upload-error']",
            timeout=1,
        )
        if error_el:
            raise PublisherError(f"Upload failed: {error_el.text}")

        elapsed = int(time.time() - (deadline - _UPLOAD_TIMEOUT))
        logger.debug("Upload in progress... ({}s elapsed)", elapsed)
        time.sleep(5)

    raise PublisherError(f"Upload timed out after {_UPLOAD_TIMEOUT}s.")


# ──────────────────────────────────────────────────────────
# Step 2：填写标题和文案
# ──────────────────────────────────────────────────────────

def _fill_caption(page, title: str, caption: str) -> None:
    """
    填写视频标题和发布文案（含 #话题标签）。
    抖音文案区通常是 contenteditable div，使用 JavaScript 注入更可靠。
    """
    logger.info("Filling caption: title={:.30s}...", title)

    # 填写标题（部分 UI 版本有独立标题框）
    title_el = _try_selectors(
        page,
        "@placeholder*:标题",
        "@placeholder*:填写作品标题",
        "@placeholder*:好的标题",
        "@placeholder*:给作品加个标题",
        "@placeholder*:添加作品标题",
        "tag:input@@placeholder*:标题",
        _SELECTORS["title_input"],
        timeout=3,
    )
    if title_el:
        title_el.clear()
        title_el.input(title)
    else:
        # 终极 JS 注入大法
        js_code = f'''
        var inp = document.querySelector("input[placeholder*='标题']");
        if(inp) {{
            let lastValue = inp.value;
            inp.value = "{title}";
            let event = new Event("input", {{ bubbles: true }});
            event.simulated = true;
            let tracker = inp._valueTracker;
            if (tracker) {{
                tracker.setValue(lastValue);
            }}
            inp.dispatchEvent(event);
            return true;
        }}
        return false;
        '''
        page.run_js(js_code)
    
    time.sleep(0.5)

    # 填写文案（由于可能由 div/textarea 演变，使用最广义的 placeholder 和可编辑属性搜索）
    caption_el = _try_selectors(
        page,
        "text:添加作品简介",
        "text:填写作品简介",
        "@placeholder*:简介",
        "xpath://*[contains(@placeholder, '简介')]",
        "xpath://div[contains(@contenteditable, 'true')]",
        "xpath://div[contains(@contenteditable, 'plaintext-only')]",
        ".zone-container",
        ".editor-kit-editor",
        timeout=3,
    )
    if not caption_el:
        raise PublisherError("Cannot locate caption editor.")

    try:
        caption_el.click()  # 真实模拟点击，确保触发焦点
    except Exception:
        caption_el.click(by_js=True)
    time.sleep(0.5)

    # 清空现有内容并输入
    # 利用 JS 获取当前聚焦的实际输入框（解决定位到的是 placeholder span 的问题）
    page.run_js(
        """
        let target = arguments[0];
        if (!target.isContentEditable && target.tagName !== 'TEXTAREA' && target.tagName !== 'INPUT') {
            target = document.activeElement;
        }
        target.focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('delete', false, null);
        // 使用 document.execCommand 插入文本能完美触发 React/Vue 的事件绑定
        document.execCommand('insertText', false, arguments[1]);
        """,
        caption_el,
        caption
    )
    time.sleep(0.5)
    logger.success("Caption filled ({} chars).", len(caption))
    _screenshot(page, "caption_filled")

    # 尝试选择 AI 推荐封面
    _select_ai_cover(page)


def _select_ai_cover(page) -> None:
    """尝试点击抖音 AI 自动推荐的第一个封面"""
    logger.info("Attempting to select AI recommended cover...")
    try:
        # 等待推荐封面生成（有时需要几秒钟）
        ai_label = _try_selectors(
            page,
            "text:Ai智能推荐封面",
            "text:智能推荐封面",
            timeout=5
        )
        if ai_label:
            # 抖音前端通常把这几个封面图片放在这个 label 的下方或者紧接着的容器里
            # 为了防止结构变化，我们直接在 label 的父级或兄弟级里找第一张不是头像的 img
            # 或者是特定的封面 class
            cover_img = page.ele("xpath://div[contains(text(), '智能推荐封面')]/following-sibling::div//img", timeout=3)
            
            if cover_img:
                cover_img.click(by_js=True)
                logger.success("Successfully selected AI recommended cover.")
                time.sleep(1)
            else:
                logger.debug("AI cover images not found in DOM.")
        else:
            logger.debug("AI cover recommendation section not found.")
    except Exception as e:
        logger.debug(f"Failed to select AI cover (ignoring): {e}")


# ──────────────────────────────────────────────────────────
# Step 3：勾选"人工智能生成内容"标识（AIGC）
# ──────────────────────────────────────────────────────────

def _check_aigc_label(page) -> bool:
    """
    定位并勾选「人工智能生成内容」（AIGC）标识。

    策略（按优先级）：
      1. 通过 data-e2e 属性直接定位 checkbox input。
      2. 通过文本「人工智能生成」定位父容器，再找子 checkbox。
      3. 通过 aria-label 定位开关按钮。
      4. 若所有策略均失败，记录警告（不阻断发布流程）。

    Returns:
        True = 成功勾选，False = 未找到该元素（记录警告）
    """
    logger.info("Locating AIGC (AI-generated content) declaration...")

    # 首先尝试展开「更多设置」（AIGC 选项可能在折叠面板内）
    more_btn = _try_selectors(
        page,
        _SELECTORS["more_settings"],
        "text=更多设置",
        "text=高级设置",
        timeout=5,
    )
    if more_btn:
        logger.debug("Expanding 'more settings' panel...")
        more_btn.click(by_js=True)
        time.sleep(1)

    # 策略 1：data-e2e 直接定位
    checkbox = _try_selectors(
        page,
        _SELECTORS["aigc_checkbox"],
        "[data-e2e='ai-label-checkbox']",
        timeout=5,
    )
    if checkbox:
        _ensure_checked(page, checkbox)
        logger.success("AIGC checkbox checked (via data-e2e).")
        _screenshot(page, "aigc_checked")
        return True

    # 策略 2：通过文本「人工智能生成」定位，找父容器内的 checkbox
    aigc_text_el = _try_selectors(
        page,
        "text:人工智能生成",
        "text:AI生成",
        "text:AIGC",
        "text:由AI生成",
        timeout=5,
    )
    if aigc_text_el:
        # 向上查找包含 checkbox 的父元素
        try:
            parent = aigc_text_el.parent()
            for _ in range(4):  # 最多向上 4 层
                cb = parent.ele("input[type='checkbox']", timeout=1)
                if cb:
                    _ensure_checked(page, cb)
                    logger.success("AIGC checkbox checked (via text-parent).")
                    _screenshot(page, "aigc_checked")
                    return True
                parent = parent.parent()
        except Exception as e:
            logger.debug("AIGC parent search failed: {}", e)
            
        # 兜底：直接点击文本本身（通常绑定了 label 会触发勾选）
        try:
            aigc_text_el.click(by_js=True)
            logger.success("AIGC label text clicked directly as fallback.")
            time.sleep(0.5)
            _screenshot(page, "aigc_checked")
            return True
        except Exception:
            pass

    # 策略 3：通过 aria-label 定位开关
    switch = _try_selectors(
        page,
        "[aria-label*='人工智能']",
        "[aria-label*='AI生成']",
        "[aria-label*='aigc']",
        ".aigc-switch",
        timeout=5,
    )
    if switch:
        # 开关类组件：检查 aria-checked 状态
        checked = switch.attr("aria-checked")
        if checked != "true":
            switch.click(by_js=True)
            time.sleep(0.5)
        logger.success("AIGC switch activated (via aria-label).")
        _screenshot(page, "aigc_checked")
        return True

    # 所有策略失败
    logger.warning(
        "⚠️  AIGC declaration element NOT FOUND. "
        "Publishing without AI-generated label. "
        "Please manually verify and update _SELECTORS['aigc_checkbox']."
    )
    _screenshot(page, "aigc_not_found")
    return False


def _ensure_checked(page, checkbox_el) -> None:
    """确保 checkbox 处于选中状态（未选中则点击）。"""
    is_checked = (
        checkbox_el.attr("checked") is not None
        or checkbox_el.attr("aria-checked") == "true"
    )
    if not is_checked:
        checkbox_el.click(by_js=True)
        time.sleep(0.3)
        logger.debug("Checkbox clicked to check.")


# ──────────────────────────────────────────────────────────
# Step 4：点击发布
# ──────────────────────────────────────────────────────────

def _click_publish(page) -> str:
    """
    点击「发布」按钮，等待成功提示，返回发布后的视频页 URL（若可获取）。
    """
    logger.info("Clicking publish button...")
    _screenshot(page, "before_publish")

    # 兜底风控：检测是否有“重复上传/相似视频”警告弹窗，若有则点击“继续发布”
    warning_btn = _try_selectors(
        page,
        "text=继续发布",
        "text=仍然发布",
        "text=确认发布",
        timeout=2,
    )
    if warning_btn:
        logger.warning("Detected duplicate video warning, clicking continue...")
        warning_btn.click(by_js=True)
        time.sleep(1)

    publish_btn = _try_selectors(
        page,
        "button[data-e2e='publish-btn']",
        ".publish-btn",
        "button@@text():发布",
        "text=发布",
        "text=确认发布",
        "text=发表",
        "@text()=发布",
        timeout=10,
    )
    if not publish_btn:
        raise PublisherError("Cannot locate publish button.")

    publish_btn.click(by_js=True)
    logger.info("Publish button clicked, waiting for confirmation...")

    # 等待成功提示，同时处理可能弹出的原生 JS 确认框
    deadline = time.time() + _PUBLISH_TIMEOUT
    while time.time() < deadline:
        # 处理可能弹出的 native alert（如“视频仍在处理中，确认发布吗？”）
        try:
            alert_text = page.handle_alert(accept=True)
            if alert_text:
                logger.info(f"Handled native alert automatically: {alert_text}")
        except Exception:
            pass
            
        try:
            success_el = _try_selectors(
                page,
                _SELECTORS["publish_success"],
                "text=发布成功",
                "text=已发布",
                ".success",
                timeout=3,
            )
            if success_el:
                logger.success("Publish confirmed by success indicator.")
                _screenshot(page, "publish_success")
                break
        except Exception as e:
            if "提示框" in str(e) or "alert" in str(e).lower():
                logger.warning("Alert interrupted selector, continuing...")
                continue
            raise e
            
        time.sleep(2)
    else:
        # 超时但未检测到成功提示（可能 UI 已跳转）
        logger.warning("Publish success indicator not detected, assuming success.")

    # 尝试获取发布后的视频 URL
    video_url = ""
    try:
        current_url = page.url
        if "douyin.com" in current_url and "video" in current_url:
            video_url = current_url
        else:
            # 尝试从页面中提取视频链接
            link_el = _try_selectors(
                page,
                "[data-e2e='video-link']",
                "a[href*='/video/']",
                timeout=5,
            )
            if link_el:
                video_url = link_el.attr("href") or ""
    except Exception as e:
        logger.debug("Could not extract video URL: {}", e)

    logger.info("Published video URL: {}", video_url or "(not captured)")
    return video_url


# ──────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────

def _select_collection(page, collection_name: str) -> None:
    """尝试将视频加入到指定的合集中"""
    logger.info("Attempting to add video to collection: {}", collection_name)
    try:
        # 点击合集下拉框或添加按钮
        collection_dropdown = _try_selectors(
            page,
            "text:收录至合集",
            "text:选择合集",
            "text:添加到合集",
            "@placeholder*:选择合集",
            "xpath://div[contains(text(), '合集')]/following-sibling::div",
            timeout=3
        )
        if collection_dropdown:
            collection_dropdown.click(by_js=True)
            time.sleep(1)
            # 点击对应的合集名称
            target_collection = page.ele(f"text:{collection_name}", timeout=3)
            if target_collection:
                target_collection.click(by_js=True)
                logger.success("Successfully selected collection: {}", collection_name)
                time.sleep(0.5)
            else:
                logger.warning("Collection '{}' not found in dropdown. Closing dropdown.", collection_name)
                # 点击空白处或再次点击下拉框以关闭
                collection_dropdown.click(by_js=True)
        else:
            logger.warning("Collection dropdown not found on the page.")
    except Exception as e:
        logger.warning("Failed to select collection: {}", e)

def publish_to_douyin(
    video_path: str,
    title: str,
    caption: str,
    check_aigc: bool = True,
    branch_a_teaser: str = "",
    branch_b_teaser: str = "",
    collection_name: str = "",
) -> str:
    """
    全自动将视频发布至抖音创作者中心。

    Args:
        video_path: 本地 MP4 文件的绝对路径
        title:      视频标题（≤55 字）
        caption:    发布文案（含 #话题标签，≤2200 字）
        check_aigc: 是否自动勾选"人工智能生成内容"标识（默认 True）

    Returns:
        发布后的抖音视频页 URL（若无法抓取则返回空字符串）

    Raises:
        PublisherError: 任何关键步骤失败
    """
    logger.info("=" * 50)
    logger.info("Starting Douyin auto-publish: {}", Path(video_path).name)
    logger.info("=" * 50)

    page = None
    try:
        page = _create_browser()

        # 打开上传页前先跳转到空白页，强行摧毁上一集的 SPA 状态和可能的弹窗
        logger.info("Resetting browser state for new video...")
        page.get('about:blank')
        time.sleep(1)

        logger.info("Navigating to creator upload page...")
        page.get(DOUYIN_CREATOR_URL)
        time.sleep(3)

        # 检查是否需要登录
        if "login" in page.url.lower() or "passport" in page.url.lower():
            raise PublisherError(
                "Not logged in to Douyin Creator Center. "
                "Please manually login in the browser with BROWSER_HEADLESS=false, "
                "then run again. Login session will be preserved via User Data Dir."
            )

        _screenshot(page, "upload_page_open")

        # Step 1: 上传视频
        _upload_video(page, video_path)
        time.sleep(2)

        # Step 2: 填写文案
        _fill_caption(page, title, caption)
        time.sleep(1)

        # Step 2.5: 选择合集
        if collection_name:
            _select_collection(page, collection_name)
        time.sleep(1)

        # Step 2.8: 尝试注入原生投票贴纸 (Native Polling Sticker)
        sticker_success = False
        if branch_a_teaser and branch_b_teaser:
            sticker_success = _inject_polling_sticker(page, branch_a_teaser, branch_b_teaser)

        # Step 3: 勾选 AIGC 标识
        if check_aigc:
            _check_aigc_label(page)
            time.sleep(0.5)

        # Step 3.5: 勾选西瓜/头条同步 (薅中视频羊毛必备)
        _check_xigua_sync(page)
        time.sleep(0.5)

        # Step 4: 发布 (支持手动干预模式)
        from config.settings import PAUSE_BEFORE_PUBLISH
        if PAUSE_BEFORE_PUBLISH:
            import subprocess
            logger.warning("=" * 50)
            logger.warning("PAUSE_BEFORE_PUBLISH is ON.")
            logger.warning("Auto-opening the generated video for your review...")
            try:
                subprocess.run(["open", str(video_path)])
            except Exception as e:
                logger.error(f"Could not open video automatically: {e}")
            logger.warning("Please manually select the cover, edit the caption, and click the PUBLISH button in the browser.")
            logger.warning("Waiting for you... (Press Enter in this terminal to continue or quit script)")
            logger.warning("=" * 50)
            input("Press Enter after you have finished publishing...")
            video_url = "Manual Publish URL"
        else:
            video_url = _click_publish(page)

        # Step 5: 抢首评自动引导 (Comment Seeding)
        # 即使贴纸成功，依然发首评做双保险，只不过文案可以微调
        if branch_a_teaser and branch_b_teaser:
            _post_first_comment(page, branch_a_teaser, branch_b_teaser, is_fallback=not sticker_success)

        logger.success("Douyin auto-publish COMPLETED. URL: {}", video_url or "(unknown)")
        return video_url

    except PublisherError:
        _screenshot(page, "publish_error") if page else None
        raise
    except Exception as e:
        _screenshot(page, "unexpected_error") if page else None
        raise PublisherError(f"Unexpected error during publishing: {e}") from e
    finally:
        if page:
            try:
                # 发布完成后保持浏览器开启 3 秒，便于人工确认
                time.sleep(3)
                page.quit()
            except Exception:
                pass


def _inject_polling_sticker(page, branch_a: str, branch_b: str) -> bool:
    """
    UI自动化：尝试在发布界面注入抖音原生投票贴纸。
    因官方网页端常更新，若找不到 DOM，自动返回 False 触发降级机制。
    """
    logger.info("Attempting to inject native polling sticker...")
    try:
        # 1. 文本极简截断 (限制约 10 个字)
        def truncate(text: str) -> str:
            clean = text.replace("林悦", "").replace("决定", "").replace("选择", "").replace("，", "").replace("。", "")
            return clean[:10]
            
        opt_a = truncate(branch_a)
        opt_b = truncate(branch_b)
        
        # 2. 定位「互动组件/贴纸」菜单
        sticker_tab = page.ele("text:互动贴纸") or page.ele("text:添加组件") or page.ele("text:投票")
        if not sticker_tab:
            logger.warning("No interactive sticker tab found on UI. Will fallback entirely to comment seeding.")
            return False
            
        sticker_tab.click()
        time.sleep(1)
        
        # 3. 选中投票组件
        vote_btn = page.ele("text:投票组件") or page.ele("text:发起投票")
        if vote_btn:
            vote_btn.click()
            time.sleep(1)
            
        # 4. 填写选项
        opt_inputs = page.eles("xpath://input[contains(@placeholder, '选项')]")
        if len(opt_inputs) >= 2:
            opt_inputs[0].clear()
            opt_inputs[0].input(opt_a)
            opt_inputs[1].clear()
            opt_inputs[1].input(opt_b)
            
            # 问题框
            q_input = page.ele("xpath://input[contains(@placeholder, '问题')]")
            if q_input:
                q_input.input("下一步怎么办？")
                
            # 确认按钮
            confirm = page.ele("text:确认添加") or page.ele("text:确定")
            if confirm:
                confirm.click()
                logger.success(f"Native polling sticker injected! [A:{opt_a} | B:{opt_b}]")
                time.sleep(1)
                return True
                
        logger.warning("Could not fill polling sticker inputs. Falling back.")
        return False
        
    except Exception as e:
        logger.warning(f"Failed to inject native sticker: {e}")
        return False


def _post_first_comment(page, branch_a: str, branch_b: str, is_fallback: bool = True):
    """
    跳转到作品管理页，给最新发布的一个视频添加“抢首评”引导，极大拉升互动率。
    is_fallback 表示贴纸是否失败。
    """
    logger.info("Navigating to content management page to post first comment...")
    try:
        # 1. 跳转到作品管理页
        page.get("https://creator.douyin.com/creator-micro/content/manage")
        page.wait.load_start()
        time.sleep(4)
        
        # 2. 找到最新视频的“评论”按钮
        comment_btn = page.ele("text:评论")
        if not comment_btn:
            logger.warning("Could not find comment button on management page. Seeding failed.")
            return
            
        comment_btn.click()
        time.sleep(2)
        
        # 3. 在弹出的评论框或跳转后的评论区输入内容
        textarea = page.ele("tag:textarea") or page.ele("xpath://textarea")
        if not textarea:
            logger.warning("Could not find comment textarea. Seeding failed.")
            return
            
        if is_fallback:
            comment_text = f"【投票专区】赞这条代表选 A：{branch_a}\n回复这条代表选 B：{branch_b}\n👇下一集的生死由你决定！"
        else:
            comment_text = f"你选A还是B？除了点击视频上的贴纸，也可以在评论区告诉我你的选择！\nA: {branch_a}\nB: {branch_b}"
            
        textarea.input(comment_text)
        time.sleep(1)
        
        # 4. 点击发送/发表评论
        send_btn = page.ele("text:发表评论") or page.ele("text:发送") or page.ele("text:发布")
        if send_btn:
            send_btn.click()
            logger.success("Successfully posted the first comment for traffic optimization!")
            time.sleep(2)
        else:
            logger.warning("Could not find send comment button.")
            
    except Exception as e:
        logger.error(f"Failed to post first comment: {e}")


# ──────────────────────────────────────────────────────────
# 文案生成辅助
# ──────────────────────────────────────────────────────────

def build_douyin_caption(
    episode_summary: str,
    branch_a_teaser: str,
    branch_b_teaser: str,
    episode_tag: str,
    extra_tags: Optional[list[str]] = None,
    title: str = "",
) -> str:
    """
    根据剧本数据自动构建抖音发布文案。
    格式：【标题】+ 剧情简介 + 投票引导 + 话题标签

    Args:
        episode_summary:  Claude 生成的本集简介
        branch_a_teaser:  下一集 A 分支预告
        branch_b_teaser:  下一集 B 分支预告
        episode_tag:      如 'S01E003'
        extra_tags:       额外话题标签列表
        title:            视频标题，注入文案头部作为双保险

    Returns:
        完整文案字符串
    """
    default_tags = ["#短剧", "#互动剧", "#AI短剧", "#剧情", "#每日更新"]
    tags = (extra_tags or []) + default_tags
    tags_str = " ".join(tags)

    title_str = f"【{title}】\n" if title else ""

    caption = (
        f"{title_str}{episode_summary}\n\n"
        f"💬 下一集剧情，你来决定！\n"
        f"👉 评论区回复「A」：{branch_a_teaser}\n"
        f"👉 评论区回复「B」：{branch_b_teaser}\n\n"
        f"📺 {episode_tag} | 明日更新\n\n"
        f"{tags_str}"
    )
    return caption[:2200]  # 抖音文案上限 2200 字

# ──────────────────────────────────────────────────────────
# 评论区辅助 (自动发表并置顶互动选项)
# ──────────────────────────────────────────────────────────

def post_interactive_comment(video_url: str, branch_a: str, branch_b: str) -> None:
    """
    在视频发布后，自动进入该视频页，发表并尽量置顶互动选项评论。
    """
    if not video_url or not video_url.startswith("http"):
        logger.warning("Invalid video URL for commenting.")
        return

    logger.info("Auto-posting interactive comment to: {}", video_url)
    page = _create_browser()
    try:
        page.get(video_url)
        time.sleep(5)  # 等待视频页加载
        
        # 定位评论输入框
        input_el = _try_selectors(
            page,
            "[data-e2e='comment-input']",
            ".comment-input",
            "text=留下你的精彩评论吧",
            timeout=10
        )
        if not input_el:
            logger.warning("Could not find comment input box.")
            return
            
        comment_text = (
            f"🚨 剧情由你决定！\n"
            f"选【A】{branch_a}\n"
            f"选【B】{branch_b}\n"
            f"直接在这里回复你的选择！"
        )
        input_el.input(comment_text)
        time.sleep(1)
        
        # 定位发送按钮
        send_btn = _try_selectors(
            page,
            "[data-e2e='comment-publish']",
            "text=发送",
            ".submit-btn",
            timeout=3
        )
        if send_btn:
            send_btn.click(by_js=True)
            logger.success("Interactive comment posted.")
            time.sleep(3)
        else:
            logger.warning("Could not find send button. Pressing Enter.")
            page.actions.type('\n')
            time.sleep(3)
            
        # 尝试寻找自己刚才发的评论的“更多”按钮进行置顶
        more_btn = _try_selectors(
            page,
            "[data-e2e='comment-more']",
            ".more-action",
            timeout=3
        )
        if more_btn:
            more_btn.click(by_js=True)
            time.sleep(1)
            pin_btn = page.ele("text=置顶", timeout=3)
            if pin_btn:
                pin_btn.click(by_js=True)
                logger.success("Interactive comment PINNED successfully.")
                time.sleep(1)
    except Exception as e:
        logger.warning(f"Failed to post/pin comment: {e}")
    finally:
        page.quit()
