"""
automation/kuaishou_publisher.py
================================
快手创作者平台全自动视频发布模块（基于 DrissionPage）。

核心流程：
  1. 打开快手创作者平台上传页。
  2. JS 强行触发隐藏 file input，完成视频上传。
  3. 填写视频标题（React-compatible JS 注入）。
  4. 填写简介/文案。
  5. 等待上传完成，自动点击【发布】按钮。

快手创作者中心 URL：
  https://cp.kuaishou.com/article/publish/video
"""

import time
from pathlib import Path
from loguru import logger
from automation.publisher import _create_browser, _try_selectors, _screenshot, PublisherError


# 快手发布页
_KS_UPLOAD_URL = "https://cp.kuaishou.com/article/publish/video"


def build_kuaishou_caption(
    episode_summary: str,
    branch_a_teaser: str,
    branch_b_teaser: str,
    episode_tag: str,
    title: str,
) -> str:
    """构建快手风格的中文互动引导文案"""
    lines = []
    if episode_summary:
        lines.append(episode_summary)
    lines.append("")
    lines.append("⚡ 剧情走向由你决定！")
    if branch_a_teaser:
        lines.append(f"🔴 A：{branch_a_teaser}")
    if branch_b_teaser:
        lines.append(f"🔵 B：{branch_b_teaser}")
    lines.append("")
    lines.append("👇 评论区留下你的选择，影响下一集剧情！")
    lines.append(f"#{episode_tag} #AI互动短剧 #储物间的秘密 #悬疑")
    return "\n".join(lines)


