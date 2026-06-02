"""
main.py
=======
交互式短剧全自动生产线 · 调度核心 (Daily Task CLI)

职责：
  1. 初始化数据库（幂等）
  2. 注册每日定时任务（schedule）
  3. 提供手动单次触发 CLI 入口（--run-now）
  4. 协调各 core/ 和 automation/ 模块按流水线顺序执行

用法::

    # 进入守护模式（每日 08:00 自动触发）
    python main.py

    # 立即执行一次完整流水线（调试用）
    python main.py --run-now

    # 仅执行某一阶段（调试用）
    python main.py --stage scrape
    python main.py --stage generate
    python main.py --stage compile
    python main.py --stage publish
"""

import argparse
import json
import sys
import time
from pathlib import Path

import schedule
from loguru import logger

from config.settings import DAILY_RUN_TIME
from database.db_session import init_db, health_check, get_session
from database.models import Episode, EpisodeStatus


# ──────────────────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
           "<level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
           "<level>{message}</level>",
    level="DEBUG",
)
logger.add(
    "storage/logs/pipeline_{time:YYYY-MM-DD}.log",
    rotation="00:00",    # 每天零点轮转
    retention="30 days", # 保留 30 天
    compression="gz",
    level="INFO",
)


# ──────────────────────────────────────────────────────────
# 流水线各阶段（Phase 2-4 实现后替换占位符）
# ──────────────────────────────────────────────────────────

def stage_scrape(episode: Episode) -> str:
    """
    步骤 1：DrissionPage 抓取抳音评论区 A/B 投票。
    投票结果写回 DB（vote_a_count / vote_b_count）。
    """
    from automation.scraper import scrape_votes, ScraperError

    logger.info("[Stage 1/6] Starting vote scraping...")

    # 从 DB 读取上一集的投票视频 URL
    with get_session() as session:
        prev_ep = (
            session.query(Episode)
            .filter(
                Episode.season_id == episode.season_id,
                Episode.episode_number == episode.episode_number - 1,
                Episode.status.in_([EpisodeStatus.COMPLETED, EpisodeStatus.PUBLISHED]),
            )
            .first()
        )
        prev_video_url = prev_ep.douyin_video_url if prev_ep else None

    if not prev_video_url:
        logger.warning(
            "No previous episode video URL found. "
            "Using DOUYIN_TARGET_VIDEO_URL from settings."
        )

    branch, vote_a, vote_b = scrape_votes(
        video_url=prev_video_url,
    )
    
    # [NEW] 顺便抓取上集播放数据
    analytics = None
    if prev_video_url:
        try:
            from automation.analytics import scrape_video_analytics
            analytics = scrape_video_analytics(prev_video_url)
            import json
            logger.info("Fetched analytics for prev episode: {}", analytics)
        except Exception as e:
            logger.warning("Failed to fetch analytics: {}", e)

    # 写回投票结果到 DB
    with get_session() as session:
        ep = session.get(Episode, episode.id)
        if ep:
            ep.vote_a_count  = vote_a
            ep.vote_b_count  = vote_b
            ep.chosen_branch = branch
            
        # 如果抓到了数据，写回上集
        if analytics and prev_ep:
            db_prev = session.get(Episode, prev_ep.id)
            if db_prev:
                db_prev.views_count = analytics.get("views_count")
                db_prev.likes_count = analytics.get("likes_count")
                db_prev.audience_profile = json.dumps(analytics.get("audience_profile"))
                db_prev.completion_rate = analytics.get("completion_rate")
                db_prev.five_sec_retention = analytics.get("five_sec_retention")

    logger.success(
        "[Stage 1/6] Vote result: A={}, B={} → Branch {} wins.",
        vote_a, vote_b, branch
    )
    return branch


