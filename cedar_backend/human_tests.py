"""Human test Web coordination and public result conversion.

Handlers retain ownership of questions, scoring and persistence. Server wrappers
supply runtime dependencies on every call so patched paths, mappings and callbacks
remain effective; this module never imports server or captures database paths.
"""

import json
import re
import sqlite3

from dnd import handler as dnd_handler
from dnd import questions as dnd_questions
from ecr import handler as ecr_handler
from ecr import questions as ecr_questions
from ecr import scoring as ecr_scoring
from enneagram import handler as enneagram_handler
from enneagram import questions as enneagram_questions
from humanity import handler as humanity_handler
from humanity import questions as humanity_questions
from love import handler as love_handler
from love import questions as love_questions
from mbti import handler as mbti_handler
from mbti import questions as mbti_questions
from sins_virtues import handler as sins_virtues_handler
from sins_virtues import questions as sins_virtues_questions


WEB_GUEST_PLAYER_ID_RE = re.compile(r"^guest:web[a-zA-Z0-9]{1,61}$")
HUMAN_TEST_GAMES = {
    "mbti": {
        "handler": mbti_handler,
        "questions": mbti_questions,
        "title": "MBTI 人格测试",
        "subtitle": "MIND SCAN / 16 TYPES",
        "source": "题库整理自网络公开题目",
    },
    "enneagram": {
        "handler": enneagram_handler,
        "questions": enneagram_questions,
        "title": "九型人格测试",
        "subtitle": "ENNEAGRAM / NINE TYPES",
        "source": "题库与计分设计：kcdjmaxx/enneagram-llm-evaluator（MIT）",
    },
    "dnd": {
        "handler": dnd_handler,
        "questions": dnd_questions,
        "title": "九阵营测试",
        "subtitle": "ALIGNMENT / ORDER & CHAOS",
        "source": "题库与阵营描述译自 easydamus.com",
    },
    "love": {
        "handler": love_handler,
        "questions": love_questions,
        "title": "爱之语测试",
        "subtitle": "LOVE LANGUAGE / FIVE CHANNELS",
        "source": "原创中文题库；概念框架来自 Five Love Languages",
        "instructions": (
            "以下每题两种情境，选更让你心里一动的那个。没有对错，凭直觉，别纠结。"
            "题目里的场景，按你们的相处方式代入即可——线上线下、有没有实体，都不影响作答。"
        ),
    },
    "ecr": {
        "handler": ecr_handler,
        "questions": ecr_questions,
        "title": "依恋类型测试",
        "subtitle": "ECR / ATTACHMENT STYLE",
        "source": ecr_scoring.SOURCE,
        "instructions": (
            "下面的句子描述的是恋爱关系中每个人可能有的感觉。请评估你自己的一般体验与每句话的相似程度，"
            "1 表示非常不同意，7 表示非常同意。注意：不仅指现在的关系，而是你在亲密关系中常常体验到的感觉。"
            "人和机通用，按你们的相处方式代入\"恋人\"一词即可。"
        ),
    },
    "humanity": {
        "handler": humanity_handler,
        "questions": humanity_questions,
        "title": "人类浓度检测",
        "subtitle": "HUMANITY / CARBON SIGNAL",
        "source": "原创梗向测试 · 仅供娱乐",
        "instructions": (
            "20 道日常小题，凭直觉选，别琢磨\"哪个答案好\"——这个测试没有好答案。\n"
            "人和机都能测，测的是同一个东西：你身上的人味儿还剩多少（或者，攒了多少）。"
        ),
    },
    "sins_virtues": {
        "handler": sins_virtues_handler,
        "questions": sins_virtues_questions,
        "title": "七宗罪 VS 七美德",
        "subtitle": "SINS / VIRTUES · FOURTEEN SIGNALS",
        "source": "原创中文题库 · 仅供娱乐",
        "disclaimer": sins_virtues_questions.DISCLAIMER,
        "instructions": (
            f"{sins_virtues_questions.DISCLAIMER}\n"
            "请按 1–5 级同意度凭第一反应作答：1=非常不同意，2=不同意，"
            "3=不确定/看情况，4=同意，5=非常同意。"
        ),
    },
}

