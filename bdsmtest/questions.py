"""BDSMTest 模式与量表常量。

题目（93 题 + 顺序 + wording）和原型名称都在请求时由原站以中文（lang=zh）
实时返回，本模块不再维护本地翻译表——既符合"用原站中文、不自译"的约定，
也避免原站题库改版后本地翻译与题号错位。
"""

VALID_MODES = ("normal", "fast")
FAST_MODE = "fast"

# 1-7 李克特量表说明（原站量表）。原站算分要求每题都有 1-7 的答案，
# 0 会被打分接口当作「缺失值」拒绝，故不开放 0；拿不准时用 4（中立）。
SCALE_HINT = "7=完全同意 6=同意 5=较同意 4=中立 3=较不同意 2=不同意 1=完全不同意"
SCORE_MIN = 1
SCORE_MAX = 7


def is_fast_mode(mode: str) -> bool:
    return mode == FAST_MODE


def wording_zh(qid, wording=""):
    """题目文案：原站 lang=zh 已返回中文，直接用；为空时回退占位。"""
    return wording or f"（题号 {qid}）"


def archetype_label(name):
    """原型名称：原站 lang=zh 已返回中文，直接展示。"""
    return name or ""