def stage_generate_script(branch: str, episode: Episode) -> dict:
    """
    步骤 2：调用 LLM 生成结构化剧本 JSON，并将结果持久化至 DB。
    """
    from core.llm_agent import (
        generate_script,
        build_history_summary,
        script_to_json_str,
        LLMCallError,
        ScriptValidationError,
    )

    logger.info("[Stage 2/6] Generating script for branch: {}", branch)

    # 从 DB 取最近 5 集历史，构建剧情上下文
    with get_session() as session:
        history_rows = (
            session.query(Episode)
            .filter(
                Episode.season_id == episode.season_id,
                Episode.episode_number < episode.episode_number,
                Episode.status.in_([
                    EpisodeStatus.COMPLETED,
                    EpisodeStatus.PUBLISHED,
                ]),
            )
            .order_by(Episode.episode_number.asc())
            .limit(5)
            .all()
        )
        history_dicts = [
            {
                "episode_number": ep.episode_number,
                "title": ep.title,
                "chosen_branch": ep.chosen_branch,
                "script_json": ep.script_json,
                "views_count": ep.views_count,
                "likes_count": ep.likes_count,
                "audience_profile": ep.audience_profile,
            }
            for ep in history_rows
        ]

    history_summary = build_history_summary(history_dicts)

    script_obj = generate_script(
        branch=branch,
        history_summary=history_summary,
        season_id=episode.season_id,
        episode_number=episode.episode_number,
        engine="deepseek",
        theme_key=episode.theme_key,
    )

    # 持久化剧本到 DB
    with get_session() as session:
        ep = session.get(Episode, episode.id)
        ep.script_json  = script_to_json_str(script_obj)
        ep.title        = script_obj.episode_title
        ep.chosen_branch = branch
        ep.status       = EpisodeStatus.PENDING_REVIEW

    logger.success(
        "[Stage 2/6] Script '{}' generated with {} scenes.",
        script_obj.episode_title,
        len(script_obj.scenes),
    )
    return json.loads(script_to_json_str(script_obj))


def stage_generate_assets(script: dict, episode: Episode) -> dict:
    """
    步骤 3&4：并行生成图片（Flux）→ 图生视频（Kling/Runway）→ 配音+音效（ElevenLabs）。
    三类资产并行生成（视觉线程 + 语音线程），最终汇总进 asset_manifest。
    """
    from core.image_gen import generate_images, ImageGenError
    from core.audio_gen import generate_audio, AudioGenError
    from core.video_gen import generate_video_clips, VideoGenError
    from concurrent.futures import ThreadPoolExecutor

    scenes = script.get("scenes", [])
    tag    = episode.episode_tag
    logger.info("[Stage 3-4/6] Generating assets for {} ({} scenes)...", tag, len(scenes))

    image_manifest: dict[int, str]       = {}
    audio_manifest: dict[int, dict]      = {}
    clip_manifest:  dict[int, str]       = {}

    # FAIL FAST 策略：必须先跑完且无报错，才允许跑极其昂贵的视频大模型 API！
    # 彻底废除并发执行，改为严格串行。
    logger.info("[Stage 3/6] Generating audio first (Fail-Fast protection)...")
    audio_manifest = generate_audio(scenes, tag, episode.id, episode.theme_key)

    logger.info("[Stage 3/6] Generating images...")
    image_manifest = generate_images(scenes, tag, None, episode.id)

    logger.info("[Stage 4/6] Image→Video (Executing sequentially to save API costs)...")
    clip_manifest = generate_video_clips(scenes, image_manifest, tag, episode.id)

    asset_manifest = {
        "images": image_manifest,
        "audio":  audio_manifest,
        "clips":  clip_manifest,
    }

    # 持久化 asset_manifest 到 DB
    with get_session() as session:
        ep = session.get(Episode, episode.id)
        if ep:
            ep.asset_manifest_json = json.dumps(asset_manifest, ensure_ascii=False)

    logger.success("[Stage 3-4/6] Assets complete: {} imgs, {} clips, {} audio tracks.",
                   len(image_manifest), len(clip_manifest), len(audio_manifest))
    return asset_manifest


