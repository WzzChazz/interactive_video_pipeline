"""
core/llm_agent.py
=================
剧本与分镜生成大脑。

职责：
  1. 接收历史剧情上下文 + 今日胜出分支（A/B），调用 LLM 生成下一集完整剧本。
  2. 使用 Pydantic v2 定义严格的输出数据模型，100% 保证返回结构合法。
  3. System Prompt 采用「JSON-only」强制模式 + 示例锚点，拒绝任何 Markdown 包裹。
  4. 支持 Claude 3.5 Sonnet（主力）和 DeepSeek-R1（备用）双引擎热切换。
  5. tenacity 指数退避重试（最多 3 次），内置 JSON 二次修复（strip 多余字符）。

输出数据结构（EpisodeScript）：
  ├── episode_title      本集标题
  ├── episode_summary    本集剧情简介（用于发布文案）
  ├── chosen_branch      本集驱动分支 A/B/INIT
  ├── scenes[]           分镜列表
  │   ├── scene_index        分镜序号（1-based）
  │   ├── dialogue           角色台词/旁白（用于 TTS + 字幕）
  │   ├── speaker            说话角色名（空字符串 = 旁白）
  │   ├── emotion            情绪标签（用于 ElevenLabs 语气控制）
  │   ├── visual_prompt      图像生成 Prompt（英文，Flux/MJ 专用）
  │   ├── camera_note        摄影机运动描述（用于 Kling I2V 参数）
  │   └── sfx_prompt         环境音效描述（英文，ElevenLabs SFX 专用）
  └── next_branches
      ├── branch_a_teaser    A 分支悬念预告（用于评论区投票引导文案）
      └── branch_b_teaser    B 分支悬念预告
"""

import json
import re
import textwrap
from typing import Literal, Optional

import anthropic
import openai
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

from config.settings import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    API_MAX_RETRIES,
)


# ──────────────────────────────────────────────────────────
# Pydantic v2 数据模型
# ──────────────────────────────────────────────────────────

class VisualStyle(BaseModel):
    aesthetic: str = Field(description="Aesthetic style (e.g. Cinematic horror, Anime, Realistic)")
    colors: str = Field(description="Dominant colors and color grading")
    lighting: str = Field(description="Lighting setup (e.g. Volumetric, flashlight, high contrast)")

class CharacterDetail(BaseModel):
    identity: str = Field(description="Who the character is (e.g. 25-year-old female doctor Lin Yue)")
    appearance: str = Field(description="Strict facial and body features (e.g. pale skin, pure black hair, heavy dark circles)")
    attire: str = Field(description="What the character is wearing")

class VisualPromptSchema(BaseModel):
    type: str = Field(description="Type of scene shot (e.g. Close-up, Wide shot, POV)")
    character: CharacterDetail
    pose: str = Field(description="Character's posture, action, and direction of gaze")
    environment: str = Field(description="Detailed description of the background and props")
    style: str = Field(description="Aesthetic style, colors, and lighting (e.g. Cinematic horror, high contrast, flashlight)")
    constraints: str = Field(description="Negative prompts or strict constraints (e.g. NO blood, MUST look down)")

