"""
core/topic_quote_picker.py
==========================
治愈线「选题 + 金句」上游择优器(内容生成的脑前叶)。

解决三个实证问题:
  1. 选题与自己数据脱节 —— 情境池按自己完播数据加权(吃东西9.1% > 小动作4% > 发呆1-3%)
  2. 无时效性 —— 注入 今天周几/季节,周一发周一梗、雨季发雨梗,共鸣倍增
  3. 重复无记忆 —— 从 DB 查最近已发题材禁止重复("下雨天"已二刷过)

流程: LLM#1 高温出「1个选题 + 10句候选金句」→ LLM#2 低温评审(共鸣/可截图/正向/不俗套)择优。
纯文本调用,成本≈0。任何失败返回 None(fail-open,回退到旧的静态题库自由发挥,绝不阻断管线)。
"""
import json
import re
from datetime import datetime
from typing import Optional

from loguru import logger


# 情境池:权重来自自己账号的真实完播数据(2026-07 快手后台)
_SITUATION_POOL = """
【高权重·优先选】(自己数据完播最高: 吃西瓜9.1%/吃冰淇淋互动最高)
- 吃东西特写: 咬西瓜/啃玉米/捧着奶茶小口嘬/偷吃零食被抓/吹热汤
【中权重】(数据~4%)
- 干件小事: 泡温泉/敷面膜/伸懒腰/裹进毯子/追着晒太阳挪窝
【低权重·少用】(数据1-3%,且"下雨天"已发过两次)
- 纯静态: 发呆/看雨/睡觉
"""

_GEN_PROMPT = """你是一个百万粉治愈账号的内容策划。为「团团」(一只治愈系水豚,IP=温柔嘴替)策划今天这条视频。

## 受众(快手,偏年轻,别只盯办公室白领)
学生党 / 小镇青年 / 年轻打工人。金句说中的人群要【逐日轮换】:
有时说学生(考试周熬夜/晚自习回家/被作业追着跑),有时说上班的(下晚班/挤末班车),
有时说所有人(被爸妈念叨/一天没说上一句真心话/热天挤公交)。"办公室加班"只是其中一种,别每天都是它。

## 今天的现实语境(必须利用,共鸣倍增器)
- 日期: {date_str} ({weekday})
- 季节: {season}
- 提示: 周一贴"新的一周好累"、周五贴"熬到周末了"、夏天贴"热"——视频在晚间(19-21点)被刷到,贴"一天结束"的情绪

## 情境池(按账号真实完播数据加权,优先高权重)
{situation_pool}

## 最近已发过的题材(【禁止重复】,尤其禁止再发下雨天/赖床)
{recent_titles}

## 任务
输出 JSON:
1. "topic": 今天的选题,一句话描述画面情境(结合今天是{weekday}/{season},从高权重情境池选,不与已发重复)
2. "candidates": 10句候选【正向治愈金句】,每句≤20字。要求:
   - 团团的口吻说出打工人/学生党的心声,但落点必须正向: 被理解/被允许休息/被温柔鼓励
   - 【点赞扳机=说中,不是安慰】必须具体到一个"微瞬间"——那种没人说过但人人都干过的小动作/小心理。
     泛泛安慰(❌"今天也辛苦了,歇会儿吧")远弱于精确看穿(✅"到家先在车里坐五分钟,再假装刚到。")
   - ⛔禁止: 丧/摆烂/躺平/emo/负能量/说教/俗套鸡汤(如"加油""明天会更好"直接淘汰)
   - 好的样子: "到家先在车里坐五分钟,再假装刚到。" / "慢一点没关系,又不是只有你在赶路。"
   - 10句要风格各异(有的戳心、有的软萌、有的带一点点俏皮),别同一个句式复制10遍
3. "openers": 5句候选【开场第一句】,每句≤14字——视频第0秒由团团(深沉老爷爷音)开口说的第一句话,
   反差(苍老大爷嗓×软萌小水豚)本身就是钩子。要求: 和选题情境强相关、口语化像随口一句、
   皮/亲切/带点共鸣,禁丧禁说教。例:"忙了一天,可算能瘫会儿喽。" / "这口瓜,比啥都甜呐。"
只输出 JSON,不要任何其他文字。"""

