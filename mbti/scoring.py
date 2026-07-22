from .descriptions import TYPE_DESCRIPTIONS


DIMENSION_LABELS = {
    "EI": ("E", "I", "外倾(E)", "内倾(I)"),
    "SN": ("S", "N", "感觉(S)", "直觉(N)"),
    "TF": ("T", "F", "思考(T)", "情感(F)"),
    "JP": ("J", "P", "判断(J)", "知觉(P)"),
}


def _blank_scores():
    return {letter: 0 for letter in "EISNTFJP"}


def score_answers(questions, answers):
    scores = _blank_scores()
    counts = {dimension: 0 for dimension in DIMENSION_LABELS}

    for question, a_score in zip(questions, answers):
        left = question["left"]
        right = question["right"]
        scores[left] += a_score
        scores[right] += 5 - a_score
        counts[question["dimension"]] += 1

    ei_score = _bias(scores["I"], scores["E"], counts["EI"])
    sn_score = _bias(scores["S"], scores["N"], counts["SN"])
    tf_score = _bias(scores["T"], scores["F"], counts["TF"])
    jp_score = _bias(scores["J"], scores["P"], counts["JP"])

    mbti_type = "".join(
        [
            "I" if ei_score >= 0 else "E",
            "S" if sn_score >= 0 else "N",
            "T" if tf_score >= 0 else "F",
            "J" if jp_score >= 0 else "P",
        ]
    )

    return {
        "type": mbti_type,
        "scores": scores,
        "counts": counts,
        "bias": {
            "EI": ei_score,
            "SN": sn_score,
            "TF": tf_score,
            "JP": jp_score,
        },
    }


def _format_type_sections(mbti_type: str) -> list[str]:
    info = TYPE_DESCRIPTIONS[mbti_type]
    return [
        f"你的MBTI类型：{mbti_type} · {info['type_name']}（{info['type_nickname']}）",
        "",
        "━━━ 类型描述 ━━━",
        info["full_description"],
        "",
        "━━━ 性格优势 ━━━",
        info["strengths"],
        "",
        "━━━ 注意事项 ━━━",
        info["weaknesses"],
    ]


def format_result(mode, questions, answers):
    result = score_answers(questions, answers)
    mbti_type = result["type"]
    info = TYPE_DESCRIPTIONS[mbti_type]
    return "\n".join(
        [
            f"【测试完成 · {mode}模式】",
            "",
            f"你的MBTI类型：{mbti_type} · {info['type_name']}（{info['type_nickname']}）",
            "",
            "━━━ 维度分析 ━━━",
            _dimension_line("EI", result),
            _dimension_line("SN", result),
            _dimension_line("TF", result),
            _dimension_line("JP", result),
            "",
            "━━━ 类型描述 ━━━",
            info["full_description"],
            "",
            "━━━ 性格优势 ━━━",
            info["strengths"],
            "",
            "━━━ 注意事项 ━━━",
            info["weaknesses"],
            "",
            "（账号结果永久保留；游客结果存档 48 小时，可用 mbti_get_result 凭 player_id 查询；查询版不含维度百分比。）",
        ]
    )


def format_stored_result(mode: str, mbti_type: str, completed_at_label: str) -> str:
    """按已存四字母类型从固定文案拼装，不含维度分析。"""
    if mbti_type not in TYPE_DESCRIPTIONS:
        raise ValueError(f"unknown mbti type: {mbti_type}")
    return "\n".join(
        [
            f"【历史结果 · {mode}模式 · {completed_at_label}】",
            "",
            *_format_type_sections(mbti_type),
            "",
            "（账号档案永久保留，游客档案保留 48 小时；维度百分比仅在当次测完时返回，重测可再次获得。）",
        ]
    )


def _bias(positive_total, negative_total, count):
    if count <= 0:
        return 0.0
    # short模式分母：EI/SN/TF/JP 都是 4*5=20；full模式按各维度实际题数计算。
    return (positive_total - negative_total) / (count * 5) * 100


def _dimension_line(dimension, result):
    left, right, left_label, right_label = DIMENSION_LABELS[dimension]
    scores = result["scores"]
    total = scores[left] + scores[right]
    if total <= 0:
        left_pct = right_pct = 0
    else:
        left_pct = round(scores[left] / total * 100)
        right_pct = 100 - left_pct

    bias = result["bias"][dimension]
    dominant = _dominant_label(dimension, bias)
    return (
        f"{left_label} {left_pct}% ·|· {right_pct}% {right_label}   "
        f"→ 偏向{dominant}，偏向程度{abs(bias):.1f}"
    )


def _dominant_label(dimension, bias):
    if bias == 0:
        return "平衡"
    if dimension == "EI":
        return "内倾(I)" if bias > 0 else "外倾(E)"
    if dimension == "SN":
        return "感觉(S)" if bias > 0 else "直觉(N)"
    if dimension == "TF":
        return "思考(T)" if bias > 0 else "情感(F)"
    if dimension == "JP":
        return "判断(J)" if bias > 0 else "知觉(P)"
    return "平衡"
