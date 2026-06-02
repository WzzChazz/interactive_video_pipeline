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

    visual_prompt: str = Field(
        ..., min_length=20, max_length=800,
        description="图像生成 Prompt（英文）：场景、角色、光线、构图、风格关键词"
    )
    camera_note: str = Field(
        default="static shot",
        description="摄影机运动描述（英文），如 'slow push in', 'pan left', 'handheld shake'"
    )
    sfx_prompt: str = Field(
        default="ambient silence",
        description="环境音效 Prompt（英文），如 'heavy rain on rooftop, distant thunder'"
    )

    @field_validator("visual_prompt", "sfx_prompt", "camera_note", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

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
    chosen_branch: str = Field(..., description="本集驱动分支：A / B / INIT")
    scenes: list[SceneShot] = Field(
        ..., min_length=4, max_length=7,
        description="分镜列表，必须严格输出 6-7 个分镜以确保极致快节奏（35-40秒）"
    )
    next_branches: NextBranches = Field(..., description="下一集 A/B 分支悬念预告")

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

def _build_system_prompt(theme_key: str = "hospital_horror") -> str:
    theme = THEMES.get(theme_key, THEMES["hospital_horror"])
    
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
   - The `next_branches` must be a severe Moral / Survival / Psychological Dilemma with HIGH EMOTIONAL CONFLICT (e.g., Expose the truth violently vs. Run away in fear).
   - The teasers MUST explicitly tell the audience to vote by typing "1" or "2" in the comments, e.g., "支持林悦反击扣1，支持跑路扣2！"

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
      "visual_prompt": "string (English, ≥20 chars, strict visual consistency lock)",
      "camera_note": "string (English, e.g. [Fast Zoom in])",
      "sfx_prompt": "string (English, comma-separated sound effect names, e.g. 'door_creak, footsteps')",
      "action_timestamp": "float (The exact second, e.g., 2.5, when the main visual action/sfx occurs in this scene's video. Range: 0.5 to 5.0)"
    }
  ],
  "next_branches": {
    "branch_a_teaser": "string (Chinese, ≤200 chars)",
    "branch_b_teaser": "string (Chinese, ≤200 chars)",
    "english_branch_a_teaser": "string (English)",
    "english_branch_b_teaser": "string (English)"
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
        raise LLMCallError(f"Claude API error {e.status_code}: {e.message}") from e


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
        raise LLMCallError(f"DeepSeek API error {e.status_code}: {e.message}") from e


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
) -> str:
    """
    构建发送给 LLM 的 User Prompt。
    包含：历史摘要、角色设定、当前集信息、分支指令。
    """
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
        "- next_branches 必须严格区分平台：抖音版必须是“极端理性 vs 圣母底线”的道德困境（引战）；快手版必须是“兄弟情义 vs 生死抉择”的热血困境（煽情）。绝对不能是简单的开门关门！",
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
    user_prompt = _build_user_prompt(
        branch=branch,
        history_summary=history_summary,
        season_id=season_id,
        episode_number=episode_number,
        character_profiles=character_profiles,
        prev_analytics=prev_analytics,
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
            
            # Critic Agent 打分
            critic_prompt = f"Review this horror script json. Are visual/sfx prompts in English? Is it scary/thrilling? Is the JSON valid? If PERFECT, output 'PASS'. Otherwise output 'FAIL: <reasons>'.\n\nScript:\n{script.model_dump_json()}"
            critique = _invoke_critic(critic_prompt, engine)
            
            if "PASS" in critique.upper():
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


def _invoke_llm(user_prompt: str, engine: str, theme_key: str = "hospital_horror") -> str:
    """
    带有自动降级策略的统一调用入口。
    优先 Claude，如果失败或未配置则回退到 DeepSeek。
    """
    if engine == "claude":
        return _call_claude(user_prompt, theme_key)
    elif engine == "deepseek":
        return _call_deepseek(user_prompt, theme_key)
    else:
        # auto 模式：优先 Claude，失败切 DeepSeek
        try:
            return _call_claude(user_prompt, theme_key)
        except LLMCallError as claude_err:
            logger.warning("Claude failed ({}), falling back to DeepSeek...", claude_err)
            try:
                return _call_deepseek(user_prompt, theme_key)
            except LLMCallError as deepseek_err:
                raise LLMCallError(
                    f"Both engines failed. Claude: {claude_err} | DeepSeek: {deepseek_err}"
                ) from deepseek_err


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