_JUDGE_PROMPT = """你是一个爆款文案评审。

【任务1】下面是同一情境的10句候选治愈金句,按四项打分(各1-10)选最佳:
A微瞬间精确度(是否说中一个具体的、没人说过但人人都干过的小瞬间——泛泛安慰给低分) B可截图性(单独截出来发朋友圈成立吗) C正向合规(有一丝丧/摆烂即0分) D不俗套(鸡汤套话即低分)

【任务2】下面是5句候选开场句(视频第0秒由深沉老爷爷音从一只软萌小水豚嘴里说出),选最佳:
标准=老爷爷音念出来反差感/像真人随口说的口语自然度/让人想再听两秒

情境: {topic}
金句候选:
{candidates}
开场句候选:
{openers}

输出 JSON: {{"best": "<金句原文一字不差>", "best_opener": "<开场句原文一字不差>", "reason": "<一句话理由>"}}
只输出 JSON。"""


def _season_of(m: int) -> str:
    return {12: "冬天", 1: "冬天", 2: "冬天", 3: "春天", 4: "春天", 5: "春天",
            6: "夏天", 7: "夏天", 8: "夏天", 9: "秋天", 10: "秋天", 11: "秋天"}[m]


def _recent_titles(theme_key: str, limit: int = 10) -> list[str]:
    """查最近已发题材用于去重;DB 失败返回空(fail-open)。"""
    try:
        from database.db_session import get_session
        from database.models import Episode
        with get_session() as session:
            rows = (session.query(Episode.title)
                    .filter(Episode.theme_key == theme_key, Episode.title.isnot(None))
                    .order_by(Episode.id.desc()).limit(limit).all())
            return [r[0] for r in rows if r[0]]
    except Exception as e:
        logger.warning(f"[TopicPicker] 查历史题材失败(跳过去重): {e}")
        return []


def _chat(messages: list[dict], temperature: float) -> str:
    import openai
    from config.settings import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    client = openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL, messages=messages, max_tokens=1200,
        temperature=temperature, response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def pick_topic_and_quote(theme_key: str = "capybara_healing") -> Optional[dict]:
    """返回 {"topic": ..., "quote": ...};任何失败返回 None(回退旧行为)。"""
    try:
        now = datetime.now()
        weekday = "周" + "一二三四五六日"[now.weekday()]
        gen_prompt = _GEN_PROMPT.format(
            date_str=now.strftime("%Y-%m-%d"),
            weekday=weekday,
            season=_season_of(now.month),
            situation_pool=_SITUATION_POOL,
            recent_titles="\n".join(f"- {t}" for t in _recent_titles(theme_key)) or "(暂无)",
        )
        data = _parse_json(_chat([{"role": "user", "content": gen_prompt}], temperature=0.9))
        topic = str(data.get("topic", "")).strip()
        candidates = [str(c).strip() for c in data.get("candidates", []) if str(c).strip()]
        openers = [str(c).strip() for c in data.get("openers", []) if str(c).strip()]
        if not topic or len(candidates) < 3:
            logger.warning("[TopicPicker] 候选不足,回退旧行为")
            return None

        judge = _parse_json(_chat([{"role": "user", "content": _JUDGE_PROMPT.format(
            topic=topic,
            candidates="\n".join(f"{i+1}. {c}" for i, c in enumerate(candidates)),
            openers="\n".join(f"{i+1}. {c}" for i, c in enumerate(openers)) or "(无)",
        )}], temperature=0.2))
        best = str(judge.get("best", "")).strip().strip('"“”「」')
        # 评审输出必须命中候选之一(防它自己现编);没命中就取第一句
        quote = best if best in candidates else candidates[0]
        if len(quote) > 24:
            quote = quote[:24]
        best_op = str(judge.get("best_opener", "")).strip().strip('"“”「」')
        opener = best_op if best_op in openers else (openers[0] if openers else "")
        if len(opener) > 16:
            opener = opener[:16]

        logger.success(f"[TopicPicker] 选题:「{topic}」 开场:「{opener}」 金句:「{quote}」 ({judge.get('reason','')[:40]})")
        return {"topic": topic, "quote": quote, "opener": opener}
    except Exception as e:
        logger.warning(f"[TopicPicker] 择优失败(回退旧行为): {e}")
        return None
