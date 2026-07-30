from .descriptions import TYPE_DESCRIPTIONS


CENTERS = {
    "腹中心": (8, 9, 1),
    "心中心": (2, 3, 4),
    "脑中心": (5, 6, 7),
}

SCALE_NOTE = "量纲不同，两套分数不可直接比较。"


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
    """Return the compact machine-facing result immediately after completion."""
    primary_type = int(result["primary_type"])
    is_full_test = bool(result.get("is_full"))
    lines = [
        f"【九型人格测试完成 · {mode}模式】",
        "",
        *_score_lines(result, is_full_test=is_full_test),
        "",
        *_compact_profile_lines(primary_type),
        "",
        _glossary_card(is_full_test),
        "",
        "想继续自查：调用 enneagram_get_result，传 detail=full 获取完整版。",
        "（账号结果永久保留；游客结果保留 48 小时。）",
    ]
    return "\n".join(lines)


def format_stored_result(
    mode,
    result_value,
    detail,
    completed_at_label,
    *,
    requested_detail=None,
):
    primary_type = int(result_value)
    is_full_test = bool(detail.get("is_full")) or mode in {"full", "full_fast"}
    lines = [
        f"【九型人格历史结果 · {mode}模式 · {completed_at_label}】",
        "",
        *_stored_score_lines(detail, primary_type, is_full_test=is_full_test),
        "",
    ]
    if requested_detail == "full":
        lines.extend(_full_profile_lines(primary_type, include_wings=is_full_test))
    else:
        lines.extend(_compact_profile_lines(primary_type))
    lines.extend(["", _glossary_card(is_full_test)])
    if requested_detail != "full":
        lines.extend(
            [
                "",
                "想继续自查：再次调用 enneagram_get_result，传 detail=full 获取完整版。",
            ]
        )
    return "\n".join(lines)


def _score_lines(result, *, is_full_test):
    primary_type = int(result["primary_type"])
    info = TYPE_DESCRIPTIONS[primary_type]
    lines = [
        f"你的主型：{primary_type}号 · {info['type_name']}（{info['type_nickname']}）"
    ]
    if is_full_test:
        lines.extend(
            [
                f"侧翼：{result.get('wing', '暂无')}",
                f"Tritype 推测：{result.get('tritype', '暂无')}",
                "",
                "━━━ 三中心权重（full 李克特相对权重）━━━",
                *_center_lines(result.get("center_weights") or {}, is_full=True),
            ]
        )
    else:
        lines.extend(
            [
                "",
                "━━━ 三中心相对分（36分制）━━━",
                *_center_lines(result.get("center_scores") or {}, is_full=False),
            ]
        )
    lines.extend(["", f"分数量纲说明：{SCALE_NOTE}"])
    return lines


def _stored_score_lines(detail, primary_type, *, is_full_test):
    result = {"primary_type": primary_type, **detail}
    return _score_lines(result, is_full_test=is_full_test)


def _compact_profile_lines(primary_type):
    info = TYPE_DESCRIPTIONS[primary_type]
    states = info["states"]
    arrows = info["arrows"]
    return [
        "━━━ 主型速写 ━━━",
        _first_sentence(info["full_description"].split("\n\n", 1)[0]),
        "",
        "━━━ 核心恐惧与欲望 ━━━",
        f"核心恐惧：{_first_sentence(info['core_fear'])}",
        f"核心欲望：{_first_sentence(info['core_desire'])}",
        "",
        "━━━ 三种状态 ━━━",
        f"健康状态：{_first_sentence(states['healthy'])}",
        f"一般状态：{_first_sentence(states['average'])}",
        f"不健康状态：{_first_sentence(states['unhealthy'])}",
        "",
        "━━━ 成长与压力箭头 ━━━",
        _first_sentence(arrows["growth"]),
        _first_sentence(arrows["stress"]),
        "",
        "━━━ 三条行动建议 ━━━",
        *[
            f"{index}. {tip}"
            for index, tip in enumerate(info["growth_tips"][:3], start=1)
        ],
    ]


def _full_profile_lines(primary_type, *, include_wings):
    info = TYPE_DESCRIPTIONS[primary_type]
    states = info["states"]
    arrows = info["arrows"]
    lines = [
        "━━━ 主型深度描述 ━━━",
        info["full_description"],
        "",
        "━━━ 核心恐惧 / 核心欲望 / 关键动机 ━━━",
        f"核心恐惧：{info['core_fear']}",
        "",
        f"核心欲望：{info['core_desire']}",
        "",
        f"关键动机：{info['key_motivation']}",
        "",
        "━━━ 健康 / 一般 / 不健康状态 ━━━",
        f"健康状态：{states['healthy']}",
        "",
        f"一般状态：{states['average']}",
        "",
        f"不健康状态：{states['unhealthy']}",
        "",
        "━━━ 成长与压力箭头 ━━━",
        arrows["growth"],
        "",
        arrows["stress"],
    ]
    if include_wings:
        lines.extend(["", "━━━ 两个侧翼的差异 ━━━"])
        for wing_name, wing_text in info["wings"].items():
            lines.extend([f"{wing_name}：{wing_text}", ""])
        lines.pop()
    lines.extend(
        [
            "",
            "━━━ 成长建议 ━━━",
            *[
                f"{index}. {tip}"
                for index, tip in enumerate(info["growth_tips"], start=1)
            ],
            "",
            "━━━ 优势 ━━━",
            *[f"• {item}" for item in info["strengths"]],
            "",
            "━━━ 盲点 ━━━",
            *[f"• {item}" for item in info["weaknesses"]],
        ]
    )
    return lines


def _first_sentence(text):
    head, marker, _ = text.partition("。")
    return head + marker if marker else text


def _glossary_card(is_full_test):
    entries = []
    if is_full_test:
        entries.extend(
            [
                "侧翼：主型相邻两型中较明显的一侧。",
                "tritype：从脑、心、腹三个中心各取一个高分型。",
            ]
        )
    entries.append(
        "三中心：脑（5/6/7）、心（2/3/4）、腹（8/9/1）三种反应重心。"
    )
    body = "\n".join(f"│ {entry}" for entry in entries)
    return f"┌─ 名词小课堂 ─┐\n{body}\n└──────────┘"


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