def stage_compile(asset_manifest: dict, episode: Episode) -> str:
    """
    步骤 5：FFmpeg 合片 + 字幕硬烧录 → 输出最终 MP4。
    """
    from core.ffmpeg_compiler import compile_video, generate_cover, FFmpegError

    # 从 DB 读取最新 script_json（含 scenes）
    with get_session() as session:
        ep = session.get(Episode, episode.id)
        script_data = json.loads(ep.script_json or "{}")

    scenes         = script_data.get("scenes", [])
    clip_manifest  = {int(k): v for k, v in asset_manifest.get("clips",  {}).items()}
    audio_manifest = {int(k): v for k, v in asset_manifest.get("audio",  {}).items()}

    logger.info("[Stage 5/6] Compiling final video with FFmpeg...")
    
    episode_title = script_data.get("episode_title", "悬疑短剧")
    banner_text = f"第 {ep.episode_number} 集 | {episode_title}" if hasattr(ep, 'episode_number') else f"互动连载 | {episode_title}"

    cover_teaser = script_data.get("cover_teaser", "")

    output_paths = compile_video(
        scenes=script_data.get("scenes", []),
        clip_manifest=clip_manifest,
        audio_manifest=audio_manifest,
        episode_tag=episode.episode_tag,
        theme_key=ep.theme_key if hasattr(ep, 'theme_key') else "hospital_horror",
        next_branches=script_data.get("next_branches", {}),
        banner_text=banner_text,
        cover_teaser=cover_teaser,
    )

    # 生成封面 (Cover Generation) — 优先使用 LLM 标注的最高潮分镜
    episode_title = script_data.get("episode_title", "互动短剧")
    image_manifest = {int(k): v for k, v in asset_manifest.get("images", {}).items()}
    # 优先选 is_climax=true 的分镜，否则取序号最大的
    scenes_list = script_data.get("scenes", [])
    climax_sc = next((s for s in scenes_list if s.get("is_climax")), None)
    if climax_sc:
        climax_scene_idx = climax_sc["scene_index"]
        logger.info("Cover using climax scene {}", climax_scene_idx)
    else:
        climax_scene_idx = max(image_manifest.keys()) if image_manifest else 1
        logger.info("No is_climax scene found, using last scene {}", climax_scene_idx)
    climax_image_path = image_manifest.get(climax_scene_idx)
    if climax_image_path and Path(climax_image_path).exists():
        from config.settings import STORAGE_OUTPUT_DIR
        out_dir = STORAGE_OUTPUT_DIR / episode.episode_tag
        out_dir.mkdir(parents=True, exist_ok=True)
        cover_path = out_dir / f"{episode.episode_tag}_cover.jpg"
        try:
            generate_cover(Path(climax_image_path), episode_title, cover_teaser, cover_path)
            output_paths["cover"] = str(cover_path)
            logger.info("Generated cover: {}", cover_path)
        except Exception as e:
            logger.error("Failed to generate cover: {}", e)

    # --- 植入黄金三秒高能 Hook (Golden 3s Hook) ---
    import subprocess
    import shutil
    climax_clip = clip_manifest.get(climax_scene_idx)
    climax_audio = audio_manifest.get(climax_scene_idx, {}).get("voice")
    
    if climax_clip and climax_audio:
        try:
            hook_path = out_dir / f"{episode.episode_tag}_hook.mp4"
            # Extract 1.5s of the climax video AND audio, applying the same visual filters as the main video
            subprocess.run([
                "ffmpeg", "-y", "-i", climax_clip, "-i", climax_audio,
                "-t", "1.5",
                "-vf", f"crop=iw:ih-140:0:0,eq=brightness=-0.15:contrast=1.2:saturation=0.8,hue=s=0,eq=contrast=1.5,scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                str(hook_path)
            ], check=True, capture_output=True)
            
            for key, final_path in output_paths.items():
                if key == "cover" or not final_path: continue
                
                temp_path = str(final_path) + "_temp.mp4"
                shutil.move(final_path, temp_path)
                
                # 使用 filter_complex concat 可以完美解决不同视频片段拼接时的帧率/时间基准不一致导致的画面卡死、绿屏和音画不同步问题
                subprocess.run([
                    "ffmpeg", "-y", 
                    "-i", str(hook_path), 
                    "-i", str(temp_path),
                    "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]",
                    "-map", "[outv]", "-map", "[outa]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    final_path
                ], check=True, capture_output=True)
            
            logger.info("Successfully injected Golden 3s Hook with Audio into all videos!")
        except Exception as e:
            logger.error(f"Failed to inject hook: {e}")

    # 更新 DB
    with get_session() as session:
        ep = session.get(Episode, episode.id)
        if ep:
            ep.video_output_path = output_paths.get("douyin")
            ep.video_global_path = output_paths.get("global")
            ep.status            = EpisodeStatus.COMPLETED

    logger.success("[Stage 5/6] Video compiled: {}", output_paths)
    return output_paths


