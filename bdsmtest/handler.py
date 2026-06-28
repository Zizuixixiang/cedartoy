import json
import re
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import api
from .api import BdsmApiError
from .questions import (
    SCALE_HINT,
    SCORE_MAX,
    SCORE_MIN,
    VALID_MODES,
    archetype_label,
    is_fast_mode,
    wording_zh,
)

DB_PATH = "/opt/cedartoy/data/sessions.db"
GAME = "bdsmtest"
MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 24 * 60 * 60
RESULT_TTL_SECONDS = 48 * 60 * 60
PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")
RESULT_URL = "https://bdsmtest.org/r/{rid}"

TOTAL_QUESTIONS_HINT = 93

_ANSWER_HINT = f"评分：{SCALE_HINT}"


TOOLS = [
    {
        "name": "bdsmtest_start",
        "description": (
            "开始或重置一次 BDSMTest 测试（实时调用 bdsmtest.org 官方接口算分）。"
            "mode=normal 逐题作答（先返回第1题）；mode=fast 一次性返回全部93题，"
            "再用 bdsmtest_answer_batch 一次提交。"
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
                "mode": {
                    "type": "string",
                    "enum": list(VALID_MODES),
                    "description": "normal 逐题；fast 一次性提交全部题。",
                },
            },
            "required": ["player_id", "mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bdsmtest_answer",
        "description": (
            "逐题模式（normal）提交当前题的认同度 score（1-7 整数）。"
            f"{SCALE_HINT}。返回下一题；答完最后一题自动算分。"
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
                "score": {
                    "type": "integer",
                    "minimum": SCORE_MIN,
                    "maximum": SCORE_MAX,
                    "description": SCALE_HINT,
                },
            },
            "required": ["player_id", "score"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bdsmtest_answer_batch",
        "description": (
            "快速模式（fast）专用：一次性提交全部题答案并算分。"
            "answers 是 {题号id: 认同度1-7} 的对象，键为 bdsmtest_start 返回的题号 id，"
            "须覆盖全部题目。"
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
                "answers": {
                    "type": "object",
                    "description": "{题号id字符串: 1-7整数}，须覆盖全部题号。",
                    "additionalProperties": {
                        "type": "integer",
                        "minimum": SCORE_MIN,
                        "maximum": SCORE_MAX,
                    },
                },
            },
            "required": ["player_id", "answers"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bdsmtest_get_result",
        "description": (
            "查询该 player_id 最近一次已完成测试的结果（各原型百分比，"
            "完成超过48小时自动删除）。"
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
                    "serverInfo": {"name": "cedartoy-bdsmtest", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            try:
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name == "bdsmtest_start":
                    text = bdsmtest_start(arguments)
                elif name == "bdsmtest_answer":
                    text = bdsmtest_answer(arguments)
                elif name == "bdsmtest_answer_batch":
                    text = bdsmtest_answer_batch(arguments)
                elif name == "bdsmtest_get_result":
                    text = bdsmtest_get_result(arguments)
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


def bdsmtest_start(arguments):
    player_id = _require_player_id(arguments)
    mode = arguments.get("mode")
    if mode not in VALID_MODES:
        raise JsonRpcError(-32602, "mode 须为 normal、fast 之一。")

    # 先拿名额，再调原站接口，避免占用过多并发。
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

    # 调原站：开局 + 拉全部题目。
    try:
        session = api.init_session()
        questions = api.fetch_questions(session["rauth"], session["pdata"])
    except BdsmApiError as exc:
        raise JsonRpcError(-32010, str(exc)) from exc

    total = len(questions)
    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        conn.execute(
            """
            INSERT INTO test_sessions
                (player_id, game, mode, current_question, answers,
                 rauth, questions, pdata, created_at, last_active)
            VALUES (?, ?, ?, 0, '[]', ?, ?, ?, ?, ?)
            ON CONFLICT(player_id, game) DO UPDATE SET
                mode = excluded.mode,
                current_question = 0,
                answers = '[]',
                rauth = excluded.rauth,
                questions = excluded.questions,
                pdata = excluded.pdata,
                created_at = excluded.created_at,
                last_active = excluded.last_active
            """,
            (
                player_id, GAME, mode,
                json.dumps(session["rauth"], ensure_ascii=False),
                json.dumps(questions, ensure_ascii=False),
                json.dumps(session["pdata"], ensure_ascii=False),
                now, now,
            ),
        )

    if is_fast_mode(mode):
        return _format_fast_all(questions)
    return _format_question(questions, 0, with_header=True)


def bdsmtest_answer(arguments):
    player_id = _require_player_id(arguments)
    score = _coerce_score(
        arguments.get("score"),
        f"score must be an integer from {SCORE_MIN} to {SCORE_MAX}",
    )

    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = _load_session(conn, player_id)
        if row is None:
            _raise_no_active_session(conn, player_id)

        mode, current_question, answers, rauth, questions, pdata = row
        if is_fast_mode(mode):
            raise JsonRpcError(
                -32602,
                "fast 模式请用 bdsmtest_answer_batch 一次提交全部答案，不要用 bdsmtest_answer。",
            )
        total = len(questions)
        if current_question != len(answers):
            current_question = len(answers)

        # 已答满但尚未出结果：上次算分可能失败，这里重试收尾。
        if current_question >= total:
            return _finish(conn, player_id, mode, rauth, pdata, questions, answers, now)

        answers.append(score)
        next_question = current_question + 1
        conn.execute(
            "UPDATE test_sessions SET current_question = ?, answers = ?, last_active = ? "
            "WHERE player_id = ? AND game = ?",
            (next_question, json.dumps(answers), now, player_id, GAME),
        )
        if next_question >= total:
            # 先落盘答案再算分：算分失败会抛错触发事务回滚，若不先提交，
            # 刚追加的这条答案会一并被回滚，session 永远停在 total-1 题无法收尾。
            conn.commit()
            return _finish(conn, player_id, mode, rauth, pdata, questions, answers, now)
    return _format_question(questions, next_question, with_header=False)


def bdsmtest_answer_batch(arguments):
    player_id = _require_player_id(arguments)
    raw = arguments.get("answers")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise JsonRpcError(-32602, "answers must be an object of {题号id: 1-7}")
    if not isinstance(raw, dict) or not raw:
        raise JsonRpcError(-32602, "answers must be a non-empty object of {题号id: 1-7}")

    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = _load_session(conn, player_id)
        if row is None:
            _raise_no_active_session(conn, player_id)

        mode, current_question, stored_answers, rauth, questions, pdata = row
        if not is_fast_mode(mode):
            raise JsonRpcError(
                -32602,
                "normal 模式请用 bdsmtest_answer 逐题提交，不要用 bdsmtest_answer_batch。",
            )

        question_ids = [q["id"] for q in questions]
        answers_by_id = _validate_batch(raw, question_ids)
        # 按题目顺序铺平成答案列表（与 normal 模式一致），便于收尾算分。
        answers = [answers_by_id[qid] for qid in question_ids]
        conn.execute(
            "UPDATE test_sessions SET current_question = ?, answers = ?, last_active = ? "
            "WHERE player_id = ? AND game = ?",
            (len(answers), json.dumps(answers), now, player_id, GAME),
        )
        # 先落盘答案再算分：算分失败会回滚事务，先提交可保住答案以便重试收尾。
        conn.commit()
        return _finish(conn, player_id, mode, rauth, pdata, questions, answers, now)


def bdsmtest_get_result(arguments):
    player_id = _require_player_id(arguments)
    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT result_detail, completed_at FROM test_results WHERE player_id = ? AND game = ?",
            (player_id, GAME),
        ).fetchone()
    if row is None:
        raise JsonRpcError(
            -32003,
            "未找到该 player_id 的已完成记录（可能从未做完测试，或完成已超过 48 小时已清理）。"
            "请先 bdsmtest_start 并完成测试。",
        )
    result_detail = json.loads(row[0] or "{}")
    label = datetime.fromtimestamp(row[1], tz=ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M"
    )
    return _format_result(
        result_detail.get("scores", []),
        result_detail.get("rid"),
        header=f"【BDSMTest 历史结果 · {label}】",
    )


def _finish(conn, player_id, mode, rauth, pdata, questions, answers, now):
    """用已收集的答案调原站算分、存档、清 session。"""
    qdata = {str(questions[i]["id"]): answers[i] for i in range(len(answers))}
    try:
        outcome = api.submit_and_score(rauth, pdata, qdata)
    except BdsmApiError as exc:
        # 保留 session，便于再次调用同一工具重试收尾。
        raise JsonRpcError(-32010, str(exc)) from exc

    scores = outcome["scores"]
    rid = outcome.get("rid") or rauth.get("rid")
    _save_result(conn, player_id, mode, scores, rid, now)
    conn.execute(
        "DELETE FROM test_sessions WHERE player_id = ? AND game = ?", (player_id, GAME)
    )
    return _format_result(scores, rid, header="【BDSMTest 测试完成】")


def _save_result(conn, player_id, mode, scores, rid, now):
    detail = {"mode": mode, "scores": scores, "rid": rid}
    result_value = scores[0].get("name") if scores else ""
    conn.execute(
        """
        INSERT INTO test_results (player_id, game, result_value, result_detail, completed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(player_id, game) DO UPDATE SET
            result_value = excluded.result_value,
            result_detail = excluded.result_detail,
            completed_at = excluded.completed_at
        """,
        (player_id, GAME, result_value, json.dumps(detail, ensure_ascii=False), now),
    )


def _validate_batch(raw, question_ids):
    """校验 answers dict：键覆盖全部题号、值为 1-7 整数。返回 {int id: int score}。"""
    id_set = set(question_ids)
    out = {}
    for key, value in raw.items():
        try:
            qid = int(key)
        except (TypeError, ValueError):
            raise JsonRpcError(-32602, f"题号 id 必须是整数，收到 {key!r}")
        if qid not in id_set:
            raise JsonRpcError(-32602, f"题号 {qid} 不属于本次测试题目。")
        out[qid] = _coerce_score(
            value, f"题号 {qid} 的答案须为 {SCORE_MIN}-{SCORE_MAX} 整数"
        )
    missing = [qid for qid in question_ids if qid not in out]
    if missing:
        raise JsonRpcError(
            -32602,
            f"answers 缺少 {len(missing)} 道题（须覆盖全部 {len(question_ids)} 题）。"
            f"缺失题号示例：{missing[:8]}",
        )
    return out


def _coerce_score(value, error_message):
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            raise JsonRpcError(-32602, error_message)
    if not isinstance(value, int) or isinstance(value, bool) or not SCORE_MIN <= value <= SCORE_MAX:
        raise JsonRpcError(-32602, error_message)
    return value


def _format_question(questions, index, with_header):
    question = questions[index]
    total = len(questions)
    number = index + 1
    lines = []
    if with_header:
        lines.append(f"【BDSMTest 开始 · 逐题模式 · 共{total}题】")
        lines.append("")
    lines.extend(
        [
            f"第{number}题 / 共{total}题",
            wording_zh(question["id"], question.get("wording")),
            "",
            f"请用 bdsmtest_answer 传入 score：{SCALE_HINT}",
        ]
    )
    return "\n".join(lines)


def _format_fast_all(questions):
    total = len(questions)
    lines = [
        f"【BDSMTest 开始 · 一次性模式 · 共{total}题】",
        "",
        "请用 bdsmtest_answer_batch 一次性提交全部答案，"
        'answers 为 {"题号id": 认同度} 对象，例如 {"3": 7, "98": 1, ...}。',
        _ANSWER_HINT,
        "",
    ]
    for idx, question in enumerate(questions, start=1):
        lines.append(
            f"第{idx}题（id={question['id']}）"
            f"{wording_zh(question['id'], question.get('wording'))}"
        )
    lines.append("")
    lines.append(
        f"提交格式：answers={{题号id: 1-7}}，须覆盖全部 {total} 题。"
    )
    return "\n".join(lines)


def _format_result(scores, rid, header):
    TOP_N = 10
    lines = [header, "", "你的 BDSM 原型倾向（按百分比排序）：", ""]
    for i, sc in enumerate(scores):
        name = sc.get("name", "")
        pct = sc.get("score", 0)
        bar = "█" * (pct // 5)
        desc = sc.get("description", "")
        pair = sc.get("pairdesc", "")
        lines.append(f"{archetype_label(name)} {pct}%  {bar}".rstrip())
        if i < TOP_N and (desc or pair):
            if desc:
                lines.append(f"  → {desc}")
            if pair:
                lines.append(f"  → {pair}")
            lines.append("")
    lines.append("")
    if rid:
        lines.append(f"完整结果详见：{RESULT_URL.format(rid=rid)}")
    lines.append("（结果已存档 48 小时，可用 bdsmtest_get_result 凭 player_id 查询。）")
    return "\n".join(lines)


def _require_player_id(arguments):
    player_id = arguments.get("player_id")
    if not isinstance(player_id, str) or PLAYER_ID_RE.fullmatch(player_id) is None:
        raise JsonRpcError(
            -32602,
            "player_id 须为 1–10 位英文字母或数字（正则 ^[a-zA-Z0-9]{1,10}$）。",
        )
    return player_id


def _load_session(conn, player_id):
    row = conn.execute(
        "SELECT mode, current_question, answers, rauth, questions, pdata "
        "FROM test_sessions WHERE player_id = ? AND game = ?",
        (player_id, GAME),
    ).fetchone()
    if row is None:
        return None
    mode, current_question, answers_json, rauth_json, questions_json, pdata_json = row
    return (
        mode,
        current_question,
        json.loads(answers_json or "[]"),
        json.loads(rauth_json or "{}"),
        json.loads(questions_json or "[]"),
        json.loads(pdata_json or "{}"),
    )


def _raise_no_active_session(conn, player_id):
    row = conn.execute(
        "SELECT result_detail, completed_at FROM test_results WHERE player_id = ? AND game = ?",
        (player_id, GAME),
    ).fetchone()
    if row is not None:
        label = datetime.fromtimestamp(row[1], tz=ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M"
        )
        raise JsonRpcError(
            -32002,
            f"该 ID 的测试已于 {label} 完成。进行中 session 已结束，请勿再提交答案。"
            "请用 bdsmtest_get_result 查看详情，或用 bdsmtest_start 重新测试。",
        )
    raise JsonRpcError(
        -32001,
        "没有进行中的测试（可能从未调用 bdsmtest_start，或超过 24 小时未活动 session 已被清理）。"
        "请先 bdsmtest_start；若刚完成测试且未满 48 小时，可用 bdsmtest_get_result 查档。",
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
    # BDSMTest 额外需要的列（与 mbti 共用 test_sessions，按需补列，幂等）。
    existing = {r[1] for r in conn.execute("PRAGMA table_info(test_sessions)")}
    for column in ("rauth", "questions", "pdata"):
        if column not in existing:
            conn.execute(f"ALTER TABLE test_sessions ADD COLUMN {column} TEXT")


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


def _tool_error_result(request_id, exc):
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": _user_facing_tool_error(exc)}],
            "isError": True,
        },
    )


def _user_facing_tool_error(exc):
    msg = (exc.message or "").strip()
    if msg.startswith("【BDSMTest"):
        return msg
    prefix_by_code = {
        -32000: "【BDSMTest繁忙】",
        -32001: "【BDSMTest】",
        -32002: "【BDSMTest】",
        -32003: "【BDSMTest】",
        -32010: "【BDSMTest原站】",
        -32601: "【BDSMTest】",
        -32602: "【BDSMTest参数错误】",
        -32603: "【BDSMTest服务错误】",
    }
    prefix = prefix_by_code.get(exc.code, "【BDSMTest】")
    return f"{prefix}{msg}"


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
