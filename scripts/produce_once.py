"""
scripts/produce_once.py — 每日无人值守出片(治愈线专用)。
只生产,【不发布、不走 Dashboard 审批门】——发布永远由人晚上手动做。
走全套新机制: 选题择优→金句10选1→首帧萌度质检→首镜强制微动→情绪BGM→金句定帧片尾→标题公式。
产出: storage/outputs/CAPY_日期/ 下的 kuaishou 成片 + 封面 + 发布物料.txt(标题/文案,复制即用)。
同日重复运行自动跳过(一天一条)。
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loguru import logger

THEME = "capybara_healing"
TAG = "CAPY_" + datetime.now().strftime("%Y%m%d")


def main() -> int:
    from core.llm_agent import generate_script
    from core.image_gen import generate_images
    from core.video_gen import generate_video_clips
    from core.audio_gen import generate_audio
    from core.ffmpeg_compiler import compile_video, generate_cover
    from config.settings import STORAGE_OUTPUT_DIR

    out_dir = STORAGE_OUTPUT_DIR / TAG
    final = out_dir / f"{TAG}_kuaishou.mp4"
    if final.exists():
        logger.info(f"{TAG} 已有成片,跳过(一天一条)")
        return 0

    logger.info(f"=== 无人值守出片 {TAG} ===")
    script = generate_script(branch="INIT", engine="deepseek", theme_key=THEME)
    scenes = [s.model_dump() for s in script.scenes]

    image_manifest = generate_images(scenes, TAG, None, None, theme_key=THEME)
    clip_manifest = generate_video_clips(scenes, image_manifest, TAG, None, theme_key=THEME)
    audio_manifest = generate_audio(scenes, TAG, None, theme_key=THEME)

    paths = compile_video(
        scenes, clip_manifest, audio_manifest, image_manifest, TAG,
        theme_key=THEME, render_mode="kuaishou_only",
        next_branches=script.next_branches.model_dump() if script.next_branches else {},
        cover_teaser=script.cover_teaser,
        bgm_mood=getattr(script, "bgm_mood", "warm"),
    )

    # 封面 = 首镜钩子帧(cozy 设计:首帧即封面)
    cover_src = image_manifest.get(1) or next(iter(image_manifest.values()), None)
    if cover_src:
        generate_cover(Path(cover_src), script.episode_title, script.cover_teaser,
                       out_dir / f"{TAG}_cover.jpg", theme_key=THEME)

    # 发布物料(晚上人工发布时复制即用);系列编号=已产出集数(收藏集邮/追更钩子)
    _series_no = len([d for d in STORAGE_OUTPUT_DIR.glob("CAPY_*") if d.is_dir()])
    (out_dir / f"{TAG}_发布物料.txt").write_text(
        f"标题:\n{script.episode_title}\n\n文案:\n{script.episode_summary}\n"
        f"「团团的晚安治愈」第 {_series_no} 句 · 每晚更新\n\n"
        f"BGM情绪: {getattr(script, 'bgm_mood', 'warm')}\n"
        f"发布清单: 挂平台热门治愈BGM(音量20-30%) / 加合集「团团的晚安治愈」/ "
        f"#水豚 #治愈 #晚安 +1热话题 / 勾AIGC / 发布后置顶自评引导互动\n",
        encoding="utf-8")
    logger.success(f"✅ 出片完成: {paths}")
    logger.success(f"   发布物料: {out_dir / f'{TAG}_发布物料.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