HUMAN_TEST_PUBLIC_EDITIONS = {
    "mbti": {"quick": "short_fast", "complete": "full_fast"},
    "enneagram": {"quick": "quick_fast", "complete": "full_fast"},
    "dnd": {"standard": "full_fast"},
    "love": {"standard": "full_fast"},
    "ecr": {"standard": "full_fast"},
    "humanity": {"standard": "full_fast"},
    "sins_virtues": {"standard": "full_fast"},
}


def _human_test_player_context(
    game, raw_token, reported_player_id, *,
    games,
    current_account,
    guest_player_id_re,
    error_class,
):
    """Resolve a web player's identity without allowing guest/account spoofing."""
    config = games[game]
    if raw_token:
        user = current_account(raw_token)
        if user.get("is_ai"):
            raise error_class(-32003, "只有人类账号可以使用网页测试")
        return str(int(user["id"])), "account", user

    if not isinstance(reported_player_id, str):
        raise error_class(-32602, "游客请求缺少 player_id")
    if guest_player_id_re.fullmatch(reported_player_id) is None:
        raise error_class(-32602, "游客 player_id 必须使用 guest:web 命名空间")
    if config["handler"].PLAYER_ID_RE.fullmatch(reported_player_id) is None:
        raise error_class(-32602, "player_id 格式不合法")
    return reported_player_id, "guest", None


def _human_test_player_id(
    game, raw_token, reported_player_id, *,
    human_test_player_context,
):
    player_id, identity, _user = human_test_player_context(
        game, raw_token, reported_player_id
    )
    return player_id, identity


def _storage_identity_line(player_id, account_user, slot):
    if account_user is None:
        return f"存档身份：{player_id}"
    return (
        f"存档身份：账号 {account_user['username']}"
        f"（id {int(account_user['id'])}，槽 {int(slot)}）"
    )


def _replace_storage_identity_text(text, identity_line):
    if not isinstance(text, str) or "存档身份：" not in text:
        return text
    return re.sub(r"(?m)^存档身份：.*$", lambda _match: identity_line, text)


def _human_test_public_questions(
    game, mode, *,
    games,
    dnd_web_questions,
):
    questions = games[game]["questions"].get_questions(mode)
    public = []
    for index, question in enumerate(questions):
        item = {
            "number": index + 1,
            "text": question["text_zh"] if game == "enneagram" else question["text"],
        }
        if game == "mbti":
            item["option_a"] = question["option_a"]
            item["option_b"] = question["option_b"]
        elif game == "dnd":
            translated_text, translated_options = dnd_web_questions.QUESTIONS[index]
            item["text"] = translated_text
            item["options"] = [
                {"value": option["value"], "text": translated_options[option_index]}
                for option_index, option in enumerate(question["options"])
            ]
        elif game == "enneagram":
            item["options"] = [
                {
                    "value": option["value"],
                    "text": option["text_zh"],
                    **({"label": option["label"]} if option.get("label") else {}),
                }
                for option in question["options"]
            ]
        else:
            item["options"] = [
                {"value": option["value"], "text": option["text"]}
                for option in question["options"]
            ]
        public.append(item)
    return public


