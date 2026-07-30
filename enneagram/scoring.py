from .descriptions import TYPE_DESCRIPTIONS


CENTERS = {
    "腹中心": (8, 9, 1),
    "心中心": (2, 3, 4),
    "脑中心": (5, 6, 7),
}

SCALE_NOTE = (
    "quick 与 full 使用不同题型和分数量纲：quick 是 36 次 A/B 选择的相对计数，"
    "full 是 180 条李克特评分；两套分数不可直接比较。"
)


def score_answers(questions, answers):
    is_quick = bool(questions) and questions[0].get("kind") == "quick"
    totals = {type_number: 0 for type_number in range(1, 10)}
    counts = {type_number: 0 for type_number in range(1, 10)}

    if is_quick:
        for question, answer in zip(questions, answers):
            option = next(
                option for option in question["options"] if option["value"] == int(answer)
            )
            totals[int(option["enneagram_type"])] += 1
        ranking_scores = totals
    else:
        for question, answer in zip(questions, answers):
            type_number = int(question["enneagram_type"])
            totals[type_number] += int(answer)
            counts[type_number] += 1
        ranking_scores = {
            type_number: (
                totals[type_number] / counts[type_number] if counts[type_number] else 0.0
            )
            for type_number in totals
        }

    primary_type = max(
        range(1, 10), key=lambda number: (ranking_scores[number], -number)
    )
    is_full = not is_quick
    center_scores = {
        center: sum(totals[type_number] for type_number in members)
        for center, members in CENTERS.items()
    }

    result = {
        "result_value": str(primary_type),
        "primary_type": primary_type,
        "type_scores": totals,
        "center_scores": center_scores,
        "score_scale": "quick_36" if is_quick else "full_likert",
        "is_full": is_full,
        "wing": _wing(primary_type, ranking_scores) if is_full else None,
        "tritype": _tritype(primary_type, ranking_scores) if is_full else None,
    }
    if is_full:
        result["type_averages"] = {
            str(key): round(value, 3) for key, value in ranking_scores.items()
        }
        result["center_weights"] = _center_weights(ranking_scores)
    else:
        # All three centers together account for exactly 36 choices.
        result["center_weights"] = center_scores
    return result


def format_result(mode, result):
    primary_type = int(result["primary_type"])
    info = TYPE_DESCRIPTIONS[primary_type]
    is_full = bool(result.get("is_full"))
    lines = [
        f"【九型人格测试完成 · {mode}模式】",
        "",
        f"你的主型：{primary_type}号 · {info['type_name']}（{info['type_nickname']}）",
    ]
    if is_full:
        lines.extend(
            [
                f"侧翼：{result['wing']}",
                f"Tritype 推测：{result['tritype']}",
                "",
                "━━━ 三中心权重（full 李克特相对权重）━━━",
                *_center_lines(result["center_weights"], is_full=True),
            ]
        )
    else:
        lines.extend(
            [
                "",
                "━━━ 三中心相对分（36分制）━━━",
                *_center_lines(result["center_scores"], is_full=False),
                "",
                "quick 仅报告主型与脑/心/腹三中心相对分；侧翼与 tritype 仅 full 档提供。",
            ]
        )
    lines.extend(
        [
            "",
            f"分数量纲说明：{SCALE_NOTE}",
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
            "（账号结果永久保留；游客结果保留 48 小时，可用 enneagram_get_result 查询。）",
        ]
    )
    return "\n".join(lines)


def format_stored_result(mode, result_value, detail, completed_at_label):
    primary_type = int(result_value)
    info = TYPE_DESCRIPTIONS[primary_type]
    is_full = bool(detail.get("is_full")) or mode in {"full", "full_fast"}
    lines = [
        f"【九型人格历史结果 · {mode}模式 · {completed_at_label}】",
        "",
        f"你的主型：{primary_type}号 · {info['type_name']}（{info['type_nickname']}）",
    ]
    if is_full:
        lines.extend(
            [
                f"侧翼：{detail.get('wing', '暂无')}",
                f"Tritype 推测：{detail.get('tritype', '暂无')}",
            ]
        )
        weights = detail.get("center_weights") or {}
        if weights:
            lines.extend(
                [
                    "",
                    "━━━ 三中心权重（full 李克特相对权重）━━━",
                    *_center_lines(weights, is_full=True),
                ]
            )
    else:
        center_scores = detail.get("center_scores") or detail.get("center_weights") or {}
        if center_scores:
            lines.extend(
                [
                    "",
                    "━━━ 三中心相对分（36分制）━━━",
                    *_center_lines(center_scores, is_full=False),
                ]
            )
        lines.extend(
            [
                "",
                "quick 仅报告主型与脑/心/腹三中心相对分；侧翼与 tritype 仅 full 档提供。",
            ]
        )
    lines.extend(
        [
            "",
            f"分数量纲说明：{SCALE_NOTE}",
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
    )
    return "\n".join(lines)


def _wing(primary_type, scores):
    left = 9 if primary_type == 1 else primary_type - 1
    right = 1 if primary_type == 9 else primary_type + 1
    wing_type = max((left, right), key=lambda number: (scores[number], -number))
    return f"{primary_type}w{wing_type}"


def _center_weights(scores):
    raw = {
        center: sum(scores[type_number] for type_number in members)
        for center, members in CENTERS.items()
    }
    total = sum(raw.values())
    if total <= 0:
        return {center: 0.0 for center in CENTERS}
    rounded = {center: round(value / total * 100, 1) for center, value in raw.items()}
    drift = round(100.0 - sum(rounded.values()), 1)
    if drift:
        strongest = max(raw, key=raw.get)
        rounded[strongest] = round(rounded[strongest] + drift, 1)
    return rounded


def _tritype(primary_type, scores):
    selected = [
        max(members, key=lambda number: (scores[number], -number))
        for members in CENTERS.values()
    ]
    selected.sort(
        key=lambda number: (
            number == primary_type,
            scores[number],
            -number,
        ),
        reverse=True,
    )
    return "-".join(str(number) for number in selected)


def _center_lines(values, *, is_full):
    suffix = "%" if is_full else "/36"
    decimals = 1 if is_full else 0
    return [
        (
            f"{center}（{'/'.join(str(number) for number in CENTERS[center])}）："
            f"{float(values.get(center, 0)):.{decimals}f}{suffix}"
        )
        for center in CENTERS
    ]
