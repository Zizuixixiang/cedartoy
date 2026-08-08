"""Independent 0-100 scoring for the 14 entertainment dimensions."""

from .questions import DISCLAIMER, PAIRS, SINS, VIRTUES


DIMENSION_NAMES = {**SINS, **VIRTUES}
PAIR_NAMES = {
    pair: f"{SINS[sin]} / {VIRTUES[virtue]}" for pair, sin, virtue in PAIRS
}


def _keyed_value(answer, loading):
    return answer if loading > 0 else 6 - answer


def score_answers(questions, answers):
    if len(questions) != 35 or len(answers) != 35:
        raise ValueError("sins_virtues requires exactly 35 answers")
    values = {dimension: [] for dimension in DIMENSION_NAMES}
    for question, answer in zip(questions, answers):
        if not isinstance(answer, int) or isinstance(answer, bool) or not 1 <= answer <= 5:
            raise ValueError(f"invalid answer {answer!r} for question {question['id']}")
        for dimension, loading in question["loadings"].items():
            values[dimension].append(_keyed_value(answer, loading))

    if any(len(items) != 3 for items in values.values()):
        raise ValueError("every dimension must have exactly three scored indicators")
    scores = {
        dimension: round((sum(items) / len(items) - 1) / 4 * 100, 1)
        for dimension, items in values.items()
    }
    pairs = [
        {
            "key": pair,
            "sin": sin,
            "sin_name": SINS[sin],
            "sin_score": scores[sin],
            "virtue": virtue,
            "virtue_name": VIRTUES[virtue],
            "virtue_score": scores[virtue],
        }
        for pair, sin, virtue in PAIRS
    ]
    top_sins = sorted(SINS, key=lambda key: (-scores[key], tuple(SINS).index(key)))[:2]
    top_virtues = sorted(VIRTUES, key=lambda key: (-scores[key], tuple(VIRTUES).index(key)))[:2]
    dominant_pair = max(
        pairs,
        key=lambda item: (
            (item["sin_score"] + item["virtue_score"]) / 2,
            -tuple(pair for pair, _sin, _virtue in PAIRS).index(item["key"]),
        ),
    )["key"]
    return {
        "result_value": dominant_pair,
        "scores": scores,
        "pairs": pairs,
        "top_sins": top_sins,
        "top_virtues": top_virtues,
        "dominant_pair": dominant_pair,
        "scoring_note": (
            "每个维度由三项指标（正向、反向与共存陈述）独立取均值，再线性映射到 0–100。"
            "两侧不做互补归一，所以同一对可以同时高、同时低，或一高一低。"
        ),
        "disclaimer": DISCLAIMER,
    }


def _dimension_list(codes, scores):
    return "、".join(f"{DIMENSION_NAMES[code]} {scores[code]:g}" for code in codes)


def format_result(mode, result):
    return "\n".join(
        [
            f"【七宗罪 VS 七美德完成 · {mode}模式】",
            DISCLAIMER,
            "",
            "━━━ 七宗罪侧（0–100）━━━",
            *[f"{SINS[code]}：{result['scores'][code]:g}" for code in SINS],
            "",
            "━━━ 七美德侧（0–100）━━━",
            *[f"{VIRTUES[code]}：{result['scores'][code]:g}" for code in VIRTUES],
            "",
            f"分数最高的七宗罪：{_dimension_list(result['top_sins'], result['scores'])}",
            f"分数最高的七美德：{_dimension_list(result['top_virtues'], result['scores'])}",
            "",
            result["scoring_note"],
            "分数只描述这次作答呈现出的趣味倾向；高低都不是好坏、品格或现实行为判定。",
            "（账号结果永久保留；游客结果存档 48 小时，可用 sins_virtues_get_result 凭 player_id 查询。）",
        ]
    )


def format_stored_result(mode, result_value, detail, completed_at_label):
    result = {"result_value": result_value, **detail}
    text = format_result(mode, result).replace(
        "七宗罪 VS 七美德完成", "七宗罪 VS 七美德历史结果", 1
    )
    return text.replace(
        "（账号结果永久保留；游客结果存档 48 小时，可用 sins_virtues_get_result 凭 player_id 查询。）",
        f"完成时间：{completed_at_label}",
    )


assert set(DIMENSION_NAMES) == {*SINS, *VIRTUES}
assert set(PAIR_NAMES) == {pair for pair, _sin, _virtue in PAIRS}