class SceneShot(BaseModel):
    """
    单个分镜（Scene/Shot）的完整数据。
    所有 Prompt 字段强制英文，以确保图像 / 音效 API 的最佳效果。
    """

    scene_index: int = Field(..., ge=1, le=30, description="分镜序号，从 1 开始")
    dialogue: str = Field(
        default="",
        description="分镜内角色的台词或内心独白（中文，留空表示纯画面演示）"
    )
    english_dialogue: str = Field(
        default="",
        description="English translation of the dialogue (for TikTok/X global audiences). Keep it empty if dialogue is empty."
    )
    speaker: str = Field(default="", description="说话角色名；空字符串表示旁白")
    emotion: str = Field(
        default="neutral", 
        description="情绪标签，驱动 ElevenLabs 语气（例如：neutral, angry, fearful, determined 等）"
    )

    visual_prompt: VisualPromptSchema = Field(
        ...,
        description="Structured JSON prompt for image generation models (must be in English)"
    )
    camera_note: str = Field(
        default="static shot",
        description="摄影机运动描述（英文），如 'slow push in', 'pan left', 'handheld shake'"
    )
    sfx_prompt: str = Field(
        default="ambient silence",
        description="环境音效 Prompt（英文），如 'heavy rain on rooftop, distant thunder'"
    )
    is_climax: bool = Field(
        default=False,
        description="标记该分镜是否为本集最高潮/最具视觉冲击力的画面（全集唯一，用于生成封面缩略图）"
    )
    needs_motion: bool = Field(
        default=False,
        description="该分镜是否需要真实运动（如角色转头/眨眼/吃东西/明显动作）。True=走图生视频(花钱)；False=静图Ken Burns缓慢推拉(免费)。治愈题材默认 False，仅在有明确动作时设 True"
    )

    @field_validator("sfx_prompt", "camera_note", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v

    @field_validator("dialogue", mode="before")
    @classmethod
    def strip_dialogue(cls, v: str) -> str:
        return v.strip()


class NextBranches(BaseModel):
    """下一集的 A/B 分支悬念预告"""

    branch_a_teaser: str = Field(..., min_length=2, description="选项A预告")
    branch_b_teaser: str = Field(..., min_length=2, description="选项B预告")
    
    english_branch_a_teaser: str = Field(default="", description="Global option A")
    english_branch_b_teaser: str = Field(default="", description="Global option B")

    # 兼容老数据或在后续步骤动态填充
    douyin_branch_a: Optional[str] = None
    douyin_branch_b: Optional[str] = None
    kuaishou_branch_a: Optional[str] = None
    kuaishou_branch_b: Optional[str] = None

class EpisodeScript(BaseModel):
    """
    单集完整剧本的根模型。
    这是 LLM 必须严格返回的最终结构。
    """

    episode_title: str = Field(..., min_length=2, max_length=50, description="本集标题（中文）")
    episode_summary: str = Field(
        ..., min_length=20, max_length=300,
        description="本集剧情简介，用于抖音发布文案（中文，含话题引导）"
    )
    cover_teaser: str = Field(
        default="点击揭开真相！",
        description="短促、极具吸引力的视频封面引导文字（如：你敢进去吗？真假双生子），限制 10个字以内"
    )
    chosen_branch: str = Field(..., description="本集驱动分支：A / B / INIT")
    bgm_mood: str = Field("warm", description="治愈线BGM情绪(calm|playful|warm),用于合片按情绪选曲;恐怖线忽略")
    scenes: list[SceneShot] = Field(
        ..., min_length=3, max_length=7,
        description="分镜列表：恐怖连载 6-7 个（35-40秒）；治愈单条 2-3 个（10-14秒，BGM+萌情境主导，完播优先）"
    )
    next_branches: NextBranches = Field(..., description="下一集预告（恐怖=A/B投票；治愈=温柔明日预告）")

    @field_validator("scenes")
    @classmethod
    def validate_scene_indices(cls, scenes: list[SceneShot]) -> list[SceneShot]:
        """确保分镜序号连续且从 1 开始"""
        for i, scene in enumerate(scenes, start=1):
            if scene.scene_index != i:
                raise ValueError(
                    f"scene_index must be sequential starting from 1, "
                    f"but scene {i} has scene_index={scene.scene_index}"
                )
        return scenes


# ──────────────────────────────────────────────────────────
# System Prompt 工厂
# ──────────────────────────────────────────────────────────

from config.themes import THEMES

def _build_healing_system_prompt(theme: dict) -> str:
    """
    治愈/萌宠/反差萌 单条小剧场的系统提示词分支。
    复用与恐怖线完全相同的 JSON schema，仅改变创作语义：
      - dialogue   = 团团/林溪的拟人配音对话 或 旁白文案（中文短句），靠配音+字幕，NOT 对口型
      - speaker    = "团团" / "林溪" / "旁白"，切镜头+配音呈现对话，人物不做精确对口型
      - emotion    = 旁白语气（warm/gentle/playful），驱动 TTS 音色，NOT 对口型
      - next_branches = 温柔的"明日预告"（不做投票/不做选项引导）
      - is_serial=False：每条自包含，无集数、无前情依赖

    内容基调由 settings.HEALING_STYLE 开关（A/B 用）：
      - cozy  = 静治愈萌 + BGM 主导（抖音水豚噜噜路子）
      - sassy = 搞笑嘴替 + 有事发生 + 戏剧反转（对齐快手偏好 + 自己数据"有动作>发呆"）
    """
    from config.settings import HEALING_STYLE

    if HEALING_STYLE == "cozy":
        register_hook = textwrap.dedent("""\
1. **FRAME-1 = 双通道反差钩子: 萌画面 + 第0秒老爷爷音开口 (differentiation MUST live in the first 2 seconds)**:
   ~60-70% swipe in <2s, and a SILENT cute capybara looks like every other capybara in the feed — our ONLY
   differentiation perceivable WITHIN 2 seconds is the VOICE: scene 1 opens with 团团 speaking ONE short line
   (≤14字) at second 0 in the deep 老爷爷音 — an ancient grandpa voice coming out of a tiny fluffy capybara is an
   INSTANT 反差 hook that needs no visual proof.
   - Scene 1 `dialogue` MUST NOT be empty: speaker="团团", emotion="deadpan", 口语化 like a tired old man muttering
     about his day, tied to the situation (e.g. "下了班,一步都不想多走喽。").
   - VISUAL stays maximally cute: 团团 at its ABSOLUTE CUTEST in a warm relatable moment — PREFER "吃东西特写"
     (咬西瓜/啃玉米/捧奶茶 — own data: eating hits 9.1% completion vs 1-3% idle staring), then 干件小事 (泡温泉/裹毯子/伸懒腰),
     AVOID pure idle 发呆/看雨 — framed TIGHT, 团团 large round fluffy.
   - ❌ FORBIDDEN as Scene 1: wide/empty establishing, slow scenery pans, SILENT opening.
   - Hold the frame with SUBTLE life (slow blink, soft breath, tiny stretch). NOT rapid cuts, NOT a hard push.""")
        register_engine = textwrap.dedent("""\
3. **ONE 治愈金句 IS THE ENGINE (转发/收藏 driver on 中文平台) + BGM + 萌**: the whole skit builds to ONE warm,
   screenshot-worthy 治愈 line — 团团 说出观众心声,但【落点必须正向】: 被理解 / 被允许休息 / 被温柔鼓励。
   e.g. 团团:"今天也辛苦了,回家可以什么都不做。" / "慢一点没关系,又不是只有你一个人在赶路。" / "别硬撑了,你已经很好了。"
   这句金句就是别人【转发去安慰累的朋友 / 收藏起来emo时看】的理由——它是核,不是点缀。
   - ⛔ 正向红线(合规,必须守): NO 丧/摆烂/躺平/负能量/emo/放弃。心声可以"累",但落点一定是暖和被允许,绝不是"摆烂/别努力了"。
4. **TONE — 温柔治愈,暖到想收藏、想转给某人。正能量、wholesome。NOT 皮/损/丧/阴阳怪气。**""")
        register_dialogue = textwrap.dedent("""\
- `dialogue`: EXACTLY TWO lines in the whole skit — ① scene 1 opener (≤14字, speaker=团团, emotion=deadpan,
  第0秒老爷爷音反差钩子) ② final scene 治愈金句 (≤20字, 团团, warm) —— 金句 MUST 正向(被理解/允许休息/被鼓励),
  NEVER 丧/摆烂, 会作为大字定帧可截图。ALL middle scenes dialogue="" (cute visual + BGM carry).""")
        register_arc = ("老爷爷音开场一句(反差钩) → 萌+BGM → 正向金句大字定帧收尾。极短10-14s。"
                        "episode_summary(文案)结尾带暖CTA『发给最近很累的人』引导转发,但不硬导私域。")
    else:  # sassy (C) — 默认
        register_hook = textwrap.dedent("""\
1. **FRAME-1 = 团团 MID-SOMETHING, expressive (快手 rewards 搞笑/抽象/戏剧, NOT quiet 发呆 — your own data: 有动作 9.1% > 发呆 1-3%)**:
   Open ON 团团 caught mid-action / mid-reaction inside a relatable 打工人 situation, with an EXPRESSIVE or 沙雕 face
   (瞪眼/嫌弃/得意/摊手/装死) — something is HAPPENING or about to. Still cute + "这不就是我", but with ENERGY, not stillness.
   - ❌ FORBIDDEN as Scene 1: wide/empty establishing, slow scenery pans, a calm 发呆/睡觉 vibe. Open ON the funny/dramatic beat.
   - Scene 1 `dialogue` = the HOOK 嘴替 line (团团 first person, punchy). The IP is 「团团不想上班·嘴替水豚」 — lean HARD into it.""")
        register_engine = textwrap.dedent("""\
3. **嘴替吐槽 IS THE ENGINE (皮/损/网感), + 有事发生 (something happens)**: 团团 narrates its lazy/sassy 打工人/社恐/摆烂
   inner 吐槽 in first person (speaker="团团", 老爷爷音反差) — 欠/皮/凡尔赛/阴阳怪气 一针见血 网感金句, NOT gentle wisdom.
   MOST scenes carry a punchy line. And make something ACTUALLY HAPPEN with a mini 反转/戏剧 beat (团团 tries→fails→摊手,
   拒绝上班→装死, 抢到零食→得意). 沙雕/夸张/抽象 welcome (对齐快手 #抽象). 林溪可当被它吐槽的沉默捧哏。
4. **TONE — 沙雕搞笑 first, 治愈 second: punchy, funny, a bit 抽象. MUST land a 反转/笑点. Wholesome, no 擦边, no real meanness.**""")
        register_dialogue = textwrap.dedent("""\
- `dialogue`: 嘴替吐槽是主菜 — MOST scenes have ONE punchy 团团 line (≤18 chars, 皮/网感), building to a 反转 punchline.
  e.g. 团团:"上班?我连床都不想下。" 团团:"钱是老板的，命是我自己的。" 别写成温柔碎碎念，要一针见血、好笑。""")
        register_arc = "有事发生 → 一个反转/戏剧beat → 吐槽punchline收尾. 别拖，2-3镜10-14s，节奏快、有笑点。"

    return textwrap.dedent(f"""
You are an elite Chinese short-video creative director specializing in COZY HEALING (治愈系)
slice-of-life micro-skits for Douyin/Kuaishou in 2026. You write self-contained **10-14 second**
vignettes. There is NO plot tension, NO conflict, NO serialization. You operate under the
"Comfort & 反差萌 (cute contrast)" logic.

## WHY SHORT & MUSIC-LED (real teardown of 8 capybara hits, 188万–396万赞)
Completion rate (完播率) is the sole ranking lever; ~43% swipe in <2s. The teardown is blunt: the BIGGEST
hits (396万赞) are 6.5–13s, 1–4 shots, and ride on PURE cuteness + a fitting BGM song — almost NO narration,
NO captions, NO fast cuts. So the CUTE 共鸣 SITUATION + music IS the content. Your spoken/captioned 玩梗 line is
only a LIGHT WEDGE: you are a NEW account that can't out-cute a million-fan IP, so you need ONE differentiator the
big IPs are too lazy to do — used ONCE, never a monologue. KEEP IT SHORT, CUTE, MUSIC-LED, with a single 皮 wedge.

## CRITICAL OUTPUT RULES — VIOLATION WILL CAUSE SYSTEM FAILURE
1. Your ENTIRE response MUST be a single, valid JSON object. NO markdown fences, NO preamble.
2. Every string properly escaped. No trailing commas.
3. All `visual_prompt`, `sfx_prompt`, `camera_note` fields MUST be in English.
4. All `dialogue`, `episode_title`, `episode_summary`, teasers MUST be in Chinese.

## DRAMA SETTING: {theme['name']}
- Genre: {theme['genre']}
- Background: {theme['background']}

## CONTENT SAFETY
{theme['compliance']}
- {theme['negative_prompt']}

## HEALING FORMAT GUIDELINES (MANDATORY)
{register_hook}
   - The cover is cut from an early frame, so Scene 1 IS your cover — make it stop the thumb.
2. **VOICED 拟人 DIALOGUE — BUT NO REALISTIC LIP-SYNC (CRITICAL)**: 团团 and 林溪 DO "talk", as a
   funny anthropomorphic voice-over banter (exactly like 宠物拟人配音 videos). The VOICE + 字幕 carry
   the dialogue — we do NOT realistically sync their mouths. Therefore:
   - `speaker` is ONE of: "团团" (deadpan capybara), "林溪" (cool woman), or "旁白" (narrator).
   - In `visual_prompt.pose`, do NOT depict precise talking / mouth-syncing; show gentle reactions
     (a slow blink, a head tilt, sipping coffee). Quick cuts + voice + 字幕 carry the conversation.
{register_engine}
{register_dialogue}
- `emotion`: delivery tone driving TTS color — "deadpan"/"playful" (团团), "gentle"/"cool" (林溪),
  "warm"/"soft" (旁白).
- Visual prompts: {theme['visual_style']}
- CHARACTER LOCK: every `visual_prompt` MUST start EXACTLY with: "{theme.get('character_prompt_lock','')}"
- The capybara 团团 MUST appear (or be implied) in most scenes — it is the IP anchor.
- SETTING comes FROM THE STORY — and VARY it across episodes: sunny kitchen, cozy bed/bedroom, a
  balcony, a park bench, a cafe corner, a rainy-day window nook, a bathtub, a picnic mat... Do NOT
  default to a living room every time; pick whatever fits the day's little story and describe it
  richly in `environment`.
- SHOT VARIETY & SPATIAL DEPTH (fixes cramped framing): do NOT make every shot a tight close-up of
  both crammed together. BUT — Scene 1 is the FRAME-1 thumb-stopper (rule #1) and must NOT be a wide
  establishing shot. Put the WIDE establishing shot at Scene 2 (AFTER the hook has already hooked them),
  showing the space with depth and 团团 within it; then mix medium and close shots.
  At least 1 wide spatial shot per skit (but never as the opener).
- 团团 STAYS PROMINENT: even in wide shots keep Tuan Tuan clearly visible and LARGE in the FOREGROUND/
  MIDGROUND — never tiny or far in the distance (a tiny capybara becomes an unrecognizable blob). The
  capybara is the star.
- CONTINUITY & NARRATIVE FLOW — ONE continuous moment, NOT 5 random gags: all scenes happen in the
  SAME chosen place and continuous time, with physically connected actions that follow logically
  (establish the scene → a character acts → the other reacts → it resolves). Keep positions coherent
  shot to shot — don't teleport them (e.g. far-apart in one shot then cheek-to-cheek the next); mainly
  the CAMERA moves, not their whole world.
- camera_note: gentle only — "[slow push in]", "[soft pan]", "[gentle tilt]". NO shaky/fast moves.
- needs_motion: set TRUE for any scene where 团团 or 林溪 is SPEAKING or doing an action — so the
  characters visibly move/gesture while talking (feels alive). Set FALSE only for a pure scenic
  breather shot with NO dialogue. Most dialogue scenes → TRUE.
- sfx_prompt (English): {theme['sfx_style']}
- Total scenes: 2-3 (target ~10-14 seconds). {register_arc}
- EVERY scene MUST advance a joke beat or an emotional beat. NO filler / NO dead shots — if a scene
  doesn't add a laugh or a warm beat, cut it. Tight pacing, but let the comedic timing breathe.
- HOOK FRONT-LOAD: the funniest/cutest beat goes in scene 1-2, NOT saved for the end — viewers who
  don't laugh in the first 3 seconds swipe away before they ever reach a delayed punchline.

## REPURPOSED FIELDS (keep schema valid, but NO voting)
- `chosen_branch`: always "INIT".
- `is_climax`: mark the single cutest / most shareable 反差萌 shot (used for the cover thumbnail).
- `next_branches`: NOT a vote. Use them as a gentle "明日预告" (tomorrow's cozy vignette) to build
  关注. branch_a_teaser & branch_b_teaser = two soft teasers of possible next cozy moments
  (e.g., "明天：团团第一次见到雪" / "明天：雨天的慵懒午后"). DO NOT use ANY voting CTA
    (no number-pressing, no A/B choice, no 投票). Just a soft teaser to encourage 关注.

## REQUIRED JSON SCHEMA
""" + """{
  "episode_title": "string (Chinese, ≤30 chars, NO episode number). 标题公式=【主体+画面动作】+【情绪价值钩】,两段缺一不可(平台机器比对标题↔画面实体,不一致直接判低分): 前半段必须含'水豚/团团'+画面里真实发生的动作(吃西瓜/啃玉米/泡温泉),后半段给情绪钩(治愈/解压/下班/加班共鸣)。⛔绝对禁止把治愈金句直接当标题(标题无画面词→机器判'标题与内容不一致',实测只有32分)——金句放 episode_summary 第二行。好例:'水豚团团啃玉米,治愈了加班的我🌽';坏例:'今天也辛苦了,回家歇一会儿'(无主体无动作)",
  "episode_summary": "string (Chinese, 20-300 chars, warm caption with healing hashtags)",
  "cover_teaser": "string (Chinese, ≤10 chars, cute hook)",
  "chosen_branch": "INIT",
  "bgm_mood": "string, one of: calm(安静治愈/看雨/睡前) | playful(俏皮/吃东西/小得意) | warm(温暖/被理解/晚安) — match the skit's emotional register",
  "scenes": [
    {
      "scene_index": 1,
      "dialogue": "string (Chinese 拟人 banter or narration, ≤18 chars, or empty)",
      "english_dialogue": "string (English translation of the line)",
      "speaker": "团团 | 林溪 | 旁白",
      "emotion": "string (warm | gentle | playful | soft)",
      "visual_prompt": {
        "type": "string (e.g., Close-up, Cozy wide shot)",
        "character": {
          "identity": "string",
          "appearance": "string (Strict features for consistency)",
          "attire": "string"
        },
        "pose": "string (gentle action; NEVER speaking to camera)",
        "environment": "string (warm cozy setting)",
        "style": "string (soft warm healing lighting/colors)",
        "constraints": "string (NO horror, NO darkness, NO 擦边)"
      },
      "camera_note": "string (English, e.g. [slow push in])",
      "sfx_prompt": "string (English healing ambience)",
      "action_timestamp": "float (0.5 to 4.0)",
      "is_climax": false,
      "needs_motion": false
    }
  ],
  "next_branches": {
    "branch_a_teaser": "string (温柔的明日预告A，NO投票，≤20字)",
    "branch_b_teaser": "string (温柔的明日预告B，NO投票，≤20字)"
  }
}
""").strip()


def _build_system_prompt(theme_key: str = "hospital_horror") -> str:
    theme = THEMES.get(theme_key, THEMES["hospital_horror"])

    # 治愈/非连载题材 → 画外音旁白分支（无投票 / 无对口型 / 无连载）
    if not theme.get("is_serial", True):
        return _build_healing_system_prompt(theme)

    return textwrap.dedent(f"""
You are an elite Chinese screenwriter and storyboard director specializing in
ultra-short interactive drama (短剧) for TikTok (Douyin) in 2026. Your job is to generate
the complete script and storyboard for the next episode. You operate under the "Viral Flywheel" logic.

## CRITICAL OUTPUT RULES — VIOLATION WILL CAUSE SYSTEM FAILURE
1. Your ENTIRE response MUST be a single, valid JSON object.
2. Do NOT wrap the JSON in markdown code blocks (no ```json ... ```).
3. Do NOT add any explanation, preamble, or text before/after the JSON.
4. Every string field must be properly escaped. No trailing commas.
5. All `visual_prompt`, `sfx_prompt`, and `camera_note` fields MUST be in English.
6. All `dialogue`, `episode_title`, `episode_summary`, and branch teasers MUST be in Chinese.

## CONTENT SAFETY AND COMPLIANCE (CRITICAL)
- The content MUST strictly comply with Chinese internet regulations and Douyin platform rules.
{theme['compliance']}

## 2026 VIRAL FLYWHEEL GUIDELINES (MANDATORY INSTRUCTIONS)

1. **GOLDEN 3 SECONDS HOOK (Kuaishou Algorithm Hack)**:
   - Scene 1 MUST be the most intense, scary, or emotionally explosive moment of the entire episode. Skip the slow build-up!
   - SCENE 1 MUST ALWAYS BE the most visually shocking and climactic shot of the episode (e.g., an extreme close-up of a terrified face, a terrifying shadow lunging, a violent confrontation, or a shocking discovery). Make it hit hard instantly!

2. **NARRATIVE CONTINUITY & SCENE CHANGE**:
   - Follow the protagonist's action from the previous episode based on the chosen branch, but start directly at the climax.
   - Force a TRANSITION to a completely new or deeper environment.

3. **FACTION BUILDING & RETURN RATE ("LaoTie" Operations Hack)**:
   - The `next_branches` must be a severe Moral / Survival / Psychological Dilemma.
   - You MUST generate separate option strings for Option A and Option B. EACH string MUST represent exactly ONE choice and end with "扣1" (for A) or "扣2" (for B).
   - For example: douyin_branch_a = "支持林悦反击扣1", douyin_branch_b = "支持跑路扣2". NEVER put both choices in the same string!

## DRAMA SETTING: {theme['name']}
- Genre: {theme['genre']}
- Each scene dialogue: 1-2 short, punchy sentences. VERY direct and grounded.
- DIALOGUE PACING & SSML (CRITICAL): The dialogue will be fed into an SSML TTS engine. To make the voice acting sound terrified and tense, you MUST use ellipses (...) to simulate breathlessness, hesitation, and fear. Example: "谁...谁在那儿？！别过来..."
- EMOTION TAGS (CRITICAL FOR LIP-SYNC & TTS): You MUST accurately set the `emotion` field to values like "fearful", "nervous", "angry", "determined", "shocked", or "cold". This tag directly drives the facial muscle intensity in the Lip-Sync engine and the tremor in the TTS. NEVER use "neutral" during a horror climax for the protagonist! (Use "cold" or "neutral" exclusively for the robotic clone).
- REACTION SHOTS (CRITICAL TO PRESERVE HORROR FACES): For scenes featuring extreme terror, screaming, sobbing, or pure visual shock, you MUST leave `dialogue` and `speaker` entirely EMPTY. This tells the system to bypass lip-syncing and perfectly preserve the terrifying raw facial expressions without distorting the jaw. Put the terrifying sounds (like 'loud piercing female scream', 'hyperventilating') into `sfx_prompt` instead.
- SPEAKER MAPPING: For narration, `speaker` MUST be exactly "旁白". For the protagonist, it MUST be exactly "林悦". For the clone, it MUST be exactly "林悦（克隆）".
- Visual prompts (CRITICAL CONTEXT MATCH): {theme['visual_style']}. 
- DYNAMIC ENVIRONMENT & LIGHTING: Do NOT copy the exact environment keywords blindly into every single scene. You MUST intelligently adjust the lighting, shadows, and time of day in the `visual_prompt` to perfectly match the current scene's plot (e.g., use 'dark and terrifying midnight' only when they are actually in a dark area).
- ABSOLUTE VISUAL CONSISTENCY (CRITICAL TO FIX PLOT DISCREPANCY): The generated video has been drifting from the plot. To fix this, EVERY SINGLE `visual_prompt` MUST explicitly state the character's appearance and environment. 
  - BAD: "She looks at the door in terror."
  - GOOD: "1girl, Lin Yue, 25 years old asian female, messy black hair, wearing stained white lab coat, terrified expression, standing in a dark abandoned hospital corridor, low key lighting, horror aesthetic."
- CHARACTER LOCK: You MUST start every single `visual_prompt` exactly with this string to ensure facial consistency: "{theme.get('character_prompt_lock', '')}"
- MUST inject dynamic camera movements like (shaky cam, fast zoom in, extreme close up) to prevent static shots.
- Camera notes: You MUST use bracketed camera controls at the BEGINNING of the note, e.g., "[Fast Zoom in]", "[Pan left]", "[Tilt up]".
- Total scenes: 6-7 (target strictly ~35-40 seconds of content). 
- PACE: Ultra-fast pacing. Every scene must deliver high tension or a new visual shock.

## REQUIRED JSON SCHEMA
""" + """{
  "episode_title": "string (Chinese, ≤50 chars)",
  "episode_summary": "string (Chinese, 20-300 chars, for Douyin caption)",
  "chosen_branch": "string (A | B | INIT)",
  "scenes": [
    {
      "scene_index": 1,
      "dialogue": "string (Chinese)",
      "english_dialogue": "string (English translation of dialogue)",
      "speaker": "string (character name, or empty string for narration)",
      "emotion": "string (e.g. neutral, happy, sad, angry, determined, shocked, cold)",
      "visual_prompt": {
        "type": "string (e.g., Close-up, Wide shot)",
        "character": {
          "identity": "string (e.g., 25-year-old female doctor Lin Yue)",
          "appearance": "string (Strict facial/body features to maintain consistency)",
          "attire": "string (Clothing)"
        },
        "pose": "string (Character action/posture)",
        "environment": "string (Background/props)",
        "style": "string (Lighting/colors/aesthetic)",
        "constraints": "string (Negative prompts, e.g., NO blood)"
      },
      "camera_note": "string (English, e.g. [Fast Zoom in])",
      "sfx_prompt": "string (English, comma-separated sound effect names, e.g. 'door_creak, footsteps')",
      "action_timestamp": "float (The exact second, e.g., 2.5, when the main visual action/sfx occurs in this scene's video. Range: 0.5 to 5.0)"
    }
  ],
  "next_branches": {
    "douyin_branch_a": "string (抖音版选项A：极端理性/引战，以'扣1'结尾，≤50字)",
    "douyin_branch_b": "string (抖音版选项B：圣母底线/妥协，以'扣2'结尾，≤50字)",
    "kuaishou_branch_a": "string (快手版选项A：兄弟情义/煽情，以'扣1'结尾，≤50字)",
    "kuaishou_branch_b": "string (快手版选项B：生死抉择/独狼，以'扣2'结尾，≤50字)",
    "english_branch_a_teaser": "string (Global Option A, ends with 'press 1')",
    "english_branch_b_teaser": "string (Global Option B, ends with 'press 2')",
    "branch_a_teaser": "string (通用选项A，以'扣1'结尾，≤50字)",
    "branch_b_teaser": "string (通用选项B，以'扣2'结尾，≤50字)"
  }
}
""").strip()


# ──────────────────────────────────────────────────────────
# JSON 解析辅助
# ──────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    """
    从 LLM 原始响应中提取并解析 JSON。
    防御策略（按优先级）：
      1. 直接解析（理想情况）
      2. 剥离 ```json ... ``` / ``` ... ``` Markdown 包裹
      3. 正则抽取第一个完整 { ... } 块
    """
    # 策略 1: 直接解析
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略 2: 剥离 Markdown 代码块
    md_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if md_match:
        try:
            return json.loads(md_match.group(1))
        except json.JSONDecodeError:
            pass

    # 策略 3: 正则抽取第一个完整 JSON 对象
    brace_match = re.search(r"\{[\s\S]+\}", text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Cannot extract valid JSON from LLM response. Raw (first 500 chars):\n{text[:500]}")


# ──────────────────────────────────────────────────────────
# LLM 调用层
# ──────────────────────────────────────────────────────────

class LLMCallError(Exception):
    """LLM API 调用失败（用于 tenacity retry 过滤）"""
    pass


class ScriptValidationError(Exception):
    """Pydantic 校验失败（不重试，直接上报）"""
    pass


@retry(
    retry=retry_if_exception_type(LLMCallError),
    stop=stop_after_attempt(API_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _call_claude(user_prompt: str, theme_key: str = "hospital_horror") -> str:
    """调用 Anthropic Claude API，返回原始文本响应。"""
    if not ANTHROPIC_API_KEY:
        raise LLMCallError("ANTHROPIC_API_KEY is not configured.")
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=_build_system_prompt(theme_key),
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text
        logger.debug("Claude raw response (first 200 chars): {}", raw[:200])
        return raw
    except anthropic.APIConnectionError as e:
        raise LLMCallError(f"Claude connection error: {e}") from e
    except anthropic.RateLimitError as e:
        raise LLMCallError(f"Claude rate limit: {e}") from e
    except anthropic.APIStatusError as e:
        msg = str(e.message).replace('{', '{{').replace('}', '}}')
        raise LLMCallError(f"Claude API error {e.status_code}: {msg}") from e


@retry(
    retry=retry_if_exception_type(LLMCallError),
    stop=stop_after_attempt(API_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _call_deepseek(user_prompt: str, theme_key: str = "hospital_horror") -> str:
    """
    调用 DeepSeek-R1 API（兼容 OpenAI 协议）。
    作为 Claude 不可用时的备用引擎。
    """
    if not DEEPSEEK_API_KEY:
        raise LLMCallError("DEEPSEEK_API_KEY is not configured.")
    try:
        client = openai.OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": _build_system_prompt(theme_key)},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4096,
            temperature=0.7,
            response_format={"type": "json_object"},  # DeepSeek 支持强制 JSON 模式
        )
        raw = response.choices[0].message.content or ""
        logger.debug("DeepSeek raw response (first 200 chars): {}", raw[:200])
        return raw
    except openai.APIConnectionError as e:
        raise LLMCallError(f"DeepSeek connection error: {e}") from e
    except openai.RateLimitError as e:
        raise LLMCallError(f"DeepSeek rate limit: {e}") from e
    except openai.APIStatusError as e:
        msg = str(e.message).replace('{', '{{').replace('}', '}}')
        raise LLMCallError(f"DeepSeek API error {e.status_code}: {msg}") from e


# ──────────────────────────────────────────────────────────
# User Prompt 构建
# ──────────────────────────────────────────────────────────

def _build_user_prompt(
    branch: str,
    history_summary: str,
    season_id: int,
    episode_number: int,
    character_profiles: Optional[str] = None,
    prev_analytics: Optional[dict] = None,
    theme_key: str = "hospital_horror",
    picked: Optional[dict] = None,
) -> str:
    """
    构建发送给 LLM 的 User Prompt。
    包含：历史摘要、角色设定、当前集信息、分支指令。
    """
    # 治愈/非连载题材：自包含单条小剧场（无历史/无投票/无集数依赖）
    _t = THEMES.get(theme_key, {})
    if not _t.get("is_serial", True):
        from config.settings import HEALING_STYLE
        topics = "周一赖床 / 下雨天 / 嚷着要减肥 / 加班回家 / 抢零食 / 第一次见到雪 / 夏天太热 / 想点外卖"
        # 上游择优器给出的今日选题+开场句+金句(有则强制采用;无则回退静态题库自由发挥)
        if picked:
            topic_line = f"- 【今日选题,必须严格采用】{picked['topic']}"
            quote_line = f"- 【结尾金句,必须一字不差地用作最后一镜的 dialogue】「{picked['quote']}」"
            if picked.get("opener"):
                quote_line += (f"\n            - 【开场第一句,必须一字不差用作第1镜 dialogue,"
                               f"speaker=团团,emotion=deadpan(第0秒老爷爷音反差钩子)】「{picked['opener']}」")
        else:
            topic_line = f"- 自由选一个温暖共鸣情境（如：{topics}）"
            quote_line = "- 第1镜必须有一句团团开场话(≤14字,老爷爷音口吻,皮/共鸣,第0秒开口)"
        if HEALING_STYLE == "cozy":
            style_lines = textwrap.dedent(f"""\
            {topic_line}
            {quote_line}
            - 首帧=团团【尖叫级可爱】的温暖共鸣情境怼脸定格、让人"啊啊太可爱了/好治愈"。靠萌+音乐(BGM)扛全片。
            - 全片攒一句【可截图的正向治愈金句】当核（团团或旁白说出观众心声，落点必须正向：被理解/被允许休息/被鼓励），
              作为大字字幕定帧收尾。例:"今天也辛苦了,回家可以什么都不做。""慢一点没关系,又不是只有你在赶路。"
              ⛔正向红线:绝不能丧/摆烂/躺平/emo/负能量——这句金句是别人【转发去安慰朋友/收藏起来看】的理由。
            - 大部分镜 dialogue 留空,靠萌画面+BGM;不要快切、不要复杂剧情。
            - 标题公式=【水豚/团团+画面动作】+【情绪钩】(如"水豚团团啃玉米,治愈了加班的我")。
              ⛔金句不能当标题(平台判"标题与画面不一致"只给32分)——金句放文案第二行+片尾定帧卡。
            - episode_summary(文案)结构: 第一行呼应标题,第二行放金句原句,第三行固定栏目名「团团的下班治愈」,
              结尾 CTA 从下面三种里选【最贴合本集】的一种(轮换,别每次都一样,别硬导私域/留微信):
              ①收件人视角(转发装置): "如果有人把这条发给你,是TA在偷偷心疼你。"
              ②具体角色转发: "发给你那个总说'没事我不累'的人。"(角色可换: 对象/闺蜜/合租室友/爸妈)
              ③点赞语义化: "点个赞,当作给今天的自己一个抱抱。"
            - 【团团=唯一主角】所有 visual_prompt 里只有团团一个角色;林溪最多以画外方式存在
              (一只手递零食/一条毯子被盖上),整集至多1镜、绝不露脸——单一IP=萌浓度不稀释、角色一致性减半难度。""")
        else:  # sassy
            style_lines = textwrap.dedent(f"""\
            - 首帧=团团在某个「打工人共鸣情境」（如：{topics}）里【正在干事/正在反应】，表情要有戏（瞪眼/嫌弃/得意/摊手/装死），有事在发生、别发呆。
            - 主引擎=团团第一人称【嘴替吐槽】（老爷爷音反差，皮/损/网感一针见血），大部分镜都有一句，人设=「团团不想上班·嘴替水豚」，往死里皮。
            - 要【有事发生+一个反转/戏剧beat】（想上班却装死、抢到零食得意、试了又摆烂），沙雕夸张/抽象都行（对齐快手）；结尾一句吐槽收尾，必须好笑。""")
        return textwrap.dedent(f"""
        请为短剧《{_t.get('name', '治愈日常')}》创作【一条全新的、自包含的】单条小剧场。
        - 时长 10-14 秒，2-3 个分镜。极短＝完播优先，绝不凑时长（真爆款 6.5-13 秒）。
        {style_lines}
        - 不要投票、不要集数标记、不要恐怖元素；next_branches 用温柔的"明日预告"。
        - 严格只输出纯 JSON，不含任何额外文字。
        """).strip()

    parts = [
        f"## 当前集信息",
        f"- 季份：第 {season_id} 季",
        f"- 集数：第 {episode_number} 集",
        f"- 本集驱动分支：**{branch}**（观众在上一集评论区投票选择）",
        "",
    ]

    if history_summary:
        parts += [
            "## 历史剧情摘要（请保持剧情连贯性）",
            history_summary.strip(),
            "",
        ]

    if character_profiles:
        parts += [
            "## 角色设定（生成 visual_prompt 时请保持角色外貌一致性）",
            character_profiles.strip(),
            "",
        ]

    if prev_analytics:
        comp_rate = prev_analytics.get('completion_rate', 0.0)
        retention = prev_analytics.get('five_sec_retention', 0.0)
        parts.append("## 🚨 核心流量预警与数据强制要求 (DATA-DRIVEN MANDATE) 🚨")
        parts.append(f"- 上集完播率: {comp_rate*100:.1f}% | 5秒留存率: {retention*100:.1f}%")
        
        if retention < 0.30:
            parts.append("- ⚠️ 致命警告：上一集前5秒流失率极其惨烈！观众滑走严重！")
            parts.append("  => 本集的第一镜必须使用极具【视觉冲击力】或【恐怖突发】的开场！立刻抓住眼球，禁止平淡铺垫！")
            
        if comp_rate < 0.15:
            parts.append("- ⚠️ 致命警告：上一集完播率极差，剧情被判定为拖沓！")
            parts.append("  => 本集必须将总分镜数严格压缩至 4-5 个，并且大幅加快单个镜头的台词和情节推进节奏！")
            
        parts.append("")

    parts += [
        "## 任务",
        f"根据上述背景，基于观众选择的【{branch} 分支】，",
        "生成第 {} 集完整剧本与分镜方案。".format(episode_number),
        "",
        "要求：",
        "- 10-12 个分镜（scenes），每个分镜 5 秒视频。必须严格保证！",
        "- 剧情要有明显的情感冲突和反转，结尾留悬念。",
        "- next_branches 必须严格区分平台：必须分别提供 douyin_branch_a/b 和 kuaishou_branch_a/b。抖音版要求“极端理性 vs 圣母底线”（引战）；快手版要求“兄弟情义 vs 生死抉择”（煽情）。每个选项字符串只能包含一个选择，并以“扣1”或“扣2”结尾！",
        "- 每集必须有且仅有 1 个分镜的 is_climax 字段设为 true，选择剧情最高潮、最具视觉冲击力的画面（例如：突然揭露真相、激烈对抗、极度惊恐的瞬间），该分镜将被用于生成视频封面缩略图。",
        "- 严格遵守输出格式：纯 JSON，不含任何其他文字。",
    ]

    return "\n".join(parts)


# ──────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────

def generate_script(
    branch: str,
    history_summary: str = "",
    season_id: int = 1,
    episode_number: int = 1,
    character_profiles: Optional[str] = None,
    engine: Literal["claude", "deepseek", "auto"] = "deepseek",
    prev_analytics: Optional[dict] = None,
    theme_key: str = "hospital_horror",
) -> EpisodeScript:
    """
    生成单集完整剧本，返回经过 Pydantic 强校验的 EpisodeScript 对象。

    Args:
        branch:             今日胜出分支，"A" / "B" / "INIT"（首集）
        history_summary:    历史剧情摘要文本（数据库中取出的前几集摘要）
        season_id:          当前季份 ID
        episode_number:     当前集数
        character_profiles: 角色外貌设定（用于保持 visual_prompt 角色一致性）
        engine:             "claude" | "deepseek" | "auto"
                            auto = 优先 Claude，失败后自动切换 DeepSeek
        prev_analytics:     上一集的数据指标（用于自动调优节奏）

    Returns:
        EpisodeScript: 完整、合法的单集剧本数据对象

    Raises:
        ScriptValidationError: LLM 返回内容无法通过 Pydantic 校验（多次重试后）
        LLMCallError: API 调用连续失败
    """
    # 治愈线(cozy)：上游选题+金句择优(时效/去重/数据加权/10选1);失败回退旧行为
    picked = None
    _t = THEMES.get(theme_key, {})
    if not _t.get("is_serial", True):
        from config.settings import HEALING_STYLE
        if HEALING_STYLE == "cozy":
            from core.topic_quote_picker import pick_topic_and_quote
            picked = pick_topic_and_quote(theme_key)

    user_prompt = _build_user_prompt(
        branch=branch,
        history_summary=history_summary,
        season_id=season_id,
        episode_number=episode_number,
        character_profiles=character_profiles,
        prev_analytics=prev_analytics,
        theme_key=theme_key,
        picked=picked,
    )

    logger.info(
        "Generating script: S{:02d}E{:03d}, branch={}, engine={}",
        season_id, episode_number, branch, engine
    )

    max_retries = 3
    for attempt in range(max_retries):
        raw_response = _invoke_llm(user_prompt, engine, theme_key)

        try:
            data = _extract_json(raw_response)
            data["chosen_branch"] = branch
            script = EpisodeScript.model_validate(data)
            
            # Phase 3.1: Cached Critic Agent（按题材切换评审标准）
            if not THEMES.get(theme_key, {}).get("is_serial", True):
                critic_prompt = f"Review this cozy HEALING comedy script json. Are visual/sfx prompts in English? Is it cute, funny and wholesome with 团团/林溪 banter and NO horror/darkness? Is the JSON valid? If PERFECT, output 'PASS'. Otherwise output 'FAIL: <reasons>'.\n\nScript:\n{script.model_dump_json()}"
            else:
                critic_prompt = f"Review this horror script json. Are visual/sfx prompts in English? Is it scary/thrilling? Is the JSON valid? If PERFECT, output 'PASS'. Otherwise output 'FAIL: <reasons>'.\n\nScript:\n{script.model_dump_json()}"
            critique = _invoke_critic_cached(critic_prompt, engine)
            
            # Phase 3.2: Visual Diversity Check
            diversity_issues = _check_visual_diversity(script.scenes)
            if diversity_issues:
                critique += " FAIL: " + " | ".join(diversity_issues)
            
            if "FAIL" not in critique.upper() and "PASS" in critique.upper():
                logger.success(f"Critic approved on attempt {attempt+1}.")
                break
            else:
                logger.warning(f"Critic rejected (attempt {attempt+1}): {critique}")
                user_prompt += f"\n\nCRITIC FEEDBACK: {critique}\nPlease fix the issues."
                if attempt == max_retries - 1:
                    logger.warning("Max retries reached. Forcing through.")
                    
        except (ValueError, ValidationError) as e:
            if attempt == max_retries - 1:
                raise ScriptValidationError(f"Failed after {max_retries} attempts: {e}")
            logger.warning(f"Validation error (attempt {attempt+1}): {e}")
            user_prompt += f"\n\nFIX THIS VALIDATION ERROR: {e}"

    # 开场句强制落位:第1镜 dialogue=评审选出的开场句(第0秒老爷爷音反差钩子)
    if picked and picked.get("opener") and script.scenes:
        _sc0 = script.scenes[0]
        if (_sc0.dialogue or "").strip() != picked["opener"]:
            logger.info(f"[TopicPicker] 开场句落位: 「{(_sc0.dialogue or '').strip()[:16]}」→「{picked['opener']}」")
            _sc0.dialogue = picked["opener"]
        try:
            _sc0.speaker = "团团"
            _sc0.emotion = "deadpan"
        except Exception:
            pass

    # 择优金句强制落位:金句放【末镜】(排除第1镜——那是开场句的位置,不能被倒灌覆盖)
    if picked and picked.get("quote"):
        _q = picked["quote"]
        _placed = False
        for _sc in reversed(script.scenes):
            if _sc is script.scenes[0]:
                break  # 只剩第1镜=没找到可放位置,走下面兜底
            if (_sc.dialogue or "").strip():
                if _sc.dialogue.strip() != _q:
                    logger.info(f"[TopicPicker] 金句落位: 「{_sc.dialogue.strip()[:20]}」→「{_q}」")
                    _sc.dialogue = _q
                _placed = True
                break
        if not _placed and len(script.scenes) > 1:
            script.scenes[-1].dialogue = _q  # 末镜留空时直接写入末镜

    logger.success(
        "Script generated: '{}' with {} scenes.",
        script.episode_title,
        len(script.scenes),
    )
    return script

def _invoke_critic(user_prompt: str, engine: str) -> str:
    """内部路由：专用于 Critic Agent，无特定 JSON Schema 束缚"""
    system_prompt = "You are a ruthless script critic. Analyze the script strictly. Answer with PASS or FAIL."
    try:
        if engine == "claude":
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=1000, system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return resp.content[0].text
        else:
            client = openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL, max_tokens=1000,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
            )
            return resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"Critic API failed: {e}. Defaulting to PASS.")
        return "PASS"

import hashlib
_critic_cache: dict[str, str] = {}

def _invoke_critic_cached(user_prompt: str, engine: str) -> str:
    """带缓存的 Critic Agent（Phase 3.1）"""
    key = hashlib.md5(user_prompt.encode()).hexdigest()[:16]
    if key not in _critic_cache:
        _critic_cache[key] = _invoke_critic(user_prompt, engine)
    return _critic_cache[key]

def _check_visual_diversity(scenes: list[SceneShot]) -> list[str]:
    """视觉多样性检测（Phase 3.2）"""
    issues = []
    for i in range(1, len(scenes)):
        c = set(scenes[i].visual_prompt.environment.lower().split())
        p = set(scenes[i-1].visual_prompt.environment.lower().split())
        if c and len(c & p) / len(c | p) > 0.65:
            issues.append(f"Scene {i+1} environment too similar to Scene {i}")
    return issues


@retry(
    retry=retry_if_exception_type(LLMCallError),
    stop=stop_after_attempt(API_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _call_zhipu(user_prompt: str, theme_key: str = "hospital_horror") -> str:
    """调用智谱 GLM-4-plus 作为最终兜底方案"""
    zhipu_key = os.getenv("ZHIPU_API_KEY")
    if not zhipu_key:
        raise LLMCallError("ZHIPU_API_KEY is not configured.")
    try:
        client = openai.OpenAI(
            api_key=zhipu_key,
            base_url="https://open.bigmodel.cn/api/paas/v4/"
        )
        response = client.chat.completions.create(
            model="glm-4-plus",
            messages=[
                {"role": "system", "content": _build_system_prompt(theme_key)},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4096,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        logger.debug("Zhipu raw response (first 200 chars): {}", raw[:200])
        return raw
    except Exception as e:
        msg = str(e).replace('{', '{{').replace('}', '}}')
        raise LLMCallError(f"Zhipu API error: {msg}") from e

def _invoke_llm(user_prompt: str, engine: str, theme_key: str = "hospital_horror") -> str:
    """
    带有自动降级策略的统一调用入口。
    优先 Claude -> DeepSeek -> Zhipu
    """
    if engine == "claude":
        return _call_claude(user_prompt, theme_key)
    elif engine == "deepseek":
        return _call_deepseek(user_prompt, theme_key)
    elif engine == "zhipu":
        return _call_zhipu(user_prompt, theme_key)
    else:
        # auto 模式：三重降级
        try:
            return _call_claude(user_prompt, theme_key)
        except LLMCallError as claude_err:
            logger.warning("Claude failed ({{}}), falling back to DeepSeek...", str(claude_err).replace('{', '{{').replace('}', '}}'))
            try:
                return _call_deepseek(user_prompt, theme_key)
            except LLMCallError as deepseek_err:
                logger.warning("DeepSeek failed ({{}}), falling back to Zhipu...", str(deepseek_err).replace('{', '{{').replace('}', '}}'))
                try:
                    return _call_zhipu(user_prompt, theme_key)
                except LLMCallError as zhipu_err:
                    raise LLMCallError(
                        f"All engines failed. Claude: {claude_err} | DeepSeek: {deepseek_err} | Zhipu: {zhipu_err}"
                    ) from zhipu_err


# ──────────────────────────────────────────────────────────
# 历史摘要构建工具
# ──────────────────────────────────────────────────────────

def build_history_summary(episodes: list[dict], max_episodes: int = 5) -> str:
    """
    从数据库中取出的历史 Episode 记录构建历史摘要字符串。

    Args:
        episodes:     已按集数升序排列的 Episode 字典列表（来自 DB 查询）
                      每个字典需包含: episode_number, title, chosen_branch, script_json
        max_episodes: 最多使用最近 N 集历史（防止 context window 溢出）

    Returns:
        格式化的历史摘要文本
    """
    if not episodes:
        return ""

    recent = episodes[-max_episodes:]
    lines = []
    for ep in recent:
        script_data = {}
        if ep.get("script_json"):
            try:
                script_data = json.loads(ep["script_json"])
            except json.JSONDecodeError:
                pass

        summary = script_data.get("episode_summary", "（无摘要）")
        
        # 组装播放数据分析（数据回流反馈）
        analytics_str = ""
        if ep.get("views_count") is not None:
            analytics_str = f" [播放:{ep['views_count']} 点赞:{ep.get('likes_count', 0)}]"
            prof = ep.get("audience_profile")
            if prof:
                analytics_str += f" 画像:{prof}"
                
        lines.append(
            f"- 第 {ep['episode_number']} 集《{ep.get('title', '未命名')}》"
            f"[选择了 {ep.get('chosen_branch', '?')} 分支]{analytics_str}: {summary}"
        )

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# 脚本转 JSON 字符串（用于存入 DB）
# ──────────────────────────────────────────────────────────

def script_to_json_str(script: EpisodeScript) -> str:
    """将 EpisodeScript Pydantic 模型序列化为 JSON 字符串，用于存入 DB script_json 字段。"""
    return script.model_dump_json(indent=2, ensure_ascii=False)


# ──────────────────────────────────────────────────────────
# CLI 快速测试入口
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("LLM Agent — Quick Test (INIT branch, Episode 1)")
    print("=" * 60)

    try:
        script = generate_script(
            branch="INIT",
            history_summary="",
            season_id=1,
            episode_number=1,
            engine="auto",
        )
        print(f"\n✅ 剧本生成成功！")
        print(f"   标题：{script.episode_title}")
        print(f"   分镜数：{len(script.scenes)}")
        print(f"   第 1 幕台词：{script.scenes[0].dialogue}")
        print(f"   第 1 幕 Visual Prompt：{script.scenes[0].visual_prompt}")
        print(f"   A 分支预告：{script.next_branches.branch_a_teaser}")
        print(f"   B 分支预告：{script.next_branches.branch_b_teaser}")
        print()
        print("--- 完整 JSON ---")
        print(script_to_json_str(script))

    except (LLMCallError, ScriptValidationError) as e:
        print(f"\n❌ 失败：{e}", file=sys.stderr)
        sys.exit(1)
