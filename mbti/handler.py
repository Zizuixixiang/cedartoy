import json
import re
import sqlite3
import time
from datetime import datetime, timezone

from .questions import (
    FAST_BATCH_SIZE_MAX,
    VALID_MODES,
    fast_batch_size,
    get_questions,
    is_fast_mode,
)
from .scoring import format_result, format_stored_result, score_answers

_SCORE_HINT = (
    "5=完全偏向A，4=比较偏向A，3=略偏向A，2=略偏向B，1=比较偏向B，0=完全偏向B（A+B=5）"
)


DB_PATH = "/opt/cedartoy/data/sessions.db"
GAME = "mbti"
MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 24 * 60 * 60
RESULT_TTL_SECONDS = 48 * 60 * 60
PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")


TOOLS = [
    {
        "name": "mbti_start",
        "description": "开始或重置一次 MBTI 测试。（游戏总入口：toy.cedarstar.org/，支持 MCP 和 curl 连接，包含更多游戏）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "1-10位字母数字 ID。",
                    "pattern": "^[a-zA-Z0-9]{1,10}$",
                },
                "mode": {
                    "type": "string",
                    "enum": list(VALID_MODES),
                    "description": (
                        "short 16题逐题；full 93题逐题；"
                        "short_fast 16题一次性提交；full_fast 93题每批最多16题。"
                    ),
                },
            },
            "required": ["player_id", "mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mbti_answer",
        "description": "提交当前题的 A 选项得分。5=完全偏向A，4=比较偏向A，3=略偏向A，2=略偏向B，1=比较偏向B，0=完全偏向B；A+B=5，因此只需提交A的0-5整数分。（游戏总入口：toy.cedarstar.org/，支持 MCP 和 curl 连接，包含更多游戏）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "1-10位字母数字 ID。",
                    "pattern": "^[a-zA-Z0-9]{1,10}$",
                },
                "a_score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5,
                    "description": "A选项得几分：5=完全偏向A，4=比较偏向A，3=略偏向A，2=略偏向B，1=比较偏向B，0=完全偏向B。",
                },
            },
            "required": ["player_id", "a_score"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mbti_answer_batch",
        "description": (
            "快速模式专用：一次提交本批全部答案（short_fast 须一次提交 16 题；"
            "full_fast 每批最多 16 题，最后一批可能不足 16）。a_scores 长度须等于本批题数。"
            "（游戏总入口：toy.cedarstar.org/，支持 MCP 和 curl 连接，包含更多游戏）"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "1-10位字母数字 ID。",
                    "pattern": "^[a-zA-Z0-9]{1,10}$",
                },
                "a_scores": {
                    "type": "array",
                    "items": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 5,
                    },
                    "minItems": 1,
                    "maxItems": FAST_BATCH_SIZE_MAX,
                    "description": (
                        "本批每题的 A 选项得分（0-5），顺序对应当前展示的题目。"
                        "长度须等于提示中的本批题数。"
                    ),
                },
            },
            "required": ["player_id", "a_scores"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mbti_get_result",
        "description": "查询该 player_id 最近一次已完成测试的结果（仅四字母类型存档，文案现场拼装；完成超过48小时自动删除）。（游戏总入口：toy.cedarstar.org/，支持 MCP 和 curl 连接，包含更多游戏）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "1-10位字母数字 ID。",
                    "pattern": "^[a-zA-Z0-9]{1,10}$",
                },
            },
            "required": ["player_id"],
            "additionalProperties": False,
        },
    },
]


def handle_mcp(payload):
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cedartoy-mbti", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            try:
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name == "mbti_start":
                    text = mbti_start(arguments)
                elif name == "mbti_answer":
                    text = mbti_answer(arguments)
                elif name == "mbti_answer_batch":
                    text = mbti_answer_batch(arguments)
                elif name == "mbti_get_result":
                    text = mbti_get_result(arguments)
                else:
                    raise JsonRpcError(-32601, f"未知工具：{name}")
                return _result(
                    request_id, {"content": [{"type": "text", "text": text}]}
                )
            except JsonRpcError as exc:
                return _tool_error_result(request_id, exc)
            except Exception as exc:
                return _tool_error_result(
                    request_id,
                    JsonRpcError(-32603, f"服务内部错误：{exc}"),
                )
        raise JsonRpcError(-32601, f"Method not found: {method}")
    except JsonRpcError as exc:
        return _error(request_id, exc.code, exc.message)
    except Exception as exc:
        return _error(request_id, -32603, f"Internal error: {exc}")