def stage_publish(output_paths: dict, episode: Episode, script: dict) -> None:
    """
    步骤 6：多平台矩阵发布 (Douyin, TikTok, X, 快手)
    包含标题、文案、AIGC 声明勾选，以及原生投票（X）。
    """
    from automation.publisher import publish_to_douyin, build_douyin_caption, PublisherError
    from automation.tiktok_publisher import publish_to_tiktok, build_tiktok_caption
    from automation.x_publisher import publish_to_x, build_x_tweet
    from automation.kuaishou_publisher import publish_to_kuaishou, build_kuaishou_caption
    from config.themes import THEMES
    
    theme_cfg = THEMES.get(episode.theme_key, THEMES.get("hospital_horror", {}))
    collection_name_cn = theme_cfg.get("collection_name", "")
    
    douyin_path = output_paths.get("douyin")
    kuaishou_path = output_paths.get("kuaishou", douyin_path)  # fallback to douyin
    global_path = output_paths.get("global")

    logger.info("[Stage 6/6] Publishing to Matrix: \n- Douyin: {}\n- Kuaishou: {}\n- Global: {}", douyin_path, kuaishou_path, global_path)

    # 1. 发布到抖音
    display_number = episode.episode_number
    if episode.theme_key == "hospital_horror":
        display_number = episode.episode_number - 12
    raw_title = script.get("episode_title", episode.episode_tag)
    episode_title_cn = f"第{display_number}集：{raw_title}"
    episode_summary_cn = script.get("episode_summary", "")
    branches        = script.get("next_branches", {})
    
    # 提取双轨特制定制文案
    douyin_branch_a = branches.get("douyin_branch_a", branches.get("branch_a_teaser", ""))
    douyin_branch_b = branches.get("douyin_branch_b", branches.get("branch_b_teaser", ""))
    kuaishou_branch_a = branches.get("kuaishou_branch_a", branches.get("branch_a_teaser", ""))
    kuaishou_branch_b = branches.get("kuaishou_branch_b", branches.get("branch_b_teaser", ""))

    caption_cn = build_douyin_caption(
        episode_summary=episode_summary_cn,
        branch_a_teaser=douyin_branch_a,
        branch_b_teaser=douyin_branch_b,
        episode_tag=episode.episode_tag,
        title=episode_title_cn,
    )

    try:
        publish_to_douyin(
            video_path=douyin_path,
            title=episode_title_cn,
            caption=caption_cn,
            check_aigc=True,
            branch_a_teaser=douyin_branch_a,
            branch_b_teaser=douyin_branch_b,
            collection_name=collection_name_cn,
        )
        logger.success("Douyin publish successful.")
        
        # 2. 发布到 TikTok (海外视频)
        episode_title_en = f"EP{display_number}: AI Interactive Horror"
        branch_a_en = branches.get("english_branch_a_teaser", "Option A")
        branch_b_en = branches.get("english_branch_b_teaser", "Option B")
        caption_en_tiktok = build_tiktok_caption(episode_title_en, branch_a_en, branch_b_en, episode.episode_tag)
        
        publish_to_tiktok(
            video_path=global_path,
            title=episode_title_en,
            caption=caption_en_tiktok,
        )
        logger.success("TikTok publish successful.")
        
        # 3. 发布到 X (带原生 Poll 投票)
        tweet_text = build_x_tweet(episode_title_en, episode.episode_tag)
        publish_to_x(
            video_path=global_path,
            tweet_text=tweet_text,
            poll_options=["Option A", "Option B"]
        )
        logger.success("X (Twitter) publish successful.")

        # 4. 发布到快手（使用快手专版视频和文案）
        caption_ks = build_kuaishou_caption(
            episode_summary=episode_summary_cn,
            branch_a_teaser=kuaishou_branch_a,
            branch_b_teaser=kuaishou_branch_b,
            episode_tag=episode.episode_tag,
            title=episode_title_cn,
        )
        publish_to_kuaishou(
            video_path=kuaishou_path,
            title=episode_title_cn,
            caption=caption_ks,
        )
        logger.success("快手发布成功！")
        
    except Exception as e:
        logger.error(f"Matrix Publish Failed: {e}")
        raise  # 上抛给 run_pipeline 统一处理

    # 更新 DB
    with get_session() as session:
        ep = session.get(Episode, episode.id)
        if ep:
            ep.status           = EpisodeStatus.PUBLISHED
            ep.published_at     = __import__('datetime').datetime.now()

    logger.success("[Stage 6/6] Published successfully.")


