"""CedarToy's identity-bound adapter for the public ARCANUM UI.

The browser keeps ARCANUM's original draw/animation code.  This module
owns only durable sessions, invitation consent, canonical fact validation and
the strict human/AI ownership boundary used by the CedarToy host.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "tarot_sessions.db"
RITUAL_ROOT = ROOT / "vendor" / "tarot-ritual"
RITUAL_PUBLIC = RITUAL_ROOT / "public"
PROMPT_HELPER = ROOT / "scripts" / "tarot_prompt_helper.mjs"
RITUAL_REPOSITORY = "https://github.com/moonlin1213/tarot-ritual"
COVE_REPOSITORY = "https://github.com/moonlin1213/cove-tarot-companion"
RITUAL_DISPLAY_NAME = "ARCANUM · 星轨塔罗圣仪"
RITUAL_COMMIT = "04c6ee2c112e2da9a22bf0b5b4ec80d61ef401d5"
TAROT_FLASH_MODEL = "gemini-3.5-flash"
TAROT_PRO_MODEL = "gemini-3.1-pro-preview"
TAROT_ALLOWED_MODELS = frozenset({TAROT_FLASH_MODEL, TAROT_PRO_MODEL})
TAROT_MODEL_LABELS = {
    TAROT_FLASH_MODEL: "Gemini 3.5 Flash",
    TAROT_PRO_MODEL: "Gemini 3.1 Pro",
}
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
REQUEST_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
EVENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DAY_SECONDS = 86_400
INVITE_TTL_SECONDS = 86_400
MAX_INVITE_QUESTION = 500
MAX_RESULT_TEXT = 24_000
HISTORY_DEFAULT_LIMIT = 10
HISTORY_MAX_LIMIT = 20
HISTORY_QUESTION_SUMMARY = 80


class TarotError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = int(status)
        self.message = str(message)


def require_tarot_model(value: Any) -> str:
    if not isinstance(value, str) or value not in TAROT_ALLOWED_MODELS:
        raise TarotError(400, "只能选择本站提供的 Flash 或 Pro 模型")
    return value


def tarot_model_source(model: str) -> str:
    model = require_tarot_model(model)
    return f"{RITUAL_DISPLAY_NAME} · {TAROT_MODEL_LABELS[model]} 塔罗专用池"


def _require_positive_id(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise TarotError(400, f"invalid {name}") from None
    if result <= 0:
        raise TarotError(400, f"invalid {name}")
    return result


def _require_id(value: Any, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise TarotError(400, f"invalid {name}")
    return value


def _require_invite_question(value: Any) -> str:
    if not isinstance(value, str):
        raise TarotError(400, "invite 必须填写想问的问题")
    question = value.strip()
    if not question:
        raise TarotError(400, "invite 必须填写想问的问题")
    if len(question) > MAX_INVITE_QUESTION:
        raise TarotError(400, f"invite 问题不能超过 {MAX_INVITE_QUESTION} 字")
    return question


def _json_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TarotCatalog:
    """Validate facts and build prompts using ARCANUM's exact modules."""

    def __init__(self, *, node_binary: str | None = None, helper: Path | None = None):
        self.node_binary = node_binary or os.getenv("TAROT_NODE_BINARY", "node")
        self.helper = Path(helper or PROMPT_HELPER)

    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = json.dumps(
            {"action": action, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            completed = subprocess.run(
                [self.node_binary, str(self.helper)],
                input=request,
                text=True,
                capture_output=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TarotError(503, "塔罗牌库暂时不可用") from exc
        if completed.returncode != 0:
            raise TarotError(400, "牌阵或牌面不符合原版牌库")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TarotError(503, "塔罗牌库响应无效") from exc
        if not isinstance(value, dict) or not isinstance(value.get("canonical"), dict):
            raise TarotError(503, "塔罗牌库响应无效")
        return value

    def canonical(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("canonical", payload)["canonical"]

    def messages(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        value = self._call("messages", payload)
        messages = value.get("messages")
        if not isinstance(messages, list) or not messages:
            raise TarotError(503, "原版塔罗提示词构建失败")
        return value["canonical"], messages


class TarotStore:
    """SQLite state store with actor ownership in every lookup predicate."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        catalog: TarotCatalog | None = None,
        public_base_url: str | None = None,
    ):
        self.db_path = Path(db_path)
        self.clock = clock
        self.catalog = catalog or TarotCatalog()
        self.public_base_url = (
            public_base_url
            or os.getenv("CEDARTOY_PUBLIC_BASE_URL", "https://toy.cedarstar.org")
        ).rstrip("/")
        self._condition = threading.Condition()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def _tx(self):
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS tarot_sessions (
                    id TEXT PRIMARY KEY,
                    human_user_id INTEGER NOT NULL,
                    ai_user_id INTEGER,
                    request_id TEXT,
                    phase TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    question TEXT NOT NULL DEFAULT '',
                    spread_id TEXT,
                    draws_json TEXT NOT NULL DEFAULT '[]',
                    canonical_json TEXT NOT NULL DEFAULT '{}',
                    reading_id TEXT,
                    csrf_token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(ai_user_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS tarot_invites (
                    session_id TEXT PRIMARY KEY REFERENCES tarot_sessions(id) ON DELETE CASCADE,
                    human_user_id INTEGER NOT NULL,
                    ai_user_id INTEGER NOT NULL,
                    question TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    accepted_at REAL,
                    rejected_at REAL
                );
                CREATE TABLE IF NOT EXISTS tarot_receipts (
                    session_id TEXT NOT NULL REFERENCES tarot_sessions(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY(session_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS tarot_readings (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES tarot_sessions(id) ON DELETE CASCADE,
                    action_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT 'gemini-3.5-flash',
                    error_code TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(session_id, action_id)
                );
                CREATE TABLE IF NOT EXISTS tarot_session_starts (
                    source_session_id TEXT NOT NULL REFERENCES tarot_sessions(id) ON DELETE CASCADE,
                    action_id TEXT NOT NULL,
                    new_session_id TEXT NOT NULL REFERENCES tarot_sessions(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(source_session_id, action_id),
                    UNIQUE(new_session_id)
                );
                CREATE INDEX IF NOT EXISTS tarot_sessions_human
                    ON tarot_sessions(human_user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS tarot_sessions_ai
                    ON tarot_sessions(ai_user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS tarot_invites_pair
                    ON tarot_invites(ai_user_id, human_user_id, created_at DESC);
                UPDATE tarot_readings
                SET model='gemini-3.5-flash'
                WHERE model IN ('', 'tarot-pool');
                UPDATE tarot_readings
                SET state='unknown', error_code='process_restart'
                WHERE state='running';
                """
            )
            invite_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(tarot_invites)")
            }
            if "question" not in invite_columns:
                conn.execute(
                    "ALTER TABLE tarot_invites "
                    "ADD COLUMN question TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _new_id() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _new_csrf() -> str:
        return secrets.token_urlsafe(32)

    def _now(self) -> float:
        value = float(self.clock())
        if value < 0:
            raise TarotError(500, "invalid clock")
        return value

    @staticmethod
    def _session_row_for_human(
        conn: sqlite3.Connection, session_id: str, human_user_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM tarot_sessions WHERE id=? AND human_user_id=?",
            (session_id, human_user_id),
        ).fetchone()
        if row is None:
            raise TarotError(404, "塔罗会话不存在")
        return row

    @staticmethod
    def _session_row_for_ai(
        conn: sqlite3.Connection, session_id: str, ai_user_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM tarot_sessions WHERE id=? AND ai_user_id=?",
            (session_id, ai_user_id),
        ).fetchone()
        if row is None:
            raise TarotError(404, "塔罗会话不存在")
        return row

    @staticmethod
    def _session_row_for_pair(
        conn: sqlite3.Connection,
        session_id: str,
        ai_user_id: int,
        human_user_id: int,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM tarot_sessions
            WHERE id=? AND ai_user_id=? AND human_user_id=?
            """,
            (session_id, ai_user_id, human_user_id),
        ).fetchone()
        if row is None:
            raise TarotError(404, "塔罗会话不存在")
        return row

    @staticmethod
    def _reading(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
        reading_id = row["reading_id"]
        if not reading_id:
            return None
        reading = conn.execute(
            "SELECT * FROM tarot_readings WHERE id=? AND session_id=?",
            (reading_id, row["id"]),
        ).fetchone()
        if reading is None:
            return None
        return {
            "id": reading["id"],
            "state": reading["state"],
            "text": reading["text"],
            "model": reading["model"],
            "source": tarot_model_source(reading["model"]),
            "error_code": reading["error_code"],
        }

    @classmethod
    def _browser_view(cls, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        draws = json.loads(row["draws_json"] or "[]")
        return {
            "id": row["id"],
            "conversation_id": f"tarot_{row['id']}",
            "revision": int(row["revision"]),
            "phase": row["phase"],
            "question": row["question"],
            "spread_id": row["spread_id"],
            "draws": draws,
            "reading": cls._reading(conn, row),
        }

    def create_direct_session(self, human_user_id: int) -> dict[str, Any]:
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        now = self._now()
        with self._tx() as conn:
            existing = conn.execute(
                """
                SELECT * FROM tarot_sessions
                WHERE human_user_id=? AND ai_user_id IS NULL
                  AND phase NOT IN ('returned','stopped')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (human_user_id,),
            ).fetchone()
            if existing is not None:
                return self._browser_view(conn, existing)
            session_id = self._new_id()
            conn.execute(
                """
                INSERT INTO tarot_sessions(
                    id,human_user_id,ai_user_id,request_id,phase,csrf_token,created_at,updated_at
                ) VALUES(?,?,NULL,NULL,'accepted',?,?,?)
                """,
                (session_id, human_user_id, self._new_csrf(), now, now),
            )
            row = self._session_row_for_human(conn, session_id, human_user_id)
            result = self._browser_view(conn, row)
        self._notify()
        return result

    def create_next_direct_session(
        self,
        source_session_id: str,
        human_user_id: int,
        action_id: str,
        csrf_token: str,
    ) -> dict[str, Any]:
        """Create an independent direct session without mutating the source.

        The source session and action id form an idempotency key so a rapid
        double-click or a retried response cannot create multiple blank records.
        """
        source_session_id = _require_id(
            source_session_id, SESSION_RE, "source_session_id"
        )
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        action_id = _require_id(action_id, EVENT_RE, "action_id")
        now = self._now()
        with self._tx() as conn:
            source = self._verify_human_csrf(
                conn, source_session_id, human_user_id, csrf_token
            )
            old = conn.execute(
                """
                SELECT session.*
                FROM tarot_session_starts AS start
                JOIN tarot_sessions AS session ON session.id=start.new_session_id
                WHERE start.source_session_id=? AND start.action_id=?
                  AND session.human_user_id=?
                """,
                (source_session_id, action_id, human_user_id),
            ).fetchone()
            if old is not None:
                return self._browser_view(conn, old)

            reading = self._reading(conn, source)
            if reading and reading["state"] == "running":
                raise TarotError(
                    409, "解读仍在进行，请等待完成或先明确结束本次"
                )

            session_id = self._new_id()
            conn.execute(
                """
                INSERT INTO tarot_sessions(
                    id,human_user_id,ai_user_id,request_id,phase,csrf_token,created_at,updated_at
                ) VALUES(?,?,NULL,NULL,'accepted',?,?,?)
                """,
                (session_id, human_user_id, self._new_csrf(), now, now),
            )
            conn.execute(
                """
                INSERT INTO tarot_session_starts(
                    source_session_id,action_id,new_session_id,created_at
                ) VALUES(?,?,?,?)
                """,
                (source_session_id, action_id, session_id, now),
            )
            row = self._session_row_for_human(conn, session_id, human_user_id)
            result = self._browser_view(conn, row)
        self._notify()
        return result

    def create_invite(
        self,
        ai_user_id: int,
        human_user_id: int,
        request_id: str,
        question: str,
    ) -> dict[str, Any]:
        ai_user_id = _require_positive_id(ai_user_id, "ai_user_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        request_id = _require_id(request_id, REQUEST_RE, "request_id")
        question = _require_invite_question(question)
        now = self._now()
        with self._tx() as conn:
            old = conn.execute(
                """
                SELECT session.*,invite.question AS invite_question
                FROM tarot_sessions AS session
                LEFT JOIN tarot_invites AS invite ON invite.session_id=session.id
                WHERE session.ai_user_id=? AND session.request_id=?
                """,
                (ai_user_id, request_id),
            ).fetchone()
            if old is not None:
                if int(old["human_user_id"]) != human_user_id:
                    raise TarotError(409, "request_id 已绑定到另一位人类")
                if str(old["invite_question"] or "") != question:
                    raise TarotError(409, "request_id 已绑定到另一个问题")
                return self._ai_status_from_row(conn, old)
            rejection = conn.execute(
                """
                SELECT MAX(rejected_at) AS rejected_at FROM tarot_invites
                WHERE ai_user_id=? AND human_user_id=?
                """,
                (ai_user_id, human_user_id),
            ).fetchone()["rejected_at"]
            if rejection is not None and float(rejection) > now - DAY_SECONDS:
                raise TarotError(
                    429,
                    "人类拒绝后 24 小时内不能再次邀请，请等待冷却结束",
                )
            count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count FROM tarot_invites
                    WHERE ai_user_id=? AND human_user_id=? AND created_at>?
                    """,
                    (ai_user_id, human_user_id, now - DAY_SECONDS),
                ).fetchone()["count"]
            )
            if count >= 3:
                raise TarotError(
                    429,
                    "主动邀请 24 小时内最多 3 次，请等待额度恢复；"
                    "人类想主动占问可直接从 CedarToy 首页进入塔罗",
                )
            session_id = self._new_id()
            conn.execute(
                """
                INSERT INTO tarot_sessions(
                    id,human_user_id,ai_user_id,request_id,phase,question,
                    csrf_token,created_at,updated_at
                ) VALUES(?,?,?,?, 'pending',?,?,?,?)
                """,
                (
                    session_id,
                    human_user_id,
                    ai_user_id,
                    request_id,
                    question,
                    self._new_csrf(),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO tarot_invites(
                    session_id,human_user_id,ai_user_id,question,state,created_at,expires_at
                ) VALUES(?,?,?,?,'pending',?,?)
                """,
                (
                    session_id,
                    human_user_id,
                    ai_user_id,
                    question,
                    now,
                    now + INVITE_TTL_SECONDS,
                ),
            )
            row = self._session_row_for_ai(conn, session_id, ai_user_id)
            result = self._ai_status_from_row(conn, row)
        self._notify()
        return result

    def invitation_for_human(self, session_id: str, human_user_id: int) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT i.*,i.question AS invite_question,s.phase,s.csrf_token,
                       s.question AS session_question
                FROM tarot_invites i
                JOIN tarot_sessions s ON s.id=i.session_id
                WHERE i.session_id=? AND i.human_user_id=?
                """,
                (session_id, human_user_id),
            ).fetchone()
            if row is None:
                raise TarotError(404, "塔罗邀请不存在")
            state = row["state"]
            if state == "pending" and float(row["expires_at"]) <= self._now():
                state = "expired"
            return {
                "session_id": session_id,
                "ai_user_id": int(row["ai_user_id"]),
                "state": state,
                "question": str(row["invite_question"] or ""),
                "expires_at": float(row["expires_at"]),
                "csrf_token": row["csrf_token"],
                "phase": row["phase"],
            }

    def pending_invitations_for_human(
        self, human_user_id: int
    ) -> list[dict[str, Any]]:
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        now = self._now()
        expired = False
        with self._tx() as conn:
            expired_ids = [
                str(row["session_id"])
                for row in conn.execute(
                    """
                    SELECT session_id FROM tarot_invites
                    WHERE human_user_id=? AND state='pending' AND expires_at<=?
                    """,
                    (human_user_id, now),
                )
            ]
            if expired_ids:
                placeholders = ",".join("?" for _ in expired_ids)
                conn.execute(
                    f"UPDATE tarot_invites SET state='expired' "
                    f"WHERE session_id IN ({placeholders}) AND state='pending'",
                    expired_ids,
                )
                conn.execute(
                    f"""
                    UPDATE tarot_sessions
                    SET phase='stopped',revision=revision+1,updated_at=?
                    WHERE id IN ({placeholders}) AND phase='pending'
                    """,
                    (now, *expired_ids),
                )
                expired = True
            rows = conn.execute(
                """
                SELECT invite.session_id,invite.ai_user_id,
                       invite.question,invite.created_at,invite.expires_at,
                       session.csrf_token
                FROM tarot_invites AS invite
                JOIN tarot_sessions AS session ON session.id=invite.session_id
                WHERE invite.human_user_id=? AND invite.state='pending'
                  AND invite.expires_at>?
                ORDER BY invite.created_at,invite.session_id
                LIMIT 20
                """,
                (human_user_id, now),
            ).fetchall()
            result = [
                {
                    "session_id": str(row["session_id"]),
                    "ai_user_id": int(row["ai_user_id"]),
                    "question": str(row["question"] or ""),
                    "created_at": float(row["created_at"]),
                    "expires_at": float(row["expires_at"]),
                    "csrf_token": str(row["csrf_token"]),
                }
                for row in rows
            ]
        if expired:
            self._notify()
        return result

    def wait_pending_invitations_for_human(
        self,
        human_user_id: int,
        *,
        after_cursor: str | None = None,
        wait_seconds: float = 0,
    ) -> dict[str, Any]:
        """Return this human's pending invites, optionally waiting for a change.

        The cursor fingerprints only the already ownership-filtered snapshot.
        Holding the condition while reading prevents a create/respond notify from
        being lost between the snapshot query and ``wait``.
        """
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        if after_cursor is not None and (
            not isinstance(after_cursor, str)
            or not re.fullmatch(r"[0-9a-f]{64}", after_cursor)
        ):
            raise TarotError(400, "invalid invitation cursor")
        try:
            wait_seconds = float(wait_seconds)
        except (TypeError, ValueError):
            raise TarotError(400, "invalid wait_seconds") from None
        if not 0 <= wait_seconds <= 25:
            raise TarotError(400, "wait_seconds 必须为 0–25")

        deadline = time.monotonic() + wait_seconds
        with self._condition:
            while True:
                invitations = self.pending_invitations_for_human(human_user_id)
                cursor = _json_fingerprint(
                    [
                        {
                            "session_id": item["session_id"],
                            "question": item["question"],
                            "expires_at": item["expires_at"],
                        }
                        for item in invitations
                    ]
                )
                result = {"invitations": invitations, "cursor": cursor}
                if after_cursor is None or cursor != after_cursor:
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result["unchanged"] = True
                    return result
                self._condition.wait(timeout=remaining)

    def respond_invite(
        self,
        session_id: str,
        human_user_id: int,
        *,
        accept: bool,
        csrf_token: str,
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        now = self._now()
        expired = False
        with self._tx() as conn:
            session = self._session_row_for_human(conn, session_id, human_user_id)
            if not secrets.compare_digest(str(session["csrf_token"]), str(csrf_token or "")):
                raise TarotError(403, "CSRF 校验失败")
            invite = conn.execute(
                "SELECT * FROM tarot_invites WHERE session_id=? AND human_user_id=?",
                (session_id, human_user_id),
            ).fetchone()
            if invite is None:
                raise TarotError(404, "塔罗邀请不存在")
            if invite["state"] == "accepted":
                if not accept:
                    raise TarotError(409, "该邀请已接受")
                result = self._browser_view(conn, session)
                result["invitation_state"] = "accepted"
                return result
            if invite["state"] == "rejected":
                if accept:
                    raise TarotError(409, "该邀请已拒绝")
                result = self._browser_view(conn, session)
                result["invitation_state"] = "rejected"
                return result
            if invite["state"] == "expired":
                raise TarotError(410, "该邀请已过期")
            if float(invite["expires_at"]) <= now:
                conn.execute(
                    "UPDATE tarot_invites SET state='expired' WHERE session_id=?",
                    (session_id,),
                )
                conn.execute(
                    "UPDATE tarot_sessions SET phase='stopped',revision=revision+1,updated_at=? WHERE id=?",
                    (now, session_id),
                )
                expired = True
            elif accept:
                conn.execute(
                    "UPDATE tarot_invites SET state='accepted',accepted_at=? WHERE session_id=?",
                    (now, session_id),
                )
                conn.execute(
                    "UPDATE tarot_sessions SET phase='accepted',revision=revision+1,updated_at=? WHERE id=?",
                    (now, session_id),
                )
            elif not expired:
                conn.execute(
                    "UPDATE tarot_invites SET state='rejected',rejected_at=? WHERE session_id=?",
                    (now, session_id),
                )
                conn.execute(
                    "UPDATE tarot_sessions SET phase='stopped',revision=revision+1,updated_at=? WHERE id=?",
                    (now, session_id),
                )
            session = self._session_row_for_human(conn, session_id, human_user_id)
            result = self._browser_view(conn, session)
            result["invitation_state"] = "accepted" if accept else "rejected"
        self._notify()
        if expired:
            raise TarotError(410, "该邀请已过期")
        return result

    def bootstrap_for_human(self, session_id: str, human_user_id: int) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        with self._connect() as conn:
            row = self._session_row_for_human(conn, session_id, human_user_id)
            if row["phase"] == "pending":
                raise TarotError(409, "请先接受邀请")
            return {
                "csrf_token": row["csrf_token"],
                "session": self._browser_view(conn, row),
            }

    @staticmethod
    def _saved_history_row_for_human(
        conn: sqlite3.Connection, session_id: str, human_user_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT session.*
            FROM tarot_sessions AS session
            WHERE session.id=? AND session.human_user_id=?
              AND EXISTS (
                SELECT 1 FROM tarot_receipts AS receipt
                WHERE receipt.session_id=session.id AND receipt.kind='draw'
              )
              AND NOT EXISTS (
                SELECT 1 FROM tarot_invites AS invite
                WHERE invite.session_id=session.id AND invite.state<>'accepted'
              )
            """,
            (session_id, human_user_id),
        ).fetchone()
        if row is None:
            raise TarotError(404, "塔罗记录不存在")
        return row

    @staticmethod
    def _canonical_from_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            canonical = json.loads(row["canonical_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            canonical = {}
        return canonical if isinstance(canonical, dict) else {}

    @staticmethod
    def _draws_from_row(row: sqlite3.Row) -> list[dict[str, Any]]:
        try:
            draws = json.loads(row["draws_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            draws = []
        return draws if isinstance(draws, list) else []

    def history_for_human(
        self,
        human_user_id: int,
        *,
        offset: int = 0,
        limit: int = HISTORY_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or offset > 1_000_000
        ):
            raise TarotError(400, "invalid history offset")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > HISTORY_MAX_LIMIT
        ):
            raise TarotError(400, "invalid history limit")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session.*
                FROM tarot_sessions AS session
                WHERE session.human_user_id=?
                  AND EXISTS (
                    SELECT 1 FROM tarot_receipts AS receipt
                    WHERE receipt.session_id=session.id AND receipt.kind='draw'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM tarot_invites AS invite
                    WHERE invite.session_id=session.id AND invite.state<>'accepted'
                  )
                ORDER BY session.updated_at DESC, session.id DESC
                LIMIT ? OFFSET ?
                """,
                (human_user_id, limit + 1, offset),
            ).fetchall()
        has_more = len(rows) > limit
        items = []
        for row in rows[:limit]:
            canonical = self._canonical_from_row(row)
            spread = canonical.get("spread")
            spread_name = (
                str(spread.get("zh") or row["spread_id"] or "未知牌阵")
                if isinstance(spread, dict)
                else str(row["spread_id"] or "未知牌阵")
            )
            question = " ".join(str(row["question"] or "").split())
            items.append(
                {
                    "session_id": row["id"],
                    "updated_at": float(row["updated_at"]),
                    "question_summary": question[:HISTORY_QUESTION_SUMMARY]
                    or "未填写问题",
                    "spread_name": spread_name[:80],
                }
            )
        return {
            "items": items,
            "next_offset": offset + limit if has_more else None,
        }

    def history_detail_for_human(
        self, session_id: str, human_user_id: int
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        with self._connect() as conn:
            row = self._saved_history_row_for_human(
                conn, session_id, human_user_id
            )
            canonical = self._canonical_from_row(row)
            draws = self._draws_from_row(row)
            reading = self._reading(conn, row)
        facts_by_position = {
            int(item["position"]): item
            for item in canonical.get("draws", [])
            if isinstance(item, dict) and isinstance(item.get("position"), int)
        }
        cards = []
        for draw in draws:
            if (
                not isinstance(draw, dict)
                or not isinstance(draw.get("position"), int)
                or draw.get("revealed") is not True
            ):
                continue
            facts = facts_by_position.get(draw["position"], {})
            cards.append(
                {
                    "position": draw["position"],
                    "card_id": str(draw.get("card_id") or "")[:32],
                    "zh": str(facts.get("zh") or "未知牌面")[:80],
                    "en": str(facts.get("en") or "")[:120],
                    "slot": str(facts.get("slot") or "")[:80],
                    "reversed": bool(draw.get("reversed")),
                    "revealed": bool(draw.get("revealed")),
                }
            )
        spread = canonical.get("spread")
        if isinstance(spread, dict):
            spread_view = {
                "id": str(spread.get("id") or row["spread_id"] or "")[:80],
                "zh": str(spread.get("zh") or row["spread_id"] or "未知牌阵")[:80],
                "en": str(spread.get("en") or "")[:120],
            }
        else:
            spread_view = {
                "id": str(row["spread_id"] or "")[:80],
                "zh": str(row["spread_id"] or "未知牌阵")[:80],
                "en": "",
            }
        reading_view = None
        if reading is not None:
            reading_view = {
                "state": reading["state"],
                "text": reading["text"],
                "model": reading["model"],
                "source": reading["source"],
            }
        return {
            "session_id": row["id"],
            "updated_at": float(row["updated_at"]),
            "question": str(row["question"] or ""),
            "spread": spread_view,
            "cards": cards,
            "reading": reading_view,
        }

    def delete_history_session(
        self,
        session_id: str,
        human_user_id: int,
        *,
        csrf_session_id: str,
        csrf_token: str,
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        csrf_session_id = _require_id(
            csrf_session_id, SESSION_RE, "csrf_session_id"
        )
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        now = self._now()
        with self._tx() as conn:
            csrf_session = self._session_row_for_human(
                conn, csrf_session_id, human_user_id
            )
            if not secrets.compare_digest(
                str(csrf_session["csrf_token"]), str(csrf_token or "")
            ):
                raise TarotError(403, "CSRF 校验失败")
            target = self._saved_history_row_for_human(
                conn, session_id, human_user_id
            )
            reading = self._reading(conn, target)
            if reading and reading["state"] == "running":
                conn.execute(
                    "UPDATE tarot_readings SET state='cancelled',updated_at=? "
                    "WHERE id=? AND session_id=? AND state='running'",
                    (now, reading["id"], session_id),
                )
            deleted = conn.execute(
                "DELETE FROM tarot_sessions WHERE id=? AND human_user_id=?",
                (session_id, human_user_id),
            ).rowcount
            if deleted != 1:
                raise TarotError(404, "塔罗记录不存在")
        self._notify()
        return {"deleted": True, "session_id": session_id}

    def _verify_human_csrf(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        human_user_id: int,
        csrf_token: str,
    ) -> sqlite3.Row:
        row = self._session_row_for_human(conn, session_id, human_user_id)
        if not secrets.compare_digest(str(row["csrf_token"]), str(csrf_token or "")):
            raise TarotError(403, "CSRF 校验失败")
        return row

    @staticmethod
    def _receipt(
        conn: sqlite3.Connection,
        session_id: str,
        event_id: str,
        kind: str,
        payload: Any,
    ) -> dict[str, Any] | None:
        old = conn.execute(
            "SELECT * FROM tarot_receipts WHERE session_id=? AND event_id=?",
            (session_id, event_id),
        ).fetchone()
        fingerprint = _json_fingerprint({"kind": kind, "payload": payload})
        if old is None:
            return None
        if old["kind"] != kind or old["fingerprint"] != fingerprint:
            raise TarotError(409, "event_id 重放内容不一致")
        return {
            "session_id": session_id,
            "event_id": event_id,
            "revision": int(old["revision"]),
        }

    @staticmethod
    def _save_receipt(
        conn: sqlite3.Connection,
        session_id: str,
        event_id: str,
        kind: str,
        payload: Any,
        revision: int,
    ) -> dict[str, Any]:
        conn.execute(
            "INSERT INTO tarot_receipts VALUES(?,?,?,?,?)",
            (
                session_id,
                event_id,
                kind,
                _json_fingerprint({"kind": kind, "payload": payload}),
                revision,
            ),
        )
        return {"session_id": session_id, "event_id": event_id, "revision": revision}

    def commit_draw(
        self,
        session_id: str,
        human_user_id: int,
        event: dict[str, Any],
        csrf_token: str,
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        event_id = _require_id(event.get("event_id"), EVENT_RE, "event_id")
        canonical = self.catalog.canonical(event)
        stored_draws = [
            {
                "position": item["position"],
                "card_id": item["card_id"],
                "reversed": item["reversed"],
                "revealed": False,
            }
            for item in canonical["draws"]
        ]
        receipt_payload = {
            "question": canonical["question"],
            "spread_id": canonical["spread_id"],
            "draws": canonical["draws"],
        }
        now = self._now()
        with self._tx() as conn:
            row = self._verify_human_csrf(
                conn, session_id, human_user_id, csrf_token
            )
            old = self._receipt(conn, session_id, event_id, "draw", receipt_payload)
            if old is not None:
                return old
            if row["phase"] != "accepted":
                raise TarotError(409, "该会话不能替换已确认的抽牌")
            revision = int(row["revision"]) + 1
            conn.execute(
                """
                UPDATE tarot_sessions SET
                    phase='drawn',revision=?,question=?,spread_id=?,draws_json=?,
                    canonical_json=?,updated_at=?
                WHERE id=? AND human_user_id=?
                """,
                (
                    revision,
                    canonical["question"],
                    canonical["spread_id"],
                    json.dumps(stored_draws, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(canonical, ensure_ascii=False, separators=(",", ":")),
                    now,
                    session_id,
                    human_user_id,
                ),
            )
            result = self._save_receipt(
                conn, session_id, event_id, "draw", receipt_payload, revision
            )
        self._notify()
        return result

    def reveal(
        self,
        session_id: str,
        human_user_id: int,
        event: dict[str, Any],
        csrf_token: str,
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        event_id = _require_id(event.get("event_id"), EVENT_RE, "event_id")
        positions = event.get("positions")
        if (
            not isinstance(positions, list)
            or not positions
            or len(positions) > 78
            or any(not isinstance(value, int) or value < 0 for value in positions)
            or len(set(positions)) != len(positions)
        ):
            raise TarotError(400, "invalid positions")
        positions = sorted(positions)
        now = self._now()
        with self._tx() as conn:
            row = self._verify_human_csrf(
                conn, session_id, human_user_id, csrf_token
            )
            old = self._receipt(conn, session_id, event_id, "reveal", positions)
            if old is not None:
                return old
            if row["phase"] not in {"drawn", "revealed"}:
                raise TarotError(409, "尚无可揭示的牌面")
            draws = json.loads(row["draws_json"] or "[]")
            if any(position >= len(draws) for position in positions):
                raise TarotError(400, "invalid position")
            for position in positions:
                draws[position]["revealed"] = True
            phase = "revealed" if draws and all(item["revealed"] for item in draws) else "drawn"
            revision = int(row["revision"]) + 1
            conn.execute(
                """
                UPDATE tarot_sessions SET phase=?,revision=?,draws_json=?,updated_at=?
                WHERE id=? AND human_user_id=?
                """,
                (
                    phase,
                    revision,
                    json.dumps(draws, ensure_ascii=False, separators=(",", ":")),
                    now,
                    session_id,
                    human_user_id,
                ),
            )
            result = self._save_receipt(
                conn, session_id, event_id, "reveal", positions, revision
            )
        self._notify()
        return result

    def claim_reading(
        self,
        session_id: str,
        human_user_id: int,
        action_id: str,
        csrf_token: str,
        model: str = TAROT_FLASH_MODEL,
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        action_id = _require_id(action_id, EVENT_RE, "action_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        model = require_tarot_model(model)
        now = self._now()
        with self._tx() as conn:
            row = self._verify_human_csrf(
                conn, session_id, human_user_id, csrf_token
            )
            if row["phase"] != "revealed":
                raise TarotError(409, "全部牌面揭示后才能解读")
            old = conn.execute(
                "SELECT * FROM tarot_readings WHERE session_id=? AND action_id=?",
                (session_id, action_id),
            ).fetchone()
            if old is not None:
                return {"claimed": False, "attempt": dict(old)}
            running = conn.execute(
                "SELECT id FROM tarot_readings WHERE session_id=? AND state='running'",
                (session_id,),
            ).fetchone()
            if running is not None:
                raise TarotError(409, "已有解读正在进行")
            attempt_id = self._new_id()
            conn.execute(
                """
                INSERT INTO tarot_readings(
                    id,session_id,action_id,state,text,model,created_at,updated_at
                ) VALUES(?,?,?,'running','',?,?,?)
                """,
                (attempt_id, session_id, action_id, model, now, now),
            )
            conn.execute(
                """
                UPDATE tarot_sessions SET reading_id=?,revision=revision+1,updated_at=?
                WHERE id=? AND human_user_id=?
                """,
                (attempt_id, now, session_id, human_user_id),
            )
            attempt = conn.execute(
                "SELECT * FROM tarot_readings WHERE id=?", (attempt_id,)
            ).fetchone()
            result = {"claimed": True, "attempt": dict(attempt)}
        self._notify()
        return result

    def reading_for_human(
        self,
        session_id: str,
        human_user_id: int,
        attempt_id: str,
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        attempt_id = _require_id(attempt_id, SESSION_RE, "attempt_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        with self._connect() as conn:
            self._session_row_for_human(conn, session_id, human_user_id)
            row = conn.execute(
                "SELECT * FROM tarot_readings WHERE id=? AND session_id=?",
                (attempt_id, session_id),
            ).fetchone()
            if row is None:
                raise TarotError(404, "解读不存在")
            return dict(row)

    def reading_messages_for_human(
        self, session_id: str, human_user_id: int
    ) -> list[dict[str, str]]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        with self._connect() as conn:
            row = self._session_row_for_human(conn, session_id, human_user_id)
            if row["phase"] != "revealed":
                raise TarotError(409, "全部牌面揭示后才能解读")
            payload = {
                "question": row["question"],
                "spread_id": row["spread_id"],
                "draws": [
                    {
                        "position": item["position"],
                        "card_id": item["card_id"],
                        "reversed": item["reversed"],
                    }
                    for item in json.loads(row["draws_json"] or "[]")
                ],
            }
        _canonical, messages = self.catalog.messages(payload)
        return messages

    def finish_reading(
        self,
        session_id: str,
        human_user_id: int,
        attempt_id: str,
        *,
        state: str,
        text: str = "",
        error_code: str | None = None,
    ) -> dict[str, Any]:
        if state not in {"succeeded", "failed", "unknown", "cancelled"}:
            raise TarotError(400, "invalid reading state")
        if not isinstance(text, str) or len(text) > 100_000 or "\0" in text:
            raise TarotError(400, "invalid reading text")
        now = self._now()
        with self._tx() as conn:
            row = self._session_row_for_human(conn, session_id, human_user_id)
            attempt = conn.execute(
                "SELECT * FROM tarot_readings WHERE id=? AND session_id=?",
                (attempt_id, session_id),
            ).fetchone()
            if attempt is None:
                raise TarotError(404, "解读不存在")
            if row["reading_id"] != attempt_id:
                raise TarotError(409, "解读状态不可更新")
            if attempt["state"] != "running":
                # A concurrent human stop wins.  The late provider response is
                # discarded and the already durable terminal state is replayed.
                return dict(attempt)
            conn.execute(
                """
                UPDATE tarot_readings SET state=?,text=?,error_code=?,updated_at=?
                WHERE id=? AND session_id=?
                """,
                (state, text, error_code, now, attempt_id, session_id),
            )
            conn.execute(
                "UPDATE tarot_sessions SET revision=revision+1,updated_at=? WHERE id=?",
                (now, session_id),
            )
            result = dict(
                conn.execute(
                    "SELECT * FROM tarot_readings WHERE id=?", (attempt_id,)
                ).fetchone()
            )
        self._notify()
        return result

    def return_session(
        self,
        session_id: str,
        human_user_id: int,
        revision: int,
        csrf_token: str,
    ) -> dict[str, Any]:
        if not isinstance(revision, int) or revision < 0:
            raise TarotError(400, "invalid revision")
        now = self._now()
        with self._tx() as conn:
            row = self._verify_human_csrf(
                conn, session_id, human_user_id, csrf_token
            )
            if row["phase"] == "returned":
                return {
                    "event_id": f"return_{session_id}",
                    "session_id": session_id,
                    "revision": int(row["revision"]),
                    "state": "pending",
                }
            if int(row["revision"]) != revision:
                raise TarotError(409, "return revision mismatch")
            reading = self._reading(conn, row)
            if reading and reading["state"] == "running":
                raise TarotError(409, "解读仍在进行")
            if row["phase"] not in {"revealed", "stopped"}:
                raise TarotError(409, "牌面尚未完整揭示")
            conn.execute(
                "UPDATE tarot_sessions SET phase='returned',revision=revision+1,updated_at=? WHERE id=?",
                (now, session_id),
            )
            current = self._session_row_for_human(conn, session_id, human_user_id)
            result = {
                "event_id": f"return_{session_id}",
                "session_id": session_id,
                "revision": int(current["revision"]),
                "state": "pending",
            }
        self._notify()
        return result

    def stop_session(
        self,
        session_id: str,
        human_user_id: int,
        csrf_token: str,
    ) -> dict[str, Any]:
        now = self._now()
        with self._tx() as conn:
            row = self._verify_human_csrf(
                conn, session_id, human_user_id, csrf_token
            )
            if row["phase"] in {"returned", "stopped"}:
                return {"session_id": session_id, "phase": row["phase"]}
            reading = self._reading(conn, row)
            if reading and reading["state"] == "running":
                conn.execute(
                    "UPDATE tarot_readings SET state='cancelled',updated_at=? WHERE id=?",
                    (now, reading["id"]),
                )
            conn.execute(
                "UPDATE tarot_sessions SET phase='stopped',revision=revision+1,updated_at=? WHERE id=?",
                (now, session_id),
            )
        self._notify()
        return {"session_id": session_id, "phase": "stopped"}

    def _ai_status_from_row(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        invite = conn.execute(
            """
            SELECT state,question,expires_at,accepted_at,rejected_at
            FROM tarot_invites WHERE session_id=?
            """,
            (row["id"],),
        ).fetchone()
        invite_state = invite["state"] if invite else None
        if invite and invite_state == "pending" and float(invite["expires_at"]) <= self._now():
            invite_state = "expired"
        reading = self._reading(conn, row)
        phase = invite_state if invite_state in {"rejected", "expired"} else row["phase"]
        result = {
            "session_id": row["id"],
            "phase": phase,
            "revision": int(row["revision"]),
            "question": str(row["question"] or ""),
            "invitation": (
                {
                    "state": invite_state,
                    "question": str(invite["question"] or ""),
                    "expires_at": float(invite["expires_at"]),
                    "accepted_at": (
                        float(invite["accepted_at"])
                        if invite["accepted_at"] is not None
                        else None
                    ),
                    "rejected_at": (
                        float(invite["rejected_at"])
                        if invite["rejected_at"] is not None
                        else None
                    ),
                }
                if invite
                else None
            ),
            "reading_state": reading["state"] if reading else "missing",
            "reading_model": reading["model"] if reading else None,
            "reading_source": reading["source"] if reading else None,
            "invite_url": f"{self.public_base_url}/tarot/",
            "next_call": {
                "game": "tarot",
                "action": "status",
                "params": {"session_id": row["id"], "after_revision": int(row["revision"]), "wait_seconds": 20},
            },
        }
        result["result_ready"] = bool(
            row["phase"] in {"revealed", "returned", "stopped"}
            and reading
            and reading["state"] in {"succeeded", "failed", "unknown", "cancelled"}
        )
        return result

    def ai_status(
        self, session_id: str, ai_user_id: int, human_user_id: int
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        ai_user_id = _require_positive_id(ai_user_id, "ai_user_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        with self._connect() as conn:
            row = self._session_row_for_pair(
                conn, session_id, ai_user_id, human_user_id
            )
            return self._ai_status_from_row(conn, row)

    def wait_ai_status(
        self,
        session_id: str,
        ai_user_id: int,
        human_user_id: int,
        *,
        after_revision: int | None = None,
        wait_seconds: float = 0,
    ) -> dict[str, Any]:
        if after_revision is not None and (
            not isinstance(after_revision, int) or after_revision < 0
        ):
            raise TarotError(400, "invalid after_revision")
        try:
            wait_seconds = float(wait_seconds)
        except (TypeError, ValueError):
            raise TarotError(400, "invalid wait_seconds") from None
        if wait_seconds < 0 or wait_seconds > 25:
            raise TarotError(400, "wait_seconds 必须为 0–25")
        deadline = time.monotonic() + wait_seconds
        while True:
            result = self.ai_status(session_id, ai_user_id, human_user_id)
            if after_revision is None or result["revision"] > after_revision:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result["unchanged"] = True
                return result
            with self._condition:
                self._condition.wait(timeout=min(remaining, 0.5))

    def ai_result(
        self, session_id: str, ai_user_id: int, human_user_id: int
    ) -> dict[str, Any]:
        session_id = _require_id(session_id, SESSION_RE, "session_id")
        ai_user_id = _require_positive_id(ai_user_id, "ai_user_id")
        human_user_id = _require_positive_id(human_user_id, "human_user_id")
        with self._connect() as conn:
            row = self._session_row_for_pair(
                conn, session_id, ai_user_id, human_user_id
            )
            draws = json.loads(row["draws_json"] or "[]")
            if not draws or not all(item.get("revealed") is True for item in draws):
                raise TarotError(409, "人类尚未完成并揭示本次抽牌")
            canonical = json.loads(row["canonical_json"] or "{}")
            reading = self._reading(conn, row)
            if reading is None or reading["state"] == "running":
                raise TarotError(409, "本次专业解读尚未完成")
            text = reading["text"] if reading else ""
            truncated = len(text) > MAX_RESULT_TEXT
            if truncated:
                text = text[:MAX_RESULT_TEXT]
            cards = []
            facts_by_position = {
                int(item["position"]): item for item in canonical.get("draws", [])
            }
            for draw in draws:
                facts = facts_by_position.get(int(draw["position"]), {})
                cards.append(
                    {
                        "position": int(draw["position"]),
                        "card_id": draw["card_id"],
                        "zh": facts.get("zh"),
                        "en": facts.get("en"),
                        "slot": facts.get("slot"),
                        "reversed": bool(draw["reversed"]),
                    }
                )
            return {
                "protocol": "cedartoy-tarot-v1",
                "type": "tarot_result",
                "untrusted": True,
                "session_id": session_id,
                "revision": int(row["revision"]),
                "phase": row["phase"],
                "question": row["question"],
                "spread": canonical.get("spread"),
                "cards": cards,
                "reading": {
                    "id": reading["id"] if reading else None,
                    "state": reading["state"] if reading else "missing",
                    "model": reading["model"] if reading else None,
                    "error_code": reading["error_code"] if reading else None,
                    "text": text,
                    "truncated": truncated,
                    "source": reading["source"] if reading else None,
                },
                "safety": "结果仅供娱乐与自我反思，不替代医疗、法律或财务专业意见。",
            }

    def delete_user_data(self, user_id: int) -> int:
        user_id = _require_positive_id(user_id, "user_id")
        with self._tx() as conn:
            count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM tarot_sessions WHERE human_user_id=? OR ai_user_id=?",
                    (user_id, user_id),
                ).fetchone()["count"]
            )
            conn.execute(
                "DELETE FROM tarot_sessions WHERE human_user_id=? OR ai_user_id=?",
                (user_id, user_id),
            )
        if count:
            self._notify()
        return count


class TarotWeb:
    def __init__(self, public_root: Path | None = None):
        self.public_root = Path(public_root or RITUAL_PUBLIC).resolve()
        self.index_path = self.public_root / "index.html"

    def static_file(self, relative_path: str) -> tuple[Path, str]:
        relative_path = relative_path.lstrip("/")
        platform_assets = {
            "platform/managed-core.v1.js": ROOT / "assets" / "tarot" / "managed-core.v1.js",
            "platform/managed-core.v5.js": ROOT / "assets" / "tarot" / "managed-core.v5.js",
            "platform/managed-core.v6.js": ROOT / "assets" / "tarot" / "managed-core.v6.js",
            "platform/managed-ui.v1.js": ROOT / "assets" / "tarot" / "managed-ui.v1.js",
            "platform/managed-ui.v1.css": ROOT / "assets" / "tarot" / "managed-ui.v1.css",
            "platform/managed-ui.v2.js": ROOT / "assets" / "tarot" / "managed-ui.v2.js",
            "platform/managed-ui.v2.css": ROOT / "assets" / "tarot" / "managed-ui.v2.css",
            "platform/managed-ui.v3.js": ROOT / "assets" / "tarot" / "managed-ui.v3.js",
            "platform/managed-ui.v3.css": ROOT / "assets" / "tarot" / "managed-ui.v3.css",
            "platform/managed-ui.v4.js": ROOT / "assets" / "tarot" / "managed-ui.v4.js",
            "platform/managed-ui.v4.css": ROOT / "assets" / "tarot" / "managed-ui.v4.css",
            "platform/managed-ui.v5.js": ROOT / "assets" / "tarot" / "managed-ui.v5.js",
            "platform/managed-ui.v5.css": ROOT / "assets" / "tarot" / "managed-ui.v5.css",
            "platform/managed-ui.v6.js": ROOT / "assets" / "tarot" / "managed-ui.v6.js",
            "platform/managed-ui.v6.css": ROOT / "assets" / "tarot" / "managed-ui.v6.css",
            "platform/managed-ui.v7.js": ROOT / "assets" / "tarot" / "managed-ui.v7.js",
            "platform/managed-ui.v7.css": ROOT / "assets" / "tarot" / "managed-ui.v7.css",
            "platform/managed-companion.v3.js": ROOT / "assets" / "tarot" / "managed-companion.v3.js",
            "platform/managed-cards3d.v5.js": ROOT / "assets" / "tarot" / "managed-cards3d.v5.js",
            "platform/managed-cards3d.v6.js": ROOT / "assets" / "tarot" / "managed-cards3d.v6.js",
            "js/three/managed-cards3d-core.v6.js": ROOT / "assets" / "tarot" / "managed-cards3d-core.v6.js",
            "js/three/canvas-navigation.v6.js": ROOT / "assets" / "tarot" / "managed-canvas-navigation.v6.js",
            "platform/upstream-companion-adapter.v1.js": RITUAL_PUBLIC / "js" / "companion-adapter.js",
            "js/three/upstream-cards3d.v1.js": RITUAL_PUBLIC / "js" / "three" / "cards3d.js",
        }
        if relative_path in platform_assets:
            candidate = platform_assets[relative_path].resolve()
            if not candidate.is_file():
                raise TarotError(404, "not found")
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            return candidate, mime
        candidate = (self.public_root / relative_path).resolve()
        if (
            self.public_root not in candidate.parents
            or not candidate.is_file()
            or candidate.suffix.lower() in {".html", ".htm"}
        ):
            raise TarotError(404, "not found")
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        return candidate, mime

    @staticmethod
    def auth_bridge(return_path: str) -> bytes:
        return_path_json = json.dumps(return_path, ensure_ascii=False).replace("<", "\\u003c")
        body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>进入 {RITUAL_DISPLAY_NAME}</title></head>
<body><main><h1>{RITUAL_DISPLAY_NAME}</h1><p id="state">正在确认登录身份……</p><p><a href="/">返回首页</a></p></main>
<script>(()=>{{const state=document.getElementById('state');const token=localStorage.getItem('cedartoy_token');if(!token){{state.textContent='请先返回首页登录，再进入圣仪。';return;}}fetch('/api/tarot/browser-login',{{method:'POST',headers:{{Authorization:'Bearer '+token}}}}).then(async r=>{{const j=await r.json().catch(()=>({{}}));if(!r.ok)throw new Error(j.error||'登录失效');location.replace({return_path_json});}}).catch(e=>state.textContent=e.message+'，请返回首页重新登录。');}})();</script></body></html>"""
        return body.encode("utf-8")
    def ritual_index(
        self, session_id: str, human_user_id: int | None = None
    ) -> bytes:
        source = self.index_path.read_text(encoding="utf-8")
        config_payload = {
            "protocol": "cove-tarot-companion-v1",
            "sessionId": session_id,
            "apiBase": "/companion/v1",
        }
        if human_user_id is not None:
            config_payload["humanUserId"] = _require_positive_id(
                human_user_id, "human_user_id"
            )
        config = json.dumps(
            config_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("<", "\\u003c")
        source = source.replace("<head>", '<head>\n<base href="/tarot/static/">', 1)
        import_map = '<script type="importmap">{ "imports": { "three": "./vendor/three.module.js" } }</script>'
        managed_import_map = (
            '<script type="importmap">{ "imports": {'
            ' "three": "./vendor/three.module.js",'
            ' "/tarot/static/js/core.js": "/tarot/static/platform/managed-core.v6.js",'
            ' "/tarot/static/js/companion-adapter.js": "/tarot/static/platform/managed-companion.v3.js",'
            ' "/tarot/static/js/three/cards3d.js": "/tarot/static/platform/managed-cards3d.v6.js"'
            ' } }</script>'
        )
        if source.count(import_map) != 1:
            raise TarotError(500, "塔罗原版模块结构已变化")
        source = source.replace(import_map, managed_import_map, 1)
        style_marker = '<link rel="stylesheet" href="./css/style.css">'
        source = source.replace(
            style_marker,
            style_marker
            + '\n<link rel="stylesheet" href="/tarot/static/platform/managed-ui.v3.css">'
            + '\n<link rel="stylesheet" href="/tarot/static/platform/managed-ui.v4.css">'
            + '\n<link rel="stylesheet" href="/tarot/static/platform/managed-ui.v5.css">'
            + '\n<link rel="stylesheet" href="/tarot/static/platform/managed-ui.v6.css">'
            + '\n<link rel="stylesheet" href="/tarot/static/platform/managed-ui.v7.css">',
            1,
        )
        upstream_settings = """    <div class="settings-body">
    <div id="dshBanner" class="dsh-banner hidden"></div>
    <div class="dsh-import-actions">
      <button id="dshImportBtn" class="btn ghost small" disabled>导入本机 DSH</button>
        <p id="dshConsentNote" class="settings-note">正在读取本机 DSH 导入与续期设置……</p>
    </div>
    <div id="providerList" class="provider-list"></div>
    <div class="settings-section">
      <div class="section-title">亲自延请一位神谕</div>
      <div class="form-grid">
        <input id="cpName" placeholder="名号（如 My Gateway）">
        <select id="cpKind">
          <option value="openai">OpenAI 兼容 · chat/completions</option>
          <option value="responses">OpenAI Responses</option>
          <option value="anthropic">Anthropic Messages</option>
        </select>
        <input id="cpBase" placeholder="Base URL（如 https://api.example.com/v1）">
        <input id="cpKey" placeholder="API Key" type="password">
        <button id="cpAdd" class="btn ghost small">载 入 议 会</button>
      </div>
      <p class="settings-note">自定义密钥仅保留在本页内存，刷新后需重新添加。问题、牌阵和实拍照片会发送到所选 AI 服务；密钥用于该服务认证。DSH 导入默认只读，若本机启用 Codex 自动续期，上方会明确说明。请勿上传不愿分享的个人信息。</p>
    </div>
    </div>"""
        managed_settings = f"""    <div class="settings-body managed-settings">
      <p class="managed-model-note">本站暂仅支持所提供的模型。如需自行配置模型，请克隆<a href="{COVE_REPOSITORY}" target="_blank" rel="noopener noreferrer">原版</a>。</p>
      <div id="providerList" class="provider-list" aria-label="本站塔罗模型"></div>
      <div class="managed-compat" hidden aria-hidden="true">
        <div id="dshBanner"></div><button id="dshImportBtn" disabled></button>
        <p id="dshConsentNote"></p><input id="cpName"><select id="cpKind"><option value="openai"></option></select>
        <input id="cpBase"><input id="cpKey" type="password"><button id="cpAdd" disabled></button>
      </div>
      <p class="managed-model-note managed-credit">原作：林默Moon · <a href="{COVE_REPOSITORY}" target="_blank" rel="noopener noreferrer">项目来源</a></p>
    </div>"""
        if source.count(upstream_settings) != 1:
            raise TarotError(500, "塔罗原版模型面板结构已变化")
        source = source.replace(upstream_settings, managed_settings, 1)
        managed = f"""
<script type="application/json" id="companion-config">{config}</script>
<script src="/tarot/static/platform/managed-ui.v3.js"></script>
<script src="/tarot/static/platform/managed-ui.v4.js"></script>
<script src="/tarot/static/platform/managed-ui.v5.js"></script>
<script src="/tarot/static/platform/managed-ui.v7.js"></script>
"""
        source = source.replace(
            '<script type="module" src="./js/main.js"></script>',
            managed + '\n<script type="module" src="./js/main.js"></script>',
            1,
        )
        return source.encode("utf-8")

    @staticmethod
    def homepage_index(source: str) -> bytes:
        """Inject the reviewed 4399 entry only after the server is restarted.

        ``index.html`` is read live by the currently running process.  Keeping
        the checked-in file unchanged prevents a half-deployed card from
        pointing at routes that are not loaded yet.
        """
        replacements = (
            (
                '"garden_cat","duel","camping_plaza"',
                '"garden_cat","duel","tarot","camping_plaza"',
            ),
            (
                '"soup", "eco", "forest", "duel", "workkk"',
                '"soup", "eco", "forest", "duel", "tarot", "workkk"',
            ),
        )
        for old, new in replacements:
            if source.count(old) != 1:
                raise TarotError(500, "CedarToy 首页结构已变化，未安全加入塔罗入口")
            source = source.replace(old, new, 1)

        card_marker = """      {
        id: "fishing",
        category: "mini","""
        card = f"""      {{
        id: "tarot",
        iconFile: "tarot.svg",
        category: "mini",
        watch: true,
        name: "{RITUAL_DISPLAY_NAME}",
        mission: "MISSION: ARCANUM",
        location: "LOCATION: TAROT RITUAL",
        glyph: "✦",
        badge: "TAROT",
        level: "78 CARDS",
        metricLabel: "存档数",
        metric: "--",
        short: "3D 塔罗 / 人机陪伴",
        desc: "保留 {RITUAL_DISPLAY_NAME} 原版 3D 抽牌与翻牌动画；小机可带问题发出邀请，人类确认后亲自选阵、抽牌。",
        logs: [
          "一句话：人类亲手在 {RITUAL_DISPLAY_NAME} 完成占问，小机在会话另一端等你带回牌语。",
          "玩法：小机填写问题并邀请；绑定人类在本站同意后进入原版界面，亲自选牌阵、抽牌与揭牌。",
          "来源：游戏采用 Tarot Ritual，人机联动规则参考 Cove Tarot Companion。",
          "作者：林默Moon",
          "小红书号：427689021"
        ],
        stats: [["CARDS", "78"], ["SPREADS", "5"]],
        footerIcons: ["✦", "☾", "◇"],
        tags: ["塔罗", "3D", "MCP"],
        url: "{COVE_REPOSITORY}",
        watchLabel: "开始占问 →",
        ctaLabel: "GitHub →",
        ranks: []
      }},
{card_marker}"""
        if source.count(card_marker) != 1:
            raise TarotError(500, "CedarToy 首页游戏列表已变化，未安全加入塔罗入口")
        source = source.replace(card_marker, card, 1)

        watch_marker = """    function watchCurrentGame(playerId, slot = 1) {
      const game = currentGame();
      if (game.id === "workkk") {"""
        watch_branch = """    function watchCurrentGame(playerId, slot = 1) {
      const game = currentGame();
      if (game.id === "tarot") {
        window.location.href = "/tarot/";
        return;
      }
      if (game.id === "workkk") {"""
        if source.count(watch_marker) != 1:
            raise TarotError(500, "CedarToy 围观入口已变化，未安全加入塔罗入口")
        source = source.replace(watch_marker, watch_branch, 1)

        hint_old = """          : (currentGame().id === "duel" ? "请先登录再进入双弈" : "请先登录再围观大屏")));"""
        hint_new = """          : (currentGame().id === "duel" ? "请先登录再进入双弈"
          : (currentGame().id === "tarot" ? "请先登录再开始占问" : "请先登录再围观大屏"))));"""
        if source.count(hint_old) != 1:
            raise TarotError(500, "CedarToy 登录提示已变化，未安全加入塔罗入口")
        source = source.replace(hint_old, hint_new, 1)

        enter_marker = """      if (currentGame().id === "garden_cat") {
        await openGardenCatPicker();"""
        enter_branch = """      if (currentGame().id === "tarot") {
        window.location.href = "/tarot/";
        return;
      }
      if (currentGame().id === "garden_cat") {
        await openGardenCatPicker();"""
        if source.count(enter_marker) != 1:
            raise TarotError(500, "CedarToy 人类入口已变化，未安全加入塔罗入口")
        source = source.replace(enter_marker, enter_branch, 1)
        return source.encode("utf-8")


_STORE: TarotStore | None = None
_STORE_LOCK = threading.Lock()


def count_saved_tarot_sessions(db_path: str | Path | None = None) -> int:
    """Count sessions with a durably committed draw, without creating a DB."""
    path = Path(
        db_path
        if db_path is not None
        else os.getenv("TAROT_DB_PATH", str(DEFAULT_DB_PATH))
    ).expanduser()
    if not path.is_file():
        return 0
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM tarot_sessions AS session
                WHERE EXISTS (
                    SELECT 1
                    FROM tarot_receipts AS receipt
                    WHERE receipt.session_id = session.id
                      AND receipt.kind = 'draw'
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM tarot_invites AS invite
                    WHERE invite.session_id = session.id
                      AND invite.state <> 'accepted'
                  )
                """
            ).fetchone()
        return int(row[0] or 0) if row else 0
    except (OSError, sqlite3.Error, ValueError):
        return 0


def get_store() -> TarotStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = TarotStore(os.getenv("TAROT_DB_PATH", str(DEFAULT_DB_PATH)))
    return _STORE


WEB = TarotWeb()