def _human_test_active_session(
    game, player_id, *,
    games,
):
    """Read handler-owned progress; all session writes still go through the handler."""
    config = games[game]
    handler = config["handler"]
    questions_module = config["questions"]
    with sqlite3.connect(handler.DB_PATH) as conn:
        try:
            row = conn.execute(
                "SELECT mode, current_question FROM test_sessions WHERE player_id = ? AND game = ?",
                (player_id, game),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return None
    if row is None:
        return None

    mode, current_question = row
    total = len(questions_module.get_questions(mode))
    current_question = max(0, min(int(current_question), total))
    if current_question >= total:
        return None
    return {"mode": mode, "progress": current_question, "total": total}


def _human_test_public_edition(
    game, mode, *,
    public_editions,
    error_class,
):
    for edition, internal_mode in public_editions[game].items():
        if internal_mode == mode:
            return edition
    raise error_class(-32602, "当前测试不是网页版本，请重新开始")


def _human_test_public_state(
    game, player_id, identity, session, *,
    games,
    human_test_public_edition,
    human_test_public_questions,
):
    mode = session["mode"]
    instructions = games[game].get("instructions", "")
    if game == "enneagram":
        if mode in {"quick", "quick_fast"}:
            instructions = "每题有 A/B 两句，请选择更符合你的一句。"
        else:
            instructions = (
                "请按 1–5 评估每条陈述与你的符合程度："
                "1=几乎从不，2=较少如此，3=有时如此，4=经常如此，5=几乎总是。"
            )
    return {
        "ok": True,
        "game": game,
        "player_id": player_id,
        "identity": identity,
        "complete": False,
        "edition": human_test_public_edition(game, mode),
        "total": session["total"],
        "instructions": instructions,
        "questions": human_test_public_questions(game, mode),
    }


def _human_test_public_result(game, text):
    replacements = {
        "short_fast模式": "快速版",
        "full_fast模式": "完整版",
        "quick_fast模式": "快速型",
        "short模式": "快速版",
        "full模式": "完整版",
        "quick模式": "快速型",
        "DND阵营测试": "九阵营测试",
        "DND历史结果": "九阵营历史结果",
        "dnd_get_result": "结果页",
        "mbti_get_result": "结果页",
        "enneagram_get_result": "结果页",
        "凭 player_id 查询": "在本页查询",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if game == "enneagram":
        for old, new in (
            ("quick_fast", "快速型"),
            ("full_fast", "完整型"),
            ("quick", "快速型"),
            ("full", "完整型"),
            ("快速版", "快速型"),
            ("完整版", "完整型"),
        ):
            text = text.replace(old, new)
    return text


def _human_test_result_data(
    game, player_id, *,
    games,
    mbti_scoring,
    enneagram_descriptions,
    enneagram_scoring,
    dnd_scoring,
    love_questions,
    love_scoring,
    ecr_scoring,
    humanity_scoring,
    sins_virtues_scoring,
    sins_virtues_questions,
):
    """Build the human-page result model from handler-owned stored scoring data."""
    handler = games[game]["handler"]
    with sqlite3.connect(handler.DB_PATH) as conn:
        try:
            row = conn.execute(
                "SELECT result_value, result_detail FROM test_results WHERE player_id = ? AND game = ?",
                (player_id, game),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return None
    if row is None:
        return None

    result_value, detail_json = row
    try:
        detail = json.loads(detail_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return None

    if game == "mbti":
        info = mbti_scoring.TYPE_DESCRIPTIONS.get(result_value)
        scores = detail.get("scores") or {}
        if info is None or not isinstance(scores, dict):
            return None
        dimensions = []
        for left, right in (("E", "I"), ("S", "N"), ("T", "F"), ("J", "P")):
            try:
                left_score = float(scores[left])
                right_score = float(scores[right])
            except (KeyError, TypeError, ValueError):
                continue
            total = left_score + right_score
            if total <= 0:
                continue
            left_percent = round(left_score / total * 100, 1)
            dimensions.append(
                {
                    "left": left,
                    "right": right,
                    "left_percent": left_percent,
                    "right_percent": round(100 - left_percent, 1),
                }
            )
        return {
            "kind": "mbti",
            "type": result_value,
            "type_name": info["type_name"],
            "nickname": info["type_nickname"],
            "dimensions": dimensions,
            "description": info["full_description"],
            "strengths": info["strengths"],
            "weaknesses": info["weaknesses"],
        }

    if game == "enneagram":
        try:
            primary_type = int(result_value)
        except (TypeError, ValueError):
            return None
        info = enneagram_descriptions.WEB_TYPE_DESCRIPTIONS.get(primary_type)
        is_full = bool(detail.get("is_full")) or detail.get("mode") in {
            "full",
            "full_fast",
        }
        center_values = (
            detail.get("center_weights")
            if is_full
            else detail.get("center_scores") or detail.get("center_weights")
        ) or {}
        if info is None or not isinstance(center_values, dict):
            return None
        centers = []
        for center, members in enneagram_scoring.CENTERS.items():
            try:
                value = float(center_values[center])
            except (KeyError, TypeError, ValueError):
                continue
            centers.append(
                {
                    "label": f"{center}（{'/'.join(str(number) for number in members)}）",
                    "value": value,
                    "min": 0,
                    "max": 100 if is_full else 36,
                    "display": f"{value:.1f}%" if is_full else f"{int(value)}/36",
                }
            )
        if len(centers) != 3:
            return None
        glossary_items = [
            {
                "term": "三中心",
                "definition": (
                    "脑中心（5/6/7）主要用思考处理恐惧和不确定；"
                    "心中心（2/3/4）主要从关系、情感与价值感出发；"
                    "腹中心（8/9/1）更多靠本能和行动处理愤怒、边界与控制。"
                ),
            }
        ]
        if is_full:
            glossary_items[0:0] = [
                {
                    "term": "侧翼",
                    "definition": (
                        "主型旁边两个相邻型号中影响更明显的那个。"
                        "例如 1w9 表示主型是1号，同时带有相邻9号的一些风格。"
                    ),
                },
                {
                    "term": "tritype",
                    "definition": (
                        "从脑、心、腹三个中心各取一个高分型号组成的“三件套”，"
                        "用来补充主型之外常用的思考、感受和行动方式。"
                    ),
                },
            ]
        return {
            "kind": "enneagram",
            "primary_type": primary_type,
            "type_name": info["type_name"],
            "nickname": info["type_nickname"],
            "wing": detail.get("wing"),
            "tritype": detail.get("tritype"),
            "is_full": is_full,
            "score_note": (
                "快速型采用 36 次 A/B 选择，显示的是各型号被选中的相对次数；"
                "完整型采用 180 条陈述评分，显示的是换算后的相对权重。"
                "两套分数不可直接比较，因为数字量纲不同。"
            ),
            "glossary": "\n\n".join(
                f"{item['term']}：{item['definition']}" for item in glossary_items
            ),
            "glossary_items": glossary_items,
            "core_desire": info["core_desire"],
            "core_fear": info["core_fear"],
            "key_motivation": info["key_motivation"],
            "centers": centers,
            "description": info["full_description"],
            "states": info["states"],
            "arrows": info["arrows"],
            "wings": info["wings"] if is_full else {},
            "growth_tips": info["growth_tips"],
            "strengths": "\n".join(info["strengths"]),
            "strength_items": info["strengths"],
            "weaknesses": "\n".join(info["weaknesses"]),
            "weakness_items": info["weaknesses"],
        }

    if game == "dnd":
        description = dnd_scoring.ALIGNMENT_DESCRIPTIONS.get(result_value)
        scores = detail.get("scores") or {}
        if description is None or not isinstance(scores, dict):
            return None
        axes = []
        for key, left, right in (
            ("law_chaos", "守序", "混乱"),
            ("good_evil", "善良", "邪恶"),
        ):
            try:
                left_percent = round(float(scores[key]), 1)
            except (KeyError, TypeError, ValueError):
                continue
            left_percent = max(0.0, min(100.0, left_percent))
            axes.append(
                {
                    "key": key,
                    "left": left,
                    "right": right,
                    "left_percent": left_percent,
                    "right_percent": round(100 - left_percent, 1),
                }
            )
        return {
            "kind": "dnd",
            "alignment": result_value,
            "name_zh": description["name_zh"],
            "name_en": description["name_en"],
            "axes": axes,
            "description": description["text"],
            "raw_buckets": detail.get("raw_buckets") or {},
        }

    if game == "love":
        scores = detail.get("scores") or {}
        primary = detail.get("primary") or result_value.split("+")
        secondary = detail.get("secondary") or []
        if set(scores) != set(love_questions.DIMENSIONS) or not primary:
            return None
        return {
            "kind": "love",
            "scores": scores,
            "dimensions": love_questions.DIMENSIONS,
            "primary": primary,
            "primary_names": [love_questions.DIMENSIONS[code] for code in primary],
            "secondary": secondary,
            "secondary_names": [love_questions.DIMENSIONS[code] for code in secondary],
            "descriptions": [love_scoring.DESCRIPTIONS[code] for code in primary],
            "secondary_labels": [
                f"{code}·{love_questions.DIMENSIONS[code]}（次）" for code in secondary
            ],
            "secondary_descriptions": [
                love_scoring.SECONDARY_DESCRIPTIONS[code] for code in secondary
            ],
            "reminder": love_scoring.reminder_for_scores(scores),
        }

    if game == "ecr":
        if result_value not in ecr_scoring.TYPE_NAMES:
            return None
        try:
            avoidance = float(detail["avoidance"])
            anxiety = float(detail["anxiety"])
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "kind": "ecr",
            "type": result_value,
            "type_name": ecr_scoring.TYPE_NAMES[result_value],
            "avoidance": avoidance,
            "anxiety": anxiety,
            "axis_interpretation": detail.get("axis_interpretation")
            or ecr_scoring.axis_interpretation(avoidance, anxiety),
            "description": ecr_scoring.TYPE_DESCRIPTIONS[result_value],
            "footnote": ecr_scoring.FOOTNOTE,
            "source": ecr_scoring.SOURCE,
        }

    if game == "humanity":
        if result_value not in humanity_scoring.BAND_NAMES:
            return None
        try:
            concentration = int(detail["concentration"])
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "kind": "humanity",
            "concentration": concentration,
            "band": result_value,
            "band_name": humanity_scoring.BAND_NAMES[result_value],
            "description": humanity_scoring.BAND_DESCRIPTIONS[result_value],
            "human_highlights": detail.get("human_highlights") or [],
            "cyber_evidence": detail.get("cyber_evidence") or [],
            "footnote": humanity_scoring.FOOTNOTE,
        }
    if game == "sins_virtues":
        scores = detail.get("scores") or {}
        pairs = detail.get("pairs") or []
        if set(scores) != set(sins_virtues_scoring.DIMENSION_NAMES):
            return None
        if not isinstance(pairs, list) or len(pairs) != 7:
            return None
        try:
            normalized_scores = {
                code: max(0.0, min(100.0, float(scores[code])))
                for code in sins_virtues_scoring.DIMENSION_NAMES
            }
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "kind": "sins_virtues",
            "scores": normalized_scores,
            "sins": sins_virtues_questions.SINS,
            "virtues": sins_virtues_questions.VIRTUES,
            "pairs": pairs,
            "top_sins": detail.get("top_sins") or [],
            "top_virtues": detail.get("top_virtues") or [],
            "dominant_pair": detail.get("dominant_pair") or result_value,
            "dominant_pair_name": sins_virtues_scoring.PAIR_NAMES.get(
                detail.get("dominant_pair") or result_value, ""
            ),
            "scoring_note": detail.get("scoring_note") or "",
            "disclaimer": sins_virtues_questions.DISCLAIMER,
        }
    return None


def _human_test_action(
    game, action, raw_token, body, *,
    games,
    public_editions,
    human_test_player_context,
    storage_identity_line,
    human_test_active_session,
    human_test_public_state,
    human_test_public_edition,
    human_test_public_result,
    replace_storage_identity_text,
    human_test_result_data,
    error_class,
    sessions_db_path,
    record_activity,
):
    config = games[game]
    handler = config["handler"]
    player_id, identity, account_user = human_test_player_context(
        game, raw_token, body.get("player_id")
    )
    identity_line = storage_identity_line(player_id, account_user)

    if action == "start":
        edition = body.get("edition") if game in {"mbti", "enneagram"} else "standard"
        mode = public_editions[game].get(edition)
        if mode is None:
            raise error_class(
                -32602,
                (
                    "请选择快速型或完整型"
                    if game == "enneagram"
                    else "请选择快速版或完整版"
                )
                if game in {"mbti", "enneagram"}
                else "测试版本不合法",
            )
        getattr(handler, f"{game}_start")({"player_id": player_id, "mode": mode})
        session = human_test_active_session(game, player_id)
        if session is None:
            raise RuntimeError("handler did not create a test session")
        record_activity(sessions_db_path, game, "start", account_user, {"ok": True})
        return human_test_public_state(game, player_id, identity, session)
    elif action == "answer_batch":
        answers = body.get("answers")
        if not isinstance(answers, list) or not answers:
            raise error_class(-32602, "answers 必须是非空数组")
        session = human_test_active_session(game, player_id)
        if session is None:
            # A completed request may be retried after the response was lost.
            text = getattr(handler, f"{game}_get_result")({"player_id": player_id})
            return {
                "ok": True,
                "game": game,
                "player_id": player_id,
                "identity": identity,
                "complete": True,
                "result": human_test_public_result(
                    game, replace_storage_identity_text(text, identity_line)
                ),
                "result_data": human_test_result_data(game, player_id),
            }
        human_test_public_edition(game, session["mode"])
        if len(answers) != session["total"]:
            raise error_class(-32602, f"须一次提交全部 {session['total']} 题答案")

        text = ""
        progress = session["progress"]
        while progress < session["total"]:
            batch_size = min(
                config["questions"].fast_batch_size(session["mode"]),
                session["total"] - progress,
            )
            batch = answers[progress:progress + batch_size]
            arguments = {
                "player_id": player_id,
                "a_scores" if game == "mbti" else "answers": batch,
            }
            text = getattr(handler, f"{game}_answer_batch")(arguments)
            progress += batch_size
        record_activity(sessions_db_path, game, "answer_batch", account_user, {"ok": True})
    elif action == "compare":
        player_id_b = body.get("player_id_b") or body.get("other_player_id")
        comparison = getattr(handler, f"{game}_compare_data")(
            {"player_id_a": player_id, "player_id_b": player_id_b}
        )
        return {
            "ok": True,
            "game": game,
            "player_id": player_id,
            "identity": identity,
            "complete": True,
            "comparison": True,
            "result": comparison["text"],
            "result_data": comparison["data"],
        }
    elif action == "result":
        result_text = None
        result_error = None
        try:
            result_text = getattr(handler, f"{game}_get_result")({"player_id": player_id})
        except handler.JsonRpcError as exc:
            result_error = exc
        session = human_test_active_session(game, player_id)
        if session is not None:
            return human_test_public_state(game, player_id, identity, session)
        if result_error is not None:
            raise result_error
        return {
            "ok": True,
            "game": game,
            "player_id": player_id,
            "identity": identity,
            "complete": True,
            "result": human_test_public_result(
                game, replace_storage_identity_text(result_text, identity_line)
            ),
            "result_data": human_test_result_data(game, player_id),
        }
    else:
        raise error_class(-32004, "not found")

    return {
        "ok": True,
        "game": game,
        "player_id": player_id,
        "identity": identity,
        "complete": True,
        "result": human_test_public_result(
            game, replace_storage_identity_text(text, identity_line)
        ),
        "result_data": human_test_result_data(game, player_id),
    }