# ──────────────────────────────────────────────────────────
# 完整流水线入口
def _get_or_create_current_episode(theme_key: str = "hospital_horror") -> Episode:
    """
    取出指定题材宇宙中，当前状态为 VOTING 或 GENERATING 的 Episode，
    若不存在则自动创建第 1 季第 1 集（首次运行）。
    """
    with get_session() as session:
        # 优先找 GENERATING（断点续跑）
        ep = (
            session.query(Episode)
            .filter(
                Episode.theme_key == theme_key,
                Episode.status.in_([
                    EpisodeStatus.VOTING,
                    EpisodeStatus.GENERATING_SCRIPT,
                    EpisodeStatus.PENDING_REVIEW,
                    EpisodeStatus.GENERATING_ASSETS,
                ])
            )
            .order_by(Episode.episode_number.desc())
            .first()
        )
        if ep is None:
            # 计算该主题的下一集号
            last = (
                session.query(Episode)
                .filter(Episode.theme_key == theme_key)
                .order_by(Episode.episode_number.desc())
                .first()
            )
            next_num = (last.episode_number + 1) if last else 1
            ep = Episode(
                season_id=1,
                episode_number=next_num,
                theme_key=theme_key,
                status=EpisodeStatus.VOTING,
            )
            session.add(ep)
            session.commit()
            session.refresh(ep)
            logger.info("Created new {} Episode: S01E{:03d}", theme_key, next_num)
        return ep