def _fill_title_ks(page, title: str) -> bool:
    """
    快手标题框填写。优先 JS 注入（React 兼容），回退原生 input。
    返回 True 表示成功，False 表示未找到输入框。
    """
    selectors = [
        "xpath://input[@placeholder='填写标题，让更多人看到你的作品']",
        "xpath://input[contains(@placeholder,'填写标题')]",
        "xpath://input[contains(@class,'title')][@type!='hidden']",
        "input.title-input",
        "[data-e2e='video-title-input']",
    ]
    inp = _try_selectors(page, *selectors, timeout=10)
    if not inp:
        logger.warning("快手：未找到标题输入框，跳过标题填写。")
        return False

    try:
        # React 兼容 JS 注入
        old_val = inp.attr("value") or ""
        page.run_js(
            """
            var inp = arguments[0];
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inp, arguments[1]);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            inp,
            title,
        )
        logger.success(f"快手：标题已注入 → {title!r}")
        return True
    except Exception as e:
        logger.warning(f"快手：JS 注入标题失败 ({e})，尝试 native input...")
        try:
            inp.clear()
            inp.input(title)
            logger.success(f"快手：标题已通过 native input 填写 → {title!r}")
            return True
        except Exception as e2:
            logger.error(f"快手：标题填写完全失败：{e2}")
            return False


def _fill_caption_ks(page, caption: str) -> bool:
    """填写简介/文案。快手用 contenteditable div 或 textarea。"""
    selectors = [
        "xpath://div[@contenteditable='true']",
        "xpath://div[@contenteditable='true' and contains(@class,'desc')]",
        "xpath://textarea[contains(@placeholder,'添加作品简介')]",
        "xpath://div[@contenteditable='true'][contains(@placeholder,'添加作品简介')]",
        "xpath://div[@contenteditable='true'][2]",
        ".description-input",
        "[data-e2e='video-desc-input']",
    ]
    el = _try_selectors(page, *selectors, timeout=10)
    if not el:
        logger.warning("快手：未找到简介输入框，跳过文案填写。")
        return False

    try:
        el.click(by_js=True)
        time.sleep(0.5)
        page.run_js(
            """
            var el = arguments[0];
            el.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            document.execCommand('insertText', false, arguments[1]);
            """,
            el,
            caption,
        )
        logger.success(f"快手：文案已填写 ({len(caption)} 字)")
        return True
    except Exception as e:
        logger.warning(f"快手：execCommand 填写失败 ({e})，尝试 input...")
        try:
            el.clear()
            el.input(caption)
            return True
        except Exception as e2:
            logger.error(f"快手：文案填写失败：{e2}")
            return False


def publish_to_kuaishou(
    video_path: str,
    title: str,
    caption: str,
) -> str:
    """
    通过 DrissionPage 模拟快手创作者平台发布视频。

    Args:
        video_path: 本地 MP4 文件路径
        title:      视频标题（30字以内）
        caption:    视频简介/文案（带话题标签）

    Returns:
        快手作品页 URL（占位，实际成功后跳转到作品管理页）
    """
    logger.info("=" * 50)
    logger.info("⚡ [快手发布器启动]")
    logger.info(f"目标视频：{video_path}")
    logger.info(f"标题：{title}")
    logger.info("=" * 50)

    if not Path(video_path).exists():
        raise PublisherError(f"视频文件不存在：{video_path}")

    page = None
    try:
        page = _create_browser()
        logger.info("正在打开快手创作者平台上传页...")
        page.get(_KS_UPLOAD_URL)
        time.sleep(6)

        # 检查登录状态
        if "login" in page.url.lower() or "passport" in page.url.lower():
            raise PublisherError(
                "未登录快手创作者平台，请先运行 login_matrix.py 手动登录。"
            )

        # ── Step 1：上传视频 ──────────────────────────
        logger.info("快手：尝试自动上传视频...")
        
        # 使用 DrissionPage 拦截操作系统的文件选择框
        page.set.upload_files(str(video_path))
        
        upload_btn = _try_selectors(
            page,
            "text:上传视频",
            "text:点击上传",
            "input[type='file']",
            ".upload-btn",
            timeout=10,
        )
        if upload_btn:
            if upload_btn.tag == "input":
                upload_btn.input(video_path)
            else:
                upload_btn.click(by_js=True)
            logger.info("快手：已触发文件上传！")
        else:
            logger.warning("快手：未找到上传按钮，回退至人工干预。")
            logger.warning("=" * 50)
            logger.warning(f"请您手动点击网页上的【上传视频】按钮，并选择文件：\n{video_path}")
            logger.warning("=" * 50)

        time.sleep(3)
        _screenshot(page, "ks_after_upload_trigger")

        # ── Step 2：等待上传进度条完成 ──────────────────
        logger.info("快手：等待视频上传完成...")
        for i in range(120):  # 最多等 10 分钟
            # 检查是否出现简介输入框（说明上传完成，快手已将标题和简介合并）
            desc_el = _try_selectors(
                page,
                "xpath://div[@contenteditable='true']",
                timeout=3,
            )
            if desc_el:
                logger.success("快手：视频上传完成，已出现编辑区。")
                break

            # 也检查进度提示
            done_el = _try_selectors(
                page,
                "text:上传成功",
                "text:视频已上传",
                "[data-e2e='upload-success']",
                timeout=1,
            )
            if done_el:
                logger.success("快手：检测到上传成功提示。")
                break

            if i % 10 == 0:
                _screenshot(page, f"ks_upload_progress_{i}")
            time.sleep(5)
        else:
            _screenshot(page, "ks_upload_timeout")
            raise PublisherError("快手：视频上传超时（10分钟内未完成）")

        time.sleep(2)

        # ── Step 3 & 4：填写标题和简介 (快手现在将它们合并在一个框里) ──────────────
        combined_text = f"{title}\n\n{caption}"
        _fill_caption_ks(page, combined_text)
        time.sleep(1)

        _screenshot(page, "ks_before_publish")

        # ── Step 4.5：自动勾选 AIGC 声明 ──────────────────
        logger.info("快手：尝试勾选 AIGC（人工智能生成内容）声明...")
        more_btn = _try_selectors(page, "text:展开更多设置", "text:高级设置", "text:更多设置", timeout=2)
        if more_btn:
            try:
                more_btn.click(by_js=True)
                time.sleep(1)
            except Exception:
                pass
            
        aigc_cb = _try_selectors(
            page,
            "text:人工智能生成内容",
            "text:含有AI生成内容",
            "text:AI生成内容",
            timeout=2
        )
        if aigc_cb:
            try:
                aigc_cb.click(by_js=True)
                logger.success("快手：AIGC 声明已成功勾选！")
            except Exception:
                pass
        else:
            logger.warning("快手：未找到 AIGC 勾选框，请等会儿人工复查。")

        # ── Step 5：人工核对与发布 ────────────────────────
        logger.warning("=" * 50)
        logger.warning("🔔 [人工核对环节] 🔔")
        logger.warning("视频和文案均已自动填好！")
        logger.warning("请您花 10 秒钟手动选择一下【封面】，并检查文案和 AIGC 标识。")
        logger.warning("确认无误后，请手动点击网页上的【发布】按钮！")
        logger.warning("发布完成后，再到这里按下 Enter 键继续处理下一集...")
        logger.warning("=" * 50)
        
        input("【等待您的回车...】(按 Enter 继续) ")
        
        logger.info("收到回车指令，正在核实发布状态...")

        # ── Step 6：等待发布成功提示 ──────────────────
        logger.info("快手：等待发布成功确认...")
        for _ in range(3):
            success_el = _try_selectors(
                page,
                "text:发布成功",
                "text:作品已发布",
                "text:视频已成功发布",
                "[data-e2e='publish-success']",
                timeout=3,
            )
            if success_el:
                logger.success("✅ 快手视频发布成功！")
                _screenshot(page, "ks_publish_success")
                break
            time.sleep(3)
        else:
            # 即使没有明确提示，也记录成功（可能已跳转）
            logger.warning("快手：未检测到明确的发布成功提示，但流程已完成。")
            _screenshot(page, "ks_publish_done_uncertain")

        logger.info("=" * 50)
        return "https://cp.kuaishou.com/profile"

    except Exception as e:
        logger.error(f"快手发布失败：{e}")
        if page:
            _screenshot(page, "ks_publish_error")
        raise
    finally:
        if page:
            time.sleep(3)
            page.quit()
