from .descriptions import ALIGNMENT_DESCRIPTIONS


BUCKETS = ("lx", "nx", "cx", "xg", "xn", "xe")

ALIGNMENT_NAMES = {
    "lawful_good": "Lawful Good",
    "neutral_good": "Neutral Good",
    "chaotic_good": "Chaotic Good",
    "lawful_neutral": "Lawful Neutral",
    "true_neutral": "True Neutral",
    "chaotic_neutral": "Chaotic Neutral",
    "lawful_evil": "Lawful Evil",
    "neutral_evil": "Neutral Evil",
    "chaotic_evil": "Chaotic Evil",
}


def score_answers(questions, answers):
    raw = {bucket: 0 for bucket in BUCKETS}
    for question, answer in zip(questions, answers):
        option = _option_for_answer(question, answer)
        raw[option["bucket"]] += option["points"]

    law_score = _axis_score(raw["lx"], raw["nx"], raw["cx"])
    good_score = _axis_score(raw["xg"], raw["xn"], raw["xe"])
    law_band = _law_band(law_score)
    good_band = _good_band(good_score)
    key = _js_alignment_key(raw)

    return {
        "alignment": key,
        "name_en": ALIGNMENT_NAMES[key],
        "raw_buckets": raw,
        "scores": {
            "law_chaos": law_score,
            "good_evil": good_score,
        },
        "bands": {
            "law_chaos": law_band,
            "good_evil": good_band,
        },
        "bucket_winners": {
            "law_chaos": _max_bucket(raw, ("lx", "nx", "cx")),
            "good_evil": _max_bucket(raw, ("xg", "xn", "xe")),
        },
    }


def format_result(mode, questions, answers):
    result = score_answers(questions, answers)
    desc = ALIGNMENT_DESCRIPTIONS[result["alignment"]]
    return "\n".join(
        [
            f"【DND阵营测试完成 · {mode}模式】",
            "",
            f"你的阵营：{desc['name_en']} · {desc['name_zh']}",
            "",
            "━━━ 轴向分数 ━━━",
            f"守序 ←→ 混乱：{result['scores']['law_chaos']:.1f}/100（{result['bands']['law_chaos']}）",
            f"善良 ←→ 邪恶：{result['scores']['good_evil']:.1f}/100（{result['bands']['good_evil']}）",
            "",
            "━━━ 原始桶分 ━━━",
            _raw_line(result["raw_buckets"]),
            "",
            "━━━ 阵营描述 ━━━",
            desc["text"],
            "",
            "（结果已存档 48 小时，可用 dnd_get_result 凭 player_id 查询。）",
        ]
    )


def format_stored_result(mode, result_value, detail, completed_at_label):
    desc = ALIGNMENT_DESCRIPTIONS[result_value]
    scores = detail.get("scores") or {}
    bands = detail.get("bands") or {}
    lines = [
        f"【DND历史结果 · {mode}模式 · {completed_at_label}】",
        "",
        f"你的阵营：{desc['name_en']} · {desc['name_zh']}",
    ]
    if scores:
        lines.extend(
            [
                "",
                "━━━ 轴向分数 ━━━",
                f"守序 ←→ 混乱：{scores.get('law_chaos', 0):.1f}/100（{bands.get('law_chaos', 'N/A')}）",
                f"善良 ←→ 邪恶：{scores.get('good_evil', 0):.1f}/100（{bands.get('good_evil', 'N/A')}）",
            ]
        )
    lines.extend(["", "━━━ 阵营描述 ━━━", desc["text"]])
    return "\n".join(lines)


def _option_for_answer(question, answer):
    for option in question["options"]:
        if option["value"] == answer:
            return option
    raise ValueError(f"invalid answer {answer!r} for question {question['id']}")


def _axis_score(positive, neutral, negative):
    total = positive + neutral + negative
    if total <= 0:
        return 50.0
    return (positive + neutral * 0.5) / total * 100


def _js_alignment_key(raw):
    combined = {
        "lawful_good": raw["lx"] + raw["xg"],
        "neutral_good": raw["nx"] + raw["xg"],
        "chaotic_good": raw["cx"] + raw["xg"],
        "lawful_neutral": raw["lx"] + raw["xn"],
        "true_neutral": raw["nx"] + raw["xn"],
        "chaotic_neutral": raw["cx"] + raw["xn"],
        "lawful_evil": raw["lx"] + raw["xe"],
        "neutral_evil": raw["nx"] + raw["xe"],
        "chaotic_evil": raw["cx"] + raw["xe"],
    }
    best_key = "true_neutral"
    best_score = combined[best_key]
    for key in (
        "lawful_good",
        "neutral_good",
        "chaotic_good",
        "lawful_neutral",
        "chaotic_neutral",
        "lawful_evil",
        "neutral_evil",
        "chaotic_evil",
    ):
        if combined[key] > best_score:
            best_key = key
            best_score = combined[key]

    if sum(1 for score in combined.values() if score == best_score) == 1:
        return best_key

    if raw["lx"] > raw["nx"] and raw["lx"] > raw["cx"]:
        law = "lawful"
    elif raw["cx"] > raw["nx"] and raw["cx"] > raw["lx"]:
        law = "chaotic"
    else:
        law = "neutral"

    if raw["xg"] > raw["xe"] and raw["xg"] > raw["xn"]:
        moral = "good"
    elif raw["xe"] > raw["xn"] and raw["xe"] > raw["xg"]:
        moral = "evil"
    else:
        moral = "neutral"

    if law == "neutral" and moral == "neutral":
        return "true_neutral"
    return f"{law}_{moral}"


def _law_band(score):
    return _band(score, "L+", "L", "L(N)", "-C", "N(L)", "N", "N(C)", "-L", "C(N)", "C", "C+")


def _good_band(score):
    return _band(score, "G+", "G", "G(N)", "-E", "N(G)", "N", "N(E)", "-G", "E(N)", "E", "E+")


def _band(score, hi_plus, hi, hi_neutral, low_transition, neutral_hi, neutral, neutral_low, high_transition, low_neutral, low, low_plus):
    if score >= 91:
        return hi_plus
    if score >= 81:
        return hi
    if score >= 71:
        return hi_neutral
    if score >= 66:
        return low_transition
    if score >= 56:
        return neutral_hi
    if score >= 45:
        return neutral
    if score >= 35:
        return neutral_low
    if score >= 30:
        return high_transition
    if score >= 20:
        return low_neutral
    if score >= 10:
        return low
    return low_plus


def _max_bucket(raw, buckets):
    return max(buckets, key=lambda bucket: raw[bucket])


def _raw_line(raw):
    return (
        f"Law {raw['lx']} / Neutral-LC {raw['nx']} / Chaos {raw['cx']}；"
        f"Good {raw['xg']} / Neutral-GE {raw['xn']} / Evil {raw['xe']}"
    )