def run_pipeline(theme_key: str = "hospital_horror") -> None:
    """
    执行完整的一期生产流水线。
    如果发现有存稿（COMPLETED 状态），优先发布存稿！
    任何阶段失败时记录错误并将 Episode 状态置为 FAILED。
    """
    logger.info("=" * 60)
    logger.info("Pipeline started for theme: {}", theme_key)
    logger.info("=" * 60)

    # 1. 优先检查存稿库（自动发布策略）
    with get_session() as session:
        completed_ep = session.query(Episode).filter(
            Episode.theme_key == theme_key,
            Episode.status == EpisodeStatus.COMPLETED
        ).order_by(Episode.episode_number.asc()).first()
    
    if completed_ep:
        logger.info("Found stockpiled COMPLETED episode: {}. Proceeding to auto-publish.", completed_ep.episode_tag)
        try:
            script = json.loads(completed_ep.script_json or "{}")
            output_paths = {
                "douyin": completed_ep.video_output_path,
                "global": completed_ep.video_global_path
            }
            stage_publish(output_paths, completed_ep, script)
            
            with get_session() as session:
                ep = session.get(Episode, completed_ep.id)
                ep.status = EpisodeStatus.PUBLISHED
                session.commit()
            logger.success("✅ Stockpile episode {} published successfully!", completed_ep.episode_tag)
            return
        except Exception as exc:
            logger.error("❌ Stockpile publishing FAILED at episode {}: {}", completed_ep.episode_tag, exc)
            with get_session() as session:
                ep = session.get(Episode, completed_ep.id)
                ep.status = EpisodeStatus.FAILED
                ep.error_message = str(exc)
                session.commit()
            return

    # 2. 如果没有存稿，走正常的生成流水线
    episode = _get_or_create_current_episode(theme_key)
    logger.info("Target episode: {} (Status: {})", episode.episode_tag, episode.status.value)

    try:
        if episode.status == EpisodeStatus.VOTING:
            branch = stage_scrape(episode)
            with get_session() as session:
                ep = session.get(Episode, episode.id)
                ep.status = EpisodeStatus.GENERATING_SCRIPT
            script = stage_generate_script(branch, episode)
            logger.warning("Pipeline paused at PENDING_REVIEW. Please approve the script in Dashboard.")
            
        elif episode.status == EpisodeStatus.GENERATING_SCRIPT:
            branch = episode.chosen_branch or stage_scrape(episode)
            script = stage_generate_script(branch, episode)
            logger.warning("Pipeline paused at PENDING_REVIEW. Please approve the script in Dashboard.")
            
        elif episode.status == EpisodeStatus.PENDING_REVIEW:
            logger.warning("Episode {} is still PENDING_REVIEW. Waiting for human approval.", episode.episode_tag)
            
        elif episode.status == EpisodeStatus.GENERATING_ASSETS:
            script = json.loads(episode.script_json or "{}")
            assets = stage_generate_assets(script, episode)
            output_path = stage_compile(assets, episode)
            stage_publish(output_path, episode, script)
            logger.success("Pipeline completed successfully.")
            
    except Exception as exc:
        logger.error("Pipeline FAILED at episode {}: {}", episode.episode_tag, exc)
        with get_session() as session:
            ep = session.get(Episode, episode.id)
            if ep:
                ep.status = EpisodeStatus.FAILED
                ep.error_message = str(exc)
        raise


# ──────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive Video Automation Pipeline"
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="立即执行一次完整流水线（跳过定时等待）",
    )
    parser.add_argument(
        "--stage",
        choices=["scrape", "generate", "compile", "publish"],
        default=None,
        help="仅执行指定阶段（调试用）",
    )
    parser.add_argument(
        "--theme",
        type=str,
        default="hospital_horror",
        help="选择生成的题材宇宙（默认：hospital_horror）",
    )
    args = parser.parse_args()

    # 1. 数据库初始化（幂等）
    init_db()
    if not health_check():
        logger.critical("Database health check failed. Aborting.")
        sys.exit(1)

    # 2. 单阶段调试模式
    if args.stage:
        _ep = _get_or_create_current_episode(args.theme)
        _dummy_script: dict = {}
        stage_map = {
            "scrape":   lambda: stage_scrape(_ep),
            "generate": lambda: stage_generate_script("A", _ep),
            "compile":  lambda: stage_compile({}, _ep),
            "publish":  lambda: stage_publish("", _ep, _dummy_script),
        }
        stage_map[args.stage]()
        return

    # 3. 立即执行一次
    if args.run_now:
        run_pipeline(args.theme)
        return

    # 4. 守护模式：注册每日定时任务
    logger.info("Daemon mode: pipeline scheduled at {} daily for theme {}.", DAILY_RUN_TIME, args.theme)
    schedule.every().day.at(DAILY_RUN_TIME).do(run_pipeline, theme_key=args.theme)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