def mbti_start(arguments):
    player_id = _require_player_id(arguments)
    mode = arguments.get("mode")
    if mode not in VALID_MODES:
        raise JsonRpcError(
            -32602,
            "mode 须为 short、full、short_fast、full_fast 之一。",
        )

    questions = get_questions(mode)
    total = len(questions)
    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        existing = conn.execute(
            "SELECT 1 FROM test_sessions WHERE player_id = ? AND game = ?",
            (player_id, GAME),
        ).fetchone()
        active_count = conn.execute(
            "SELECT COUNT(*) FROM test_sessions"
        ).fetchone()[0]
        if existing is None and active_count >= MAX_SESSIONS:
            raise JsonRpcError(-32000, "当前测试人数过多，请稍后再试")

        conn.execute(
            """
            INSERT INTO test_sessions (player_id, game, mode, current_question, answers, created_at, last_active)
            VALUES (?, ?, ?, 0, '[]', ?, ?)
            ON CONFLICT(player_id, game) DO UPDATE SET
                mode = excluded.mode,
                current_question = 0,
                answers = '[]',
                created_at = excluded.created_at,
                last_active = excluded.last_active
            """,
            (player_id, GAME, mode, now, now),
        )

    if is_fast_mode(mode):
        return _format_fast_batch(mode, questions, 0, total)
    return _format_question(mode, questions, 0)


def _coerce_score(value, error_message):
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            raise JsonRpcError(-32602, error_message)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 5:
        raise JsonRpcError(-32602, error_message)
    return value


def mbti_answer(arguments):
    player_id = _require_player_id(arguments)
    a_score = _coerce_score(
        arguments.get("a_score"),
        "a_score must be an integer from 0 to 5",
    )

    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT mode, current_question, answers FROM test_sessions WHERE player_id = ? AND game = ?",
            (player_id, GAME),
        ).fetchone()
        if row is None:
            _raise_no_active_session(conn, player_id)

        mode, current_question, answers_json = row
        if is_fast_mode(mode):
            raise JsonRpcError(
                -32602,
                f"{mode} 请使用 mbti_answer_batch 一次提交本批答案，不要用 mbti_answer。",
            )
        questions = get_questions(mode)
        answers = json.loads(answers_json)
        if current_question != len(answers):
            current_question = len(answers)
        if current_question >= len(questions):
            raise JsonRpcError(
                -32002,
                "当前 session 内题目已全部提交完毕，正在收尾；请勿重复提交。"
                "若需重测请稍候再调 mbti_start。",
            )

        answers.append(a_score)
        next_question = current_question + 1
        conn.execute(
            """
            UPDATE test_sessions
            SET current_question = ?, answers = ?, last_active = ?
            WHERE player_id = ? AND game = ?
            """,
            (next_question, json.dumps(answers), now, player_id, GAME),
        )

        if next_question >= len(questions):
            return _finish_test(conn, player_id, mode, questions, answers, now)
    return _format_question(mode, questions, next_question)


def mbti_answer_batch(arguments):
    player_id = _require_player_id(arguments)
    raw_scores = arguments.get("a_scores")
    if not isinstance(raw_scores, list) or not raw_scores:
        raise JsonRpcError(-32602, "a_scores must be a non-empty array")

    a_scores = []
    for item in raw_scores:
        a_scores.append(
            _coerce_score(
                item,
                "each a_scores item must be an integer from 0 to 5",
            )
        )

    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT mode, current_question, answers FROM test_sessions WHERE player_id = ? AND game = ?",
            (player_id, GAME),
        ).fetchone()
        if row is None:
            _raise_no_active_session(conn, player_id)

        mode, current_question, answers_json = row
        if not is_fast_mode(mode):
            raise JsonRpcError(
                -32602,
                f"{mode} 请使用 mbti_answer 逐题提交，不要用 mbti_answer_batch。",
            )

        questions = get_questions(mode)
        total = len(questions)
        answers = json.loads(answers_json)
        if current_question != len(answers):
            current_question = len(answers)
        if current_question >= total:
            raise JsonRpcError(
                -32002,
                "当前 session 内题目已全部提交完毕，正在收尾；请勿重复提交。"
                "若需重测请稍候再调 mbti_start。",
            )

        remaining = total - current_question
        batch_size = min(fast_batch_size(mode), remaining)
        if len(a_scores) != batch_size:
            raise JsonRpcError(
                -32602,
                f"本批须提交 {batch_size} 个分数（a_scores 长度应为 {batch_size}，当前为 {len(a_scores)}）",
            )

        answers.extend(a_scores)
        next_question = current_question + batch_size
        conn.execute(
            """
            UPDATE test_sessions
            SET current_question = ?, answers = ?, last_active = ?
            WHERE player_id = ? AND game = ?
            """,
            (next_question, json.dumps(answers), now, player_id, GAME),
        )

        if next_question >= total:
            return _finish_test(conn, player_id, mode, questions, answers, now)

    return _format_fast_batch(mode, questions, next_question, total)


