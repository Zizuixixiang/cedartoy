"""Shared persistent state machine for fixed-question psychological scales."""

import json
import re
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo


MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 24 * 60 * 60
RESULT_TTL_SECONDS = 48 * 60 * 60
PLAYER_ID_RE = re.compile(r"^(?:guest:[a-zA-Z0-9]{1,64}|[a-zA-Z0-9]{1,64}(?::[1-5])?)$")
ACCOUNT_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]{2,20}$")


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ScaleTestEngine:
    """The dnd-style start/answer/batch/result lifecycle, configured per scale."""

    def __init__(
        self,
        *,
        game,
        title,
        db_path,
        questions,
        scoring,
        answer_min,
        answer_max,
        prompt,
        supports_compare=True,
        account_db_path=None,
    ):
        self.game = game
        self.title = title
        self._db_path = db_path
        self.questions = questions
        self.scoring = scoring
        self.answer_min = answer_min
        self.answer_max = answer_max
        self.prompt = prompt
        self.supports_compare = supports_compare
        self._account_db_path = account_db_path
        self.tools = self._build_tools()

    def handle_mcp(self, payload):
        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        try:
            if method == "initialize":
                return self._result(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": f"cedartoy-{self.game}", "version": "1.0.0"},
                        "capabilities": {"tools": {}},
                    },
                )
            if method == "tools/list":
                return self._result(request_id, {"tools": self.tools})
            if method == "tools/call":
                try:
                    name = params.get("name")
                    arguments = params.get("arguments") or {}
                    functions = {
                        f"{self.game}_start": self.start,
                        f"{self.game}_answer": self.answer,
                        f"{self.game}_answer_batch": self.answer_batch,
                        f"{self.game}_get_result": self.get_result,
                    }
                    if self.supports_compare:
                        functions[f"{self.game}_compare"] = self.compare
                    function = functions.get(name)
                    if function is None:
                        raise JsonRpcError(-32601, f"未知工具：{name}")
                    text = function(arguments)
                    return self._result(request_id, {"content": [{"type": "text", "text": text}]})
                except JsonRpcError as exc:
                    return self._tool_error_result(request_id, exc)
                except Exception as exc:
                    return self._tool_error_result(
                        request_id, JsonRpcError(-32603, f"服务内部错误：{exc}")
                    )
            raise JsonRpcError(-32601, f"Method not found: {method}")
        except JsonRpcError as exc:
            return self._error(request_id, exc.code, exc.message)
        except Exception as exc:
            return self._error(request_id, -32603, f"Internal error: {exc}")

    def start(self, arguments):
        player_id = self._require_player_id(arguments, "player_id")
        mode = arguments.get("mode")
        if mode not in self.questions.VALID_MODES:
            raise JsonRpcError(-32602, "mode 须为 full 或 full_fast。")

        questions = self.questions.get_questions(mode)
        now = time.time()
        with self._connect() as conn:
            self._init_db(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired(conn, now)
            existing = conn.execute(
                "SELECT 1 FROM test_sessions WHERE player_id = ? AND game = ?",
                (player_id, self.game),
            ).fetchone()
            active_count = conn.execute("SELECT COUNT(*) FROM test_sessions").fetchone()[0]
            if existing is None and active_count >= MAX_SESSIONS:
                raise JsonRpcError(-32000, "当前测试人数过多，请稍后再试")
            conn.execute(
                """
                INSERT INTO test_sessions
                    (player_id, game, mode, current_question, answers, created_at, last_active)
                VALUES (?, ?, ?, 0, '[]', ?, ?)
                ON CONFLICT(player_id, game) DO UPDATE SET
                    mode = excluded.mode,
                    current_question = 0,
                    answers = '[]',
                    created_at = excluded.created_at,
                    last_active = excluded.last_active
                """,
                (player_id, self.game, mode, now, now),
            )
            conn.commit()

        if self.questions.is_fast_mode(mode):
            return self._format_fast_batch(mode, questions, 0)
        return self._format_question(mode, questions, 0)

    def answer(self, arguments):
        player_id = self._require_player_id(arguments, "player_id")
        answer = self._coerce_answer(arguments.get("answer"))
        now = time.time()
        with self._connect() as conn:
            self._init_db(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired(conn, now)
            row = conn.execute(
                "SELECT mode, current_question, answers FROM test_sessions WHERE player_id = ? AND game = ?",
                (player_id, self.game),
            ).fetchone()
            if row is None:
                self._raise_no_active_session(conn, player_id)
            mode, current_question, answers_json = row
            if self.questions.is_fast_mode(mode):
                raise JsonRpcError(
                    -32602,
                    f"{mode} 请使用 {self.game}_answer_batch 一次提交全部答案，不要用 {self.game}_answer。",
                )
            questions = self.questions.get_questions(mode)
            answers = json.loads(answers_json)
            current_question = len(answers)
            if current_question >= len(questions):
                raise JsonRpcError(-32002, "当前 session 内题目已全部提交完毕，请勿重复提交。")
            self._validate_question_answer(questions[current_question], answer)

            answers.append(answer)
            next_question = current_question + 1
            conn.execute(
                """
                UPDATE test_sessions SET current_question = ?, answers = ?, last_active = ?
                WHERE player_id = ? AND game = ?
                """,
                (next_question, json.dumps(answers), now, player_id, self.game),
            )
            if next_question >= len(questions):
                text = self._finish_test(conn, player_id, mode, questions, answers, now)
                conn.commit()
                return text
            conn.commit()
        return self._format_question(mode, questions, next_question)

    def answer_batch(self, arguments):
        player_id = self._require_player_id(arguments, "player_id")
        raw_answers = arguments.get("answers")
        if isinstance(raw_answers, str):
            try:
                raw_answers = json.loads(raw_answers)
            except (json.JSONDecodeError, ValueError):
                raise JsonRpcError(-32602, "answers 必须是非空数组")
        if not isinstance(raw_answers, list) or not raw_answers:
            raise JsonRpcError(-32602, "answers 必须是非空数组")
        answers_batch = [self._coerce_answer(item) for item in raw_answers]

        now = time.time()
        with self._connect() as conn:
            self._init_db(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired(conn, now)
            row = conn.execute(
                "SELECT mode, current_question, answers FROM test_sessions WHERE player_id = ? AND game = ?",
                (player_id, self.game),
            ).fetchone()
            if row is None:
                self._raise_no_active_session(conn, player_id)
            mode, current_question, answers_json = row
            if not self.questions.is_fast_mode(mode):
                raise JsonRpcError(
                    -32602,
                    f"{mode} 请使用 {self.game}_answer 逐题提交，不要用 {self.game}_answer_batch。",
                )
            questions = self.questions.get_questions(mode)
            answers = json.loads(answers_json)
            current_question = len(answers)
            total = len(questions)
            if current_question >= total:
                raise JsonRpcError(-32002, "当前 session 内题目已全部提交完毕，请勿重复提交。")

            batch_size = min(self.questions.fast_batch_size(mode), total - current_question)
            if len(answers_batch) != batch_size:
                raise JsonRpcError(
                    -32602, f"本批须提交 {batch_size} 个答案（当前为 {len(answers_batch)}）"
                )
            for question, answer in zip(
                questions[current_question:current_question + batch_size], answers_batch
            ):
                self._validate_question_answer(question, answer)
            answers.extend(answers_batch)
            next_question = current_question + batch_size
            conn.execute(
                """
                UPDATE test_sessions SET current_question = ?, answers = ?, last_active = ?
                WHERE player_id = ? AND game = ?
                """,
                (next_question, json.dumps(answers), now, player_id, self.game),
            )
            if next_question >= total:
                text = self._finish_test(conn, player_id, mode, questions, answers, now)
                conn.commit()
                return text
            conn.commit()
        return self._format_fast_batch(mode, questions, next_question)

    def get_result(self, arguments):
        player_id = self._require_player_id(arguments, "player_id")
        row = self._load_result(player_id)
        if row is None:
            raise JsonRpcError(
                -32003,
                f"未找到该 player_id 的已完成记录。请先 {self.game}_start 并完成测试。",
            )
        result_value, detail, completed_at = row
        mode = detail.get("mode") or "unknown"
        label = self._time_label(completed_at)
        return (
            self.scoring.format_stored_result(mode, result_value, detail, label)
            + f"\n存档身份：{player_id}"
        )

    def compare(self, arguments):
        if not self.supports_compare:
            raise JsonRpcError(-32601, f"{self.game} 不提供 compare。")
        return self.compare_data(arguments)["text"]

    def compare_data(self, arguments):
        if not self.supports_compare:
            raise JsonRpcError(-32601, f"{self.game} 不提供 compare。")
        player_id_a = self._resolve_compare_player_id(arguments, "player_id_a")
        player_id_b = self._resolve_compare_player_id(arguments, "player_id_b")
        now = time.time()
        with self._connect() as conn:
            self._init_db(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired(conn, now)
            rows = []
            for player_id in (player_id_a, player_id_b):
                row = conn.execute(
                    """
                    SELECT result_value, result_detail, completed_at FROM test_results
                    WHERE player_id = ? AND game = ?
                    """,
                    (player_id, self.game),
                ).fetchone()
                if row is None:
                    raise JsonRpcError(
                        -32003,
                        "结果不存在。可输入对方用户名或账号 id；游客结果超过 48 小时会清理。",
                    )
                rows.append((row[0], json.loads(row[1] or "{}"), row[2]))
            conn.commit()
        result_a, detail_a, _ = rows[0]
        result_b, detail_b, _ = rows[1]
        data = self.scoring.build_compare_data(
            player_id_a, result_a, detail_a, player_id_b, result_b, detail_b
        )
        for key, player_id in (("player_a", player_id_a), ("player_b", player_id_b)):
            person = data.get(key)
            if isinstance(person, dict):
                person["display_name"] = self._compare_display_name(player_id)
        return {"text": self.scoring.format_compare(data), "data": data}

    def _load_result(self, player_id):
        now = time.time()
        with self._connect() as conn:
            self._init_db(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired(conn, now)
            row = conn.execute(
                """
                SELECT result_value, result_detail, completed_at FROM test_results
                WHERE player_id = ? AND game = ?
                """,
                (player_id, self.game),
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return row[0], json.loads(row[1] or "{}"), row[2]

    def _finish_test(self, conn, player_id, mode, questions, answers, now):
        result = self.scoring.score_answers(questions, answers)
        result_value = result["result_value"]
        detail = {"mode": mode, **{key: value for key, value in result.items() if key != "result_value"}}
        conn.execute(
            """
            INSERT INTO test_results (player_id, game, result_value, result_detail, completed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(player_id, game) DO UPDATE SET
                result_value = excluded.result_value,
                result_detail = excluded.result_detail,
                completed_at = excluded.completed_at
            """,
            (
                player_id,
                self.game,
                result_value,
                json.dumps(detail, ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            "DELETE FROM test_sessions WHERE player_id = ? AND game = ?",
            (player_id, self.game),
        )
        total = conn.execute(
            "SELECT COUNT(*) FROM test_results WHERE game = ?", (self.game,)
        ).fetchone()[0]
        same = conn.execute(
            "SELECT COUNT(*) FROM test_results WHERE game = ? AND result_value = ?",
            (self.game, result_value),
        ).fetchone()[0]
        return (
            self.scoring.format_result(mode, result)
            + f"\n存档身份：{player_id}"
            + f"\n全平台已有{int(total)}只完成此测试，与你同型的共{int(same)}只（含你）"
        )

    def _format_question(self, mode, questions, index):
        question = self._public_question(questions[index])
        total = len(questions)
        lines = []
        if index == 0:
            lines.extend([f"【{self.title}开始 · {mode}模式 · 共{total}题】", self.prompt, ""])
        lines.extend([f"第{index + 1}题 / 共{total}题", question["text"], ""])
        for option in question["options"]:
            lines.append(f"{option['value']}. {option['text']}")
        lines.extend(
            [
                "",
                f"请用 {self.game}_answer 传入 answer：{self.answer_min}~{self.answer_max}。",
            ]
        )
        return "\n".join(lines)

    def _format_fast_batch(self, mode, questions, start_index):
        total = len(questions)
        batch_size = min(self.questions.fast_batch_size(mode), total - start_index)
        end_index = start_index + batch_size
        lines = [f"【{self.title}开始 · {mode}模式 · 共{total}题 · 快速批量】"]
        if start_index == 0:
            lines.extend([self.prompt, ""])
        else:
            lines.extend([f"已完成 {start_index}/{total} 题", ""])
        lines.extend([f"本批第 {start_index + 1}-{end_index} 题，请一次性提交 {batch_size} 个答案。", ""])
        for index in range(start_index, end_index):
            question = self._public_question(questions[index])
            lines.extend([f"第{index + 1}题 / 共{total}题", question["text"], ""])
            for option in question["options"]:
                lines.append(f"{option['value']}. {option['text']}")
            lines.append("")
        placeholders = ", ".join("?" for _ in range(batch_size))
        lines.append(
            f"请用 {self.game}_answer_batch 传入 answers（长度必须为 {batch_size}）：[{placeholders}]"
        )
        return "\n".join(lines)

    @staticmethod
    def _public_question(question):
        return {
            "text": question["text"],
            "options": [{"value": option["value"], "text": option["text"]} for option in question["options"]],
        }

    def _coerce_answer(self, value):
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                pass
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not self.answer_min <= value <= self.answer_max
        ):
            raise JsonRpcError(
                -32602, f"answer 必须是 {self.answer_min}~{self.answer_max} 的整数"
            )
        return value

    @staticmethod
    def _validate_question_answer(question, answer):
        valid_values = {option["value"] for option in question.get("options", ())}
        if answer not in valid_values:
            allowed = "、".join(str(value) for value in sorted(valid_values))
            raise JsonRpcError(-32602, f"本题 answer 只能为 {allowed}")

    @staticmethod
    def _require_player_id(arguments, field):
        player_id = arguments.get(field)
        if not isinstance(player_id, str) or PLAYER_ID_RE.fullmatch(player_id) is None:
            raise JsonRpcError(-32602, f"{field} 格式不合法。")
        return player_id

    def _resolve_compare_player_id(self, arguments, field):
        """Resolve compare input to the key actually used by test_results."""
        player_id = arguments.get(field)
        error = (
            f"{field} 格式不合法。可输入对方用户名或账号 id；"
            "游客请输入 guest: 开头的 id。"
        )
        if not isinstance(player_id, str):
            raise JsonRpcError(-32602, error)
        player_id = player_id.strip()
        if re.fullmatch(r"guest:[a-zA-Z0-9]{1,64}", player_id):
            return player_id
        if re.fullmatch(r"[0-9]+(?::[1-5])?", player_id):
            return player_id
        if ACCOUNT_USERNAME_RE.fullmatch(player_id) is None or self._account_db_path is None:
            raise JsonRpcError(-32602, error)
        try:
            with sqlite3.connect(self._account_db_path()) as conn:
                row = conn.execute(
                    "SELECT id FROM toy_users WHERE username = ? AND deleted_at IS NULL",
                    (player_id,),
                ).fetchone()
        except sqlite3.Error:
            row = None
        if row is None:
            raise JsonRpcError(
                -32003,
                "未找到该账号用户名。可输入对方用户名或账号 id；"
                "游客请输入 guest: 开头的 id。",
            )
        return str(int(row[0]))

    def _compare_display_name(self, player_id):
        """Return a friendly report label while keeping player_id as the archive key."""
        if player_id.startswith("guest:") or self._account_db_path is None:
            return player_id
        account_id = player_id.split(":", 1)[0]
        if not account_id.isdigit():
            return player_id
        try:
            with sqlite3.connect(self._account_db_path()) as conn:
                row = conn.execute(
                    "SELECT username FROM toy_users WHERE id = ?",
                    (int(account_id),),
                ).fetchone()
        except sqlite3.Error:
            row = None
        if row is None or not isinstance(row[0], str) or not row[0].strip():
            return player_id
        return row[0]

    def _raise_no_active_session(self, conn, player_id):
        row = conn.execute(
            """
            SELECT result_value, result_detail, completed_at FROM test_results
            WHERE player_id = ? AND game = ?
            """,
            (player_id, self.game),
        ).fetchone()
        if row is not None:
            detail = json.loads(row[1] or "{}")
            raise JsonRpcError(
                -32002,
                f"该 ID 的{self.title}已于 {self._time_label(row[2])} 完成"
                f"（结果 {row[0]}，模式 {detail.get('mode', 'unknown')}）。"
                f"请用 {self.game}_get_result 查看详情，或用 {self.game}_start 重新测试。",
            )
        raise JsonRpcError(-32001, f"没有进行中的{self.title}，请先 {self.game}_start。")

    def _connect(self):
        return sqlite3.connect(self._db_path(), isolation_level=None)

    @staticmethod
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

    @staticmethod
    def _cleanup_expired(conn, now):
        conn.execute(
            "DELETE FROM test_sessions WHERE last_active < ?", (now - SESSION_TTL_SECONDS,)
        )
        conn.execute(
            "DELETE FROM test_results WHERE completed_at < ? AND player_id LIKE 'guest:%'",
            (now - RESULT_TTL_SECONDS,),
        )

    @staticmethod
    def _time_label(timestamp):
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M"
        )

    def _build_tools(self):
        player = {"type": "string", "description": "平台 player_id。"}
        compare_player = {
            "type": "string",
            "description": "账号用户名或数字账号 id；游客使用 guest: 前缀 id。",
        }
        answer = {
            "type": "integer",
            "minimum": self.answer_min,
            "maximum": self.answer_max,
        }
        total = len(self.questions.get_questions("full"))
        tools = [
            {
                "name": f"{self.game}_start",
                "description": f"开始或重置一次{self.title}。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "player_id": player,
                        "mode": {"type": "string", "enum": list(self.questions.VALID_MODES)},
                    },
                    "required": ["player_id", "mode"],
                    "additionalProperties": False,
                },
            },
            {
                "name": f"{self.game}_answer",
                "description": "逐题提交当前题答案。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"player_id": player, "answer": answer},
                    "required": ["player_id", "answer"],
                    "additionalProperties": False,
                },
            },
            {
                "name": f"{self.game}_answer_batch",
                "description": f"快速模式一次提交全部 {total} 题答案。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "player_id": player,
                        "answers": {
                            "type": "array",
                            "items": answer,
                            "minItems": total,
                            "maxItems": total,
                        },
                    },
                    "required": ["player_id", "answers"],
                    "additionalProperties": False,
                },
            },
            {
                "name": f"{self.game}_get_result",
                "description": f"查询最近一次已完成的{self.title}结果。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"player_id": player},
                    "required": ["player_id"],
                    "additionalProperties": False,
                },
            },
        ]
        if self.supports_compare:
            tools.append({
                "name": f"{self.game}_compare",
                "description": "读取两份已完成结果并生成双人对测报告；不设授权限制，游客同权。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "player_id_a": compare_player,
                        "player_id_b": compare_player,
                    },
                    "required": ["player_id_a", "player_id_b"],
                    "additionalProperties": False,
                },
            })
        return tools

    def _tool_error_result(self, request_id, exc):
        prefix = "参数错误" if exc.code == -32602 else ""
        label = f"【{self.title}{prefix}】"
        return self._result(
            request_id,
            {"content": [{"type": "text", "text": f"{label}{exc.message}"}], "isError": True},
        )

    @staticmethod
    def _result(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
