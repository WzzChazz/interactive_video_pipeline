"""
make_video.py — 水豚治愈短剧【一键出片】,带分镜板审阅卡点。
这是 skill 的主入口。流程：
  剧本 → 生图 → 【分镜板审阅(改提示词/重生)→ 确认渲染】 → 视频 → 配音 → 合片
用法：
  python make_video.py                # 带审阅(推荐)
  python make_video.py --no-review    # 全自动,不审阅
  python make_video.py --tag CAPY_03  # 自定义 tag
"""
import sys
import time
import argparse
from unittest.mock import MagicMock
sys.modules["redis"] = MagicMock()

from core.llm_agent import _call_deepseek, _extract_json, EpisodeScript
from core.image_gen import generate_images
from core.video_gen import generate_video_clips
from core.audio_gen import generate_audio
from core.ffmpeg_compiler import compile_video
from core.storyboard_server import serve_review

THEME = "capybara_healing"
USER_PROMPT = ("请为《水豚的治愈日常》创作一条自包含小剧场，5-6个分镜，"
               "团团(佛系蠢萌水豚,深沉老爷爷音)×林溪(年轻活泼少女,清甜女声)拟人对话，"
               "反差萌+皮的摆烂吐槽。严格只输出纯JSON。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="CAPY")
    ap.add_argument("--no-review", action="store_true", help="跳过分镜板审阅,全自动")
    args = ap.parse_args()
    tag = f"{args.tag}_{int(time.time()) % 100000}"

    print(">>> [1/5] 剧本 (Ark DeepSeek)…", flush=True)
    script = None
    for _ in range(4):
        try:
            data = _extract_json(_call_deepseek(USER_PROMPT, theme_key=THEME))
            data.setdefault("chosen_branch", "INIT")
            script = EpisodeScript.model_validate(data)
            break
        except Exception as e:
            print("   剧本重试:", type(e).__name__, str(e)[:90], flush=True)
    if not script:
        sys.exit("❌ 剧本生成失败(Ark DeepSeek 可能欠费,需先充值)")
    print("   标题:", script.episode_title, "| 分镜:", len(script.scenes), flush=True)
    script_d = script.model_dump()
    scenes = script_d["scenes"]

    print(">>> [2/5] 生图 (Seedream 4K + 定妆照锁脸)…", flush=True)
    image_manifest = generate_images(scenes, tag, episode_id=None)
    print("   生成图:", len(image_manifest), "张", flush=True)

    if not args.no_review:
        print(">>> 打开分镜板审阅页:改提示词/重生满意后,点页面底部「确认渲染」继续…", flush=True)
        image_manifest = serve_review(script_d, image_manifest, theme_key=THEME)
        print("   审阅确认,继续渲染。", flush=True)

    print(">>> [3/5] 视频片段 (Ken Burns + Seedance)…", flush=True)
    clip_manifest = generate_video_clips(scenes, image_manifest, tag, episode_id=None, theme_key=THEME)

    print(">>> [4/5] 配音 + 音效…", flush=True)
    audio_manifest = generate_audio(scenes, tag, episode_id=None, theme_key=THEME)

    print(">>> [5/5] 合片 (字幕+BGM+封面+片尾)…", flush=True)
    out = compile_video(scenes, clip_manifest, audio_manifest, image_manifest, tag,
                        theme_key=THEME, render_mode="kuaishou_only",
                        next_branches=script.next_branches.model_dump(),
                        banner_text=script.episode_title, cover_teaser=script.cover_teaser)
    print(f"\n>>> 🎬 成品: {out}", flush=True)

    # 渲染后自动自检(免肉眼审 bug)
    from core.video_qa import qa_check_video, qa_check_voices, format_qa_report
    final = list(out.values())[0] if isinstance(out, dict) and out else out
    issues = qa_check_video(final) + qa_check_voices(audio_manifest, scenes)
    print("\n" + format_qa_report(issues), flush=True)


if __name__ == "__main__":
    main()
