"""
core/teardown —— 爆款拆解器（自进化内容引擎的「脑」· MVP）
==========================================================
输入：同赛道爆款视频文件（或你自己已发的成片）
输出：结构化 DNA（节奏/钩子/配音）→ dna.json + 对比 report.md

定位：闭环飞轮的 ② 拆解段。先把"爆款怎么做的"从猜变成可复制的结构化规则，
后续 ④ 提炼 → ⑤ 回灌进 llm_agent。详见记忆 [[image-pose-deviation-plan]] 同级的内容引擎规划。
"""
from .analyzer import analyze_video

__all__ = ["analyze_video"]