def mbti_get_result(arguments):
    player_id = _require_player_id(arguments)
    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT result_value, result_detail, completed_at FROM test_results WHERE player_id = ? AND game = ?",
            (player_id, GAME),
        ).fetchone()
    if row is None:
        raise JsonRpcError(
            -32003,
            "未找到该 player_id 的已完成记录（可能从未做完测试，或完成已超过 48 小时已清理）。"
            "请先 mbti_start 并完成测试。",
        )
    mbti_type, result_detail_json, completed_at = row
    result_detail = json.loads(result_detail_json or "{}")
    mode = result_detail.get("mode") or "unknown"
    label = datetime.fromtimestamp(completed_at, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    try:
        return format_stored_result(mode, mbti_type, label)
    except ValueError as exc:
        raise JsonRpcError(-32603, f"Internal error: {exc}") from exc


def _finish_test(conn, player_id, mode, questions, answers, now):
    result = score_answers(questions, answers)
    mbti_type = result["type"]
    _save_result(conn, player_id, mode, mbti_type, result, now)
    conn.execute("DELETE FROM test_sessions WHERE player_id = ? AND game = ?", (player_id, GAME))
    return format_result(mode, questions, answers)


def _save_result(conn, player_id, mode, mbti_type, result, now):
    detail = {
        "mode": mode,
        "scores": result["scores"],
        "counts": result["counts"],
        "bias": result["bias"],
    }
    conn.execute(
        """
        INSERT INTO test_results (player_id, game, result_value, result_detail, completed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(player_id, game) DO UPDATE SET
            result_value = excluded.result_value,
            result_detail = excluded.result_detail,
            completed_at = excluded.completed_at
        """,
        (player_id, GAME, mbti_type, json.dumps(detail, ensure_ascii=False), now),
    )


def _format_question(mode, questions, index):
    question = questions[index]
    total = len(questions)
    number = index + 1
    if number == 1:
        return "\n".join(
            [
                f"【MBTI测试开始 · {mode}模式 · 共{total}题】",
                "",
                f"第{number}题 / 共{total}题",
                question["text"],
                "",
                f"A. {question['option_a']}",
                f"B. {question['option_b']}",
                "",
                f"请用 mbti_answer 传入 a_score：{_SCORE_HINT}",
            ]
        )
    return "\n".join(
        [
            f"第{number}题 / 共{total}题",
            question["text"],
            "",
            f"A. {question['option_a']}",
            f"B. {question['option_b']}",
            "",
            f"请用 mbti_answer 传入 a_score：{_SCORE_HINT}",
        ]
    )


def _format_fast_batch(mode, questions, start_index, total):
    remaining = total - start_index
    per_batch = fast_batch_size(mode)
    batch_size = min(per_batch, remaining)
    end_index = start_index + batch_size
    lines = []
    if start_index == 0:
        if mode == "short_fast":
            lines.append(
                f"【MBTI测试开始 · {mode}模式 · 共{total}题 · 快速批量（一次性提交全部 {total} 题）】"
            )
        else:
            lines.append(
                f"【MBTI测试开始 · {mode}模式 · 共{total}题 · 快速批量（每批最多{per_batch}题）】"
            )
    else:
        lines.append(f"【{mode} · 已完成 {start_index}/{total} 题】")
    lines.append("")
    batch_note = (
        f"请一次性提交 {batch_size} 个 a_score（本批共 {batch_size} 题，无需凑满 {per_batch} 题）"
        if batch_size < per_batch
        else f"请一次性提交 {batch_size} 个 a_score"
    )
    lines.append(f"本批第 {start_index + 1}–{end_index} 题（{batch_note}）")
    lines.append("")

    for idx in range(start_index, end_index):
        question = questions[idx]
        number = idx + 1
        lines.extend(
            [
                f"第{number}题 / 共{total}题",
                question["text"],
                "",
                f"A. {question['option_a']}",
                f"B. {question['option_b']}",
                "",
            ]
        )

    lines.append(
        f"请用 mbti_answer_batch 传入 a_scores（长度必须为 {batch_size}）："
        f"[{', '.join('?' for _ in range(batch_size))}]"
    )
    lines.append(f"评分：{_SCORE_HINT}")
    return "\n".join(lines)


def _require_player_id(arguments):
    player_id = arguments.get("player_id")
    if not isinstance(player_id, str) or PLAYER_ID_RE.fullmatch(player_id) is None:
        raise JsonRpcError(
            -32602,
            "player_id 须为 1–10 位英文字母或数字（正则 ^[a-zA-Z0-9]{1,10}$）。",
        )
    return player_id


def _raise_no_active_session(conn, player_id: str) -> None:
    """无进行中 session：区分「刚做完已删 session」与「从未开始/已过期」。"""
    row = conn.execute(
        "SELECT result_value, result_detail, completed_at FROM test_results WHERE player_id = ? AND game = ?",
        (player_id, GAME),
    ).fetchone()
    if row is not None:
        mbti_type, result_detail_json, completed_at = row
        result_detail = json.loads(result_detail_json or "{}")
        mode = result_detail.get("mode") or "unknown"
        label = datetime.fromtimestamp(completed_at, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        raise JsonRpcError(
            -32002,
            f"该 ID 的测试已于 {label} 完成（结果 {mbti_type}，模式 {mode}）。"
            "进行中 session 已结束，请勿再提交答案。"
            "请用 mbti_get_result 查看详情，或用 mbti_start 重新测试。",
        )
    raise JsonRpcError(
        -32001,
        "没有进行中的测试（可能从未调用 mbti_start，或超过 24 小时未活动 session 已被清理）。"
        "请先 mbti_start；若刚完成测试且未满 48 小时，可用 mbti_get_result 查档。",
    )


def _connect():
    return sqlite3.connect(DB_PATH)


def _init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS test_sessions (
            player_id TEXT NOT NULL,
            game TEXT NOT NULL,
            mode TEXT NOT NULL,
            current_question INTEGER NOT NULL DEFAULT 0,
            answers TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            last_active REAL NOT NULL,
            PRIMARY KEY (player_id, game)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS test_results (
            player_id TEXT NOT NULL,
            game TEXT NOT NULL,
            result_value TEXT NOT NULL,
            result_detail TEXT NOT NULL,
            completed_at REAL NOT NULL,
            PRIMARY KEY (player_id, game)
        )
        """
    )


def _cleanup_expired(conn, now):
    conn.execute(
        "DELETE FROM test_sessions WHERE last_active < ?",
        (now - SESSION_TTL_SECONDS,),
    )
    conn.execute(
        "DELETE FROM test_results WHERE completed_at < ?",
        (now - RESULT_TTL_SECONDS,),
    )


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_error_result(request_id, exc: "JsonRpcError"):
    """tools/call 业务错误走 MCP isError 文本，避免 JSON-RPC error 导致客户端 TaskGroup。"""
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": _user_facing_tool_error(exc)}],
            "isError": True,
        },
    )


def _user_facing_tool_error(exc: "JsonRpcError") -> str:
    msg = (exc.message or "").strip()
    if msg.startswith("【MBTI"):
        return msg
    prefix_by_code = {
        -32000: "【MBTI繁忙】",
        -32001: "【MBTI】",
        -32002: "【MBTI】",
        -32003: "【MBTI】",
        -32601: "【MBTI】",
        -32602: "【MBTI参数错误】",
        -32603: "【MBTI服务错误】",
    }
    prefix = prefix_by_code.get(exc.code, "【MBTI】")
    return f"{prefix}{msg}"


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
