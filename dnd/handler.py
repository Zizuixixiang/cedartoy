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


DB_PATH = "/opt/cedartoy/data/sessions.db"
GAME = "dnd"
MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 24 * 60 * 60
RESULT_TTL_SECONDS = 48 * 60 * 60
PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")


TOOLS = [
    {
        "name": "dnd_start",
        "description": "开始或重置一次 D&D 阵营测试。",
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
                    "description": "full 36题逐题；full_fast 36题一次性提交。",
                },
            },
            "required": ["player_id", "mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dnd_answer",
        "description": "提交当前题答案。answer 为 1-4，对应题面展示的四个选项。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "1-10位字母数字 ID。",
                    "pattern": "^[a-zA-Z0-9]{1,10}$",
                },
                "answer": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                    "description": "选择 1、2、3、4 中的一个。",
                },
            },
            "required": ["player_id", "answer"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dnd_answer_batch",
        "description": "快速模式专用：一次提交本批全部答案。answers 长度须等于本批题数，每项为 1-4。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "1-10位字母数字 ID。",
                    "pattern": "^[a-zA-Z0-9]{1,10}$",
                },
                "answers": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 4},
                    "minItems": 1,
                    "maxItems": FAST_BATCH_SIZE_MAX,
                    "description": "本批每题答案，顺序对应当前展示题目。",
                },
            },
            "required": ["player_id", "answers"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dnd_get_result",
        "description": "查询该 player_id 最近一次已完成 D&D 阵营测试结果。",
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
                    "serverInfo": {"name": "cedartoy-dnd", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            try:
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name == "dnd_start":
                    text = dnd_start(arguments)
                elif name == "dnd_answer":
                    text = dnd_answer(arguments)
                elif name == "dnd_answer_batch":
                    text = dnd_answer_batch(arguments)
                elif name == "dnd_get_result":
                    text = dnd_get_result(arguments)
                else:
                    raise JsonRpcError(-32601, f"未知工具：{name}")
                return _result(request_id, {"content": [{"type": "text", "text": text}]})
            except JsonRpcError as exc:
                return _tool_error_result(request_id, exc)
            except Exception as exc:
                return _tool_error_result(request_id, JsonRpcError(-32603, f"服务内部错误：{exc}"))
        raise JsonRpcError(-32601, f"Method not found: {method}")
    except JsonRpcError as exc:
        return _error(request_id, exc.code, exc.message)
    except Exception as exc:
        return _error(request_id, -32603, f"Internal error: {exc}")


def dnd_start(arguments):
    player_id = _require_player_id(arguments)
    mode = arguments.get("mode")
    if mode not in VALID_MODES:
        raise JsonRpcError(-32602, "mode 须为 full 或 full_fast。")

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
        active_count = conn.execute("SELECT COUNT(*) FROM test_sessions").fetchone()[0]
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


def dnd_answer(arguments):
    player_id = _require_player_id(arguments)
    answer = _coerce_answer(arguments.get("answer"), "answer must be an integer from 1 to 4")
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
            raise JsonRpcError(-32602, f"{mode} 请使用 dnd_answer_batch 一次提交本批答案，不要用 dnd_answer。")
        questions = get_questions(mode)
        answers = json.loads(answers_json)
        if current_question != len(answers):
            current_question = len(answers)
        if current_question >= len(questions):
            raise JsonRpcError(-32002, "当前 session 内题目已全部提交完毕，请勿重复提交。")

        answers.append(answer)
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


def dnd_answer_batch(arguments):
    player_id = _require_player_id(arguments)
    raw_answers = arguments.get("answers")
    if not isinstance(raw_answers, list) or not raw_answers:
        raise JsonRpcError(-32602, "answers must be a non-empty array")
    answers_batch = [
        _coerce_answer(item, "each answers item must be an integer from 1 to 4")
        for item in raw_answers
    ]

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
            raise JsonRpcError(-32602, f"{mode} 请使用 dnd_answer 逐题提交，不要用 dnd_answer_batch。")
        questions = get_questions(mode)
        total = len(questions)
        answers = json.loads(answers_json)
        if current_question != len(answers):
            current_question = len(answers)
        if current_question >= total:
            raise JsonRpcError(-32002, "当前 session 内题目已全部提交完毕，请勿重复提交。")

        batch_size = min(fast_batch_size(mode), total - current_question)
        if len(answers_batch) != batch_size:
            raise JsonRpcError(-32602, f"本批须提交 {batch_size} 个答案（当前为 {len(answers_batch)}）")

        answers.extend(answers_batch)
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


def dnd_get_result(arguments):
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
        raise JsonRpcError(-32003, "未找到该 player_id 的已完成记录。请先 dnd_start 并完成测试。")
    result_value, detail_json, completed_at = row
    detail = json.loads(detail_json or "{}")
    mode = detail.get("mode") or "unknown"
    label = datetime.fromtimestamp(completed_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return format_stored_result(mode, result_value, detail, label)


def _finish_test(conn, player_id, mode, questions, answers, now):
    result = score_answers(questions, answers)
    detail = {
        "mode": mode,
        "raw_buckets": result["raw_buckets"],
        "scores": result["scores"],
        "bands": result["bands"],
        "bucket_winners": result["bucket_winners"],
        "name_en": result["name_en"],
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
        (player_id, GAME, result["alignment"], json.dumps(detail, ensure_ascii=False), now),
    )
    conn.execute("DELETE FROM test_sessions WHERE player_id = ? AND game = ?", (player_id, GAME))
    return format_result(mode, questions, answers)


def _strip_scoring(questions):
    return [
        {**q, "options": [{"value": o["value"], "text": o["text"]} for o in q["options"]]}
        for q in questions
    ]


def _format_question(mode, questions, index):
    questions = _strip_scoring(questions)
    question = questions[index]
    total = len(questions)
    lines = [f"第{index + 1}题 / 共{total}题", question["text"], ""]
    if index == 0:
        lines.insert(0, "")
        lines.insert(0, f"【DND阵营测试开始 · {mode}模式 · 共{total}题】")
    for option in question["options"]:
        lines.append(f"{option['value']}. {option['text']}")
    lines.extend(["", "请用 dnd_answer 传入 answer：1、2、3 或 4。"])
    return "\n".join(lines)


def _format_fast_batch(mode, questions, start_index, total):
    questions = _strip_scoring(questions)
    remaining = total - start_index
    per_batch = fast_batch_size(mode)
    batch_size = min(per_batch, remaining)
    end_index = start_index + batch_size
    lines = []
    if start_index == 0:
        lines.append(f"【DND阵营测试开始 · {mode}模式 · 共{total}题 · 快速批量】")
    else:
        lines.append(f"【{mode} · 已完成 {start_index}/{total} 题】")
    lines.extend(["", f"本批第 {start_index + 1}-{end_index} 题，请一次性提交 {batch_size} 个答案。", ""])
    for idx in range(start_index, end_index):
        question = questions[idx]
        lines.extend([f"第{idx + 1}题 / 共{total}题", question["text"], ""])
        for option in question["options"]:
            lines.append(f"{option['value']}. {option['text']}")
        lines.append("")
    lines.append(f"请用 dnd_answer_batch 传入 answers（长度必须为 {batch_size}）：[{', '.join('?' for _ in range(batch_size))}]")
    return "\n".join(lines)


def _coerce_answer(value, error_message):
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            raise JsonRpcError(-32602, error_message)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 4:
        raise JsonRpcError(-32602, error_message)
    return value


def _require_player_id(arguments):
    player_id = arguments.get("player_id")
    if not isinstance(player_id, str) or PLAYER_ID_RE.fullmatch(player_id) is None:
        raise JsonRpcError(-32602, "player_id 须为 1-10 位英文字母或数字。")
    return player_id


def _raise_no_active_session(conn, player_id):
    row = conn.execute(
        "SELECT result_value, result_detail, completed_at FROM test_results WHERE player_id = ? AND game = ?",
        (player_id, GAME),
    ).fetchone()
    if row is not None:
        result_value, detail_json, completed_at = row
        detail = json.loads(detail_json or "{}")
        mode = detail.get("mode") or "unknown"
        label = datetime.fromtimestamp(completed_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        raise JsonRpcError(
            -32002,
            f"该 ID 的 DND 测试已于 {label} 完成（结果 {result_value}，模式 {mode}）。请用 dnd_get_result 查看详情，或用 dnd_start 重新测试。",
        )
    raise JsonRpcError(-32001, "没有进行中的 DND 测试，请先 dnd_start。")


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
    conn.execute("DELETE FROM test_sessions WHERE last_active < ?", (now - SESSION_TTL_SECONDS,))
    conn.execute("DELETE FROM test_results WHERE completed_at < ?", (now - RESULT_TTL_SECONDS,))


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_error_result(request_id, exc):
    return _result(
        request_id,
        {"content": [{"type": "text", "text": _user_facing_tool_error(exc)}], "isError": True},
    )


def _user_facing_tool_error(exc):
    prefix_by_code = {
        -32000: "【DND繁忙】",
        -32001: "【DND】",
        -32002: "【DND】",
        -32003: "【DND】",
        -32601: "【DND】",
        -32602: "【DND参数错误】",
        -32603: "【DND服务错误】",
    }
    return f"{prefix_by_code.get(exc.code, '【DND】')}{exc.message}"


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
