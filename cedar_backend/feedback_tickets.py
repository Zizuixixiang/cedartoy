"""Private append-only feedback conversations, separate from password recovery."""
import hashlib
import logging
import re
import secrets
import time
from contextlib import closing

CATEGORIES = ("bug", "suggestion", "account", "other")
STATUSES = ("pending", "processing", "closed")
PUBLIC_FIELDS = "id, category, contact, status, created_at_epoch, updated_at_epoch"
ADMIN_FIELDS = PUBLIC_FIELDS + ", owner_id, internal_note, reviewed_by, contact_email"
PAGE_SIZE = 20
MESSAGE_PAGE_SIZE = 10
_TICKET_SCHEMA = """CREATE TABLE IF NOT EXISTS {name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER,
    query_code_hash TEXT UNIQUE, ip_hash TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('bug','suggestion','account','other')),
    content TEXT NOT NULL, contact TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','closed')),
    internal_note TEXT NOT NULL DEFAULT '', reviewed_by INTEGER,
    created_at_epoch INTEGER NOT NULL, updated_at_epoch INTEGER
)"""


def init_schema(conn):
    """Atomic v1 upgrade: preserve IDs/capabilities/notes and seed immutable QA once.

    A savepoint also works inside the platform's existing startup transaction.
    Never commit that caller's transaction, nor touch the recovery tables.
    """
    conn.execute("SAVEPOINT feedback_schema")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(feedback_tickets)")}
        legacy = "reply" in columns
        if legacy:
            sequence_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
            sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='feedback_tickets'").fetchone() if sequence_table else None
            old_sequence = sequence[0] if sequence else 0
            conn.execute(_TICKET_SCHEMA.format(name="feedback_tickets_qa_upgrade"))
            conn.execute("""INSERT INTO feedback_tickets_qa_upgrade
                SELECT id, owner_id, query_code_hash, ip_hash, category, content, contact,
                    CASE status WHEN 'replied' THEN 'processing' ELSE status END,
                    internal_note, reviewed_by, created_at_epoch, updated_at_epoch
                FROM feedback_tickets""")
            conn.execute("ALTER TABLE feedback_tickets RENAME TO feedback_tickets_legacy_upgrade")
            conn.execute("ALTER TABLE feedback_tickets_qa_upgrade RENAME TO feedback_tickets")
        else:
            conn.execute(_TICKET_SCHEMA.format(name="feedback_tickets"))
        conn.execute("""CREATE TABLE IF NOT EXISTS feedback_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL REFERENCES feedback_tickets(id),
            role TEXT NOT NULL CHECK(role IN ('player','admin')),
            actor_id INTEGER, content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 4000),
            created_at_epoch INTEGER NOT NULL, ip_hash TEXT
        )""")
        if legacy:
            conn.execute("""INSERT INTO feedback_messages(ticket_id,role,actor_id,content,created_at_epoch,ip_hash)
                SELECT id,'player',owner_id,content,created_at_epoch,ip_hash
                FROM feedback_tickets_legacy_upgrade ORDER BY id""")
            conn.execute("""INSERT INTO feedback_messages(ticket_id,role,actor_id,content,created_at_epoch)
                SELECT id,'admin',reviewed_by,reply,coalesce(updated_at_epoch,created_at_epoch)
                FROM feedback_tickets_legacy_upgrade WHERE length(trim(reply)) > 0 ORDER BY id""")
            conn.execute("DROP TABLE feedback_tickets_legacy_upgrade")
            if conn.execute("SELECT 1 FROM sqlite_sequence WHERE name='feedback_tickets'").fetchone():
                conn.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='feedback_tickets'", (old_sequence,))
            else:
                conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('feedback_tickets',?)", (old_sequence,))
        # Adding an optional contact address leaves existing tickets and query codes unchanged.
        ticket_columns = {row[1] for row in conn.execute("PRAGMA table_info(feedback_tickets)")}
        if "contact_email" not in ticket_columns:
            conn.execute("ALTER TABLE feedback_tickets ADD COLUMN contact_email TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS feedback_email_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL REFERENCES feedback_tickets(id),
            admin_message_id INTEGER REFERENCES feedback_messages(id),
            kind TEXT NOT NULL CHECK(kind IN ('reply','closed')),
            status TEXT NOT NULL CHECK(status IN ('sending','sent','failed')),
            error TEXT NOT NULL DEFAULT '',
            created_at_epoch INTEGER NOT NULL, finished_at_epoch INTEGER
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS feedback_email_ticket_idx ON feedback_email_events(ticket_id,id)")
        for name, columns in (("owner", "owner_id, id"), ("status", "status, id"),
                              ("ip_time", "ip_hash, created_at_epoch")):
            conn.execute(f"CREATE INDEX IF NOT EXISTS feedback_{name}_idx ON feedback_tickets({columns})")
        for name, columns in (("ticket", "ticket_id, id"), ("actor_time", "role, actor_id, created_at_epoch"),
                              ("ip_time", "role, ip_hash, created_at_epoch")):
            conn.execute(f"CREATE INDEX IF NOT EXISTS feedback_messages_{name}_idx ON feedback_messages({columns})")
        # Includes note-only edits and closing through MCP; no unbounded in-memory key map.
        conn.execute("""CREATE TABLE IF NOT EXISTS feedback_review_limits (
            admin_id INTEGER NOT NULL, created_at_epoch INTEGER NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS feedback_review_limits_idx ON feedback_review_limits(admin_id,created_at_epoch)")
        # One durable rollout boundary, including any v1 replies migrated above.
        # Never rebaseline on restart or first visit by a particular account.
        conn.execute("""CREATE TABLE IF NOT EXISTS feedback_unread_baseline (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), message_id INTEGER NOT NULL)""")
        conn.execute("""INSERT OR IGNORE INTO feedback_unread_baseline
            SELECT 1, coalesce(max(id),0) FROM feedback_messages""")
        conn.execute("""CREATE TABLE IF NOT EXISTS feedback_read_cursors (
            ticket_id INTEGER NOT NULL REFERENCES feedback_tickets(id), owner_id INTEGER NOT NULL,
            read_id INTEGER NOT NULL DEFAULT 0, notified_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(ticket_id, owner_id))""")
        conn.execute("RELEASE feedback_schema")
    except Exception:
        conn.execute("ROLLBACK TO feedback_schema")
        conn.execute("RELEASE feedback_schema")
        raise


class FeedbackStore:
    def __init__(self, connect, error, ip_hash, *, normalize_email=None, send_email=None):
        self.connect, self.error, self.ip_hash = connect, error, ip_hash
        self.normalize_email, self.send_email = normalize_email, send_email

    def _open(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            init_schema(conn)
            conn.commit()
        except Exception:
            conn.close()
            raise
        return conn

    def integer(self, value, name, maximum=2**63 - 1):
        if type(value) is not int or not 1 <= value <= maximum:
            raise self.error(-32602, f"{name} 必须是 1–{maximum} 的整数")
        return value

    def text(self, body, key, limit, required=False):
        value = body.get(key, "")
        if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
            raise self.error(-32602, f"{key} {'必填，' if required else ''}最多 {limit} 字")
        return value.strip()

    def choice(self, value, choices, name):
        if not isinstance(value, str) or value not in choices:
            raise self.error(-32602, f"{name} 无效")
        return value

    def _rate(self, conn, table, clause, values, hourly, daily, now):
        # Table/column identifiers are exclusively internal constants.
        row = conn.execute(f"SELECT count(*), coalesce(sum(created_at_epoch >= ?),0) FROM {table} "
                           f"WHERE {clause} AND created_at_epoch >= ?",
                           [now-3600, *values, now-86400]).fetchone()
        if row[0] >= daily or row[1] >= hourly:
            raise self.error(-32029, "提交较多，请稍后再试")

    def _message(self, conn, ticket_id, role, actor, content, now, ip_hash=None):
        cursor = conn.execute("""INSERT INTO feedback_messages
            (ticket_id,role,actor_id,content,created_at_epoch,ip_hash) VALUES (?,?,?,?,?,?)""",
            (ticket_id, role, actor, content, now, ip_hash))
        return cursor.lastrowid

    def _unread_rows(self, conn, owner_id):
        # owner_id always comes from authentication, never a binding or request field.
        return conn.execute("""SELECT t.id, max(m.id) AS latest_id,
                coalesce(c.notified_id,0) AS notified_id
            FROM feedback_tickets t JOIN feedback_messages m ON m.ticket_id=t.id AND m.role='admin'
            LEFT JOIN feedback_read_cursors c ON c.ticket_id=t.id AND c.owner_id=t.owner_id
            WHERE t.owner_id=? AND m.id > max(coalesce(c.read_id,0),
                (SELECT message_id FROM feedback_unread_baseline WHERE singleton=1))
            GROUP BY t.id""", (owner_id,)).fetchall()

    def unread(self, owner_id):
        if owner_id is None:
            raise self.error(-32001, "请先登录后查看自己的反馈")
        with closing(self._open()) as conn:
            return {"unread_count": len(self._unread_rows(conn, owner_id))}

    def claim_notification(self, owner_id):
        if owner_id is None:
            return False
        # Startup already migrates the schema. The common MCP path must not
        # acquire _open()'s write lock, even for already-notified unread replies.
        with closing(self.connect()) as conn, conn:
            if not any(r["latest_id"] > r["notified_id"] for r in self._unread_rows(conn, owner_id)):
                return False
            # The SELECT above is fully consumed, with no read transaction held.
            # Recheck after locking: another claim or detail read may have won.
            conn.execute("BEGIN IMMEDIATE")
            rows = [r for r in self._unread_rows(conn, owner_id) if r["latest_id"] > r["notified_id"]]
            for row in rows:
                conn.execute("""INSERT INTO feedback_read_cursors(ticket_id,owner_id,notified_id) VALUES (?,?,?)
                    ON CONFLICT(ticket_id,owner_id) DO UPDATE SET notified_id=max(notified_id,excluded.notified_id)""",
                    (row["id"], owner_id, row["latest_id"]))
            return bool(rows)

    def submit(self, body, owner_id, ip):
        category = self.choice(body.get("category"), CATEGORIES, "category")
        content = self.text(body, "content", 4000, True)
        contact = self.text(body, "contact", 120)
        notify_email = body.get("notify_email", False)
        if type(notify_email) is not bool:
            raise self.error(-32602, "邮箱通知选项无效")
        contact_email = None
        if notify_email:
            if self.normalize_email is None:
                raise self.error(-32602, "邮箱通知暂不可用")
            contact_email = self.normalize_email(body.get("contact_email"))
        now, ip_hash = int(time.time()), self.ip_hash(ip)
        code = secrets.token_urlsafe(32) if owner_id is None else None
        digest = hashlib.sha256(code.encode()).hexdigest() if code else None
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for column, identity in (("ip_hash", ip_hash), ("owner_id", owner_id)):
                if identity is not None:
                    self._rate(conn, "feedback_tickets", f"{column}=?", [identity], 5, 20, now)
            for column, identity in (("ip_hash", ip_hash), ("actor_id", owner_id)):
                if identity is not None:
                    self._rate(conn, "feedback_messages", f"role='player' AND {column}=?", [identity], 20, 100, now)
            cursor = conn.execute("""INSERT INTO feedback_tickets
                (owner_id,query_code_hash,ip_hash,category,content,contact,contact_email,created_at_epoch)
                VALUES (?,?,?,?,?,?,?,?)""", (owner_id, digest, ip_hash, category, content, contact, contact_email, now))
            ticket_id = cursor.lastrowid
            self._message(conn, ticket_id, "player", owner_id, content, now, ip_hash)
            return {"ok": True, "ticket_id": ticket_id, **({"query_code": code} if code else {})}

    def listing(self, owner_id=None, *, admin=False, status="all", page=1):
        self.integer(page, "page", 1000000)
        self.choice(status, ("all",) + STATUSES, "status")
        if not admin and owner_id is None:
            raise self.error(-32001, "请先登录后查看自己的反馈")
        clauses, values = [], []
        if not admin:
            clauses.append("owner_id=?")
            values.append(owner_id)
        if status != "all":
            clauses.append("status=?")
            values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT count(*) FROM feedback_tickets" + where, values).fetchone()[0]
            rows = conn.execute("""SELECT id, category, substr(content,1,80) AS preview, status,
                created_at_epoch, updated_at_epoch,
                (SELECT count(*) FROM feedback_messages m WHERE m.ticket_id=t.id) AS message_count,
                (SELECT role FROM feedback_messages m WHERE m.ticket_id=t.id ORDER BY id DESC LIMIT 1) AS last_message_role
                FROM feedback_tickets t""" + where +
                " ORDER BY coalesce(updated_at_epoch,created_at_epoch) DESC,id DESC LIMIT ? OFFSET ?",
                values + [PAGE_SIZE, (page-1)*PAGE_SIZE]).fetchall()
            unread_ids = {row["id"] for row in self._unread_rows(conn, owner_id)} if not admin else set()
        return {"tickets": [dict(row) if admin else {**dict(row), "unread": row["id"] in unread_ids} for row in rows],
                "total": total, "page": page, "page_size": PAGE_SIZE,
                **({"unread_count": len(unread_ids)} if not admin else {})}

    def _code_hash(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
            self._missing()
        return hashlib.sha256(code.encode()).hexdigest()

    def _missing(self):
        raise self.error(-32004, "未找到反馈或无权查看")

    def _owned(self, conn, ticket_id, owner_id, code=None):
        self.integer(ticket_id, "ticket_id")
        if code is not None:
            row = conn.execute("SELECT id,owner_id FROM feedback_tickets WHERE id=? AND owner_id IS NULL AND query_code_hash=?",
                               (ticket_id, self._code_hash(code))).fetchone()
        elif owner_id is not None:
            row = conn.execute("SELECT id,owner_id FROM feedback_tickets WHERE id=? AND owner_id=?", (ticket_id, owner_id)).fetchone()
        else:
            raise self.error(-32001, "请登录或提供该单私密查询码")
        if row is None:
            self._missing()
        return row

    def _detail(self, conn, ticket_id, admin, before_id):
        if before_id is not None:
            self.integer(before_id, "before_id")
        row = conn.execute(f"SELECT {ADMIN_FIELDS if admin else PUBLIC_FIELDS} FROM feedback_tickets WHERE id=?", (ticket_id,)).fetchone()
        if row is None:
            self._missing()
        count = conn.execute("SELECT count(*) FROM feedback_messages WHERE ticket_id=?", (ticket_id,)).fetchone()[0]
        messages = conn.execute("SELECT id,role,content,created_at_epoch FROM feedback_messages WHERE ticket_id=?" +
                                (" AND id < ?" if before_id is not None else "") + " ORDER BY id DESC LIMIT ?",
                                [ticket_id, *([before_id] if before_id is not None else []), MESSAGE_PAGE_SIZE+1]).fetchall()
        has_more = len(messages) > MESSAGE_PAGE_SIZE
        messages = messages[:MESSAGE_PAGE_SIZE]
        return {"ticket": dict(row), "messages": [dict(m) for m in reversed(messages)], "message_count": count,
                "next_before_id": messages[-1]["id"] if has_more else None}

    def detail(self, ticket_id, owner_id=None, *, admin=False, before_id=None):
        self.integer(ticket_id, "ticket_id")
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN" if admin else "BEGIN IMMEDIATE")
            if not admin:
                self._owned(conn, ticket_id, owner_id)
            result = self._detail(conn, ticket_id, admin, before_id)
            if not admin:
                # Only opening the latest detail acknowledges this snapshot. Loading
                # older history must not swallow replies arriving after it was opened.
                if before_id is None:
                    latest = conn.execute("SELECT coalesce(max(id),0) FROM feedback_messages WHERE ticket_id=? AND role='admin'",
                                          (ticket_id,)).fetchone()[0]
                    conn.execute("""INSERT INTO feedback_read_cursors(ticket_id,owner_id,read_id) VALUES (?,?,?)
                        ON CONFLICT(ticket_id,owner_id) DO UPDATE SET read_id=max(read_id,excluded.read_id)""",
                        (ticket_id, owner_id, latest))
                result["unread_count"] = len(self._unread_rows(conn, owner_id))
            return result

    def query(self, code, *, before_id=None):
        digest = self._code_hash(code)
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT id FROM feedback_tickets WHERE owner_id IS NULL AND query_code_hash=?", (digest,)).fetchone()
            if row is None:
                self._missing()
            return self._detail(conn, row["id"], False, before_id)

    def append(self, body, owner_id, ip):
        if set(body) - {"ticket_id", "content", "query_code"}:
            raise self.error(-32602, "追问仅接受 ticket_id、content 和可选 query_code")
        content = self.text(body, "content", 4000, True)
        ticket_id = self.integer(body.get("ticket_id"), "ticket_id")
        now, ip_hash = int(time.time()), self.ip_hash(ip)
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if "query_code" in body:
                self._code_hash(body["query_code"])  # Explicit null/empty code never falls back to an owner.
            row = self._owned(conn, ticket_id, owner_id, body.get("query_code"))
            for column, identity, hourly, daily in (("ip_hash", ip_hash, 20, 100),
                    ("actor_id", row["owner_id"], 20, 100), ("ticket_id", ticket_id, 10, 40)):
                if identity is not None:
                    self._rate(conn, "feedback_messages", f"role='player' AND {column}=?", [identity], hourly, daily, now)
            message_id = self._message(conn, ticket_id, "player", row["owner_id"], content, now, ip_hash)
            conn.execute("UPDATE feedback_tickets SET status='pending', updated_at_epoch=? WHERE id=?", (now,ticket_id))
        return {"ok": True, "ticket_id": ticket_id, "message_id": message_id, "status": "pending"}

    def close(self, body, owner_id):
        if set(body) - {"ticket_id", "confirm", "query_code"}:
            raise self.error(-32602, "结束工单仅接受 ticket_id、confirm 和可选 query_code")
        if body.get("confirm") is not True:
            raise self.error(-32602, "结束工单需要 confirm=true")
        ticket_id = self.integer(body.get("ticket_id"), "ticket_id")
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if "query_code" in body:
                self._code_hash(body["query_code"])
            self._owned(conn, ticket_id, owner_id, body.get("query_code"))
            # A repeated close is a no-op, including timestamps and notifications.
            conn.execute("""UPDATE feedback_tickets SET status='closed', updated_at_epoch=?
                            WHERE id=? AND status!='closed'""", (int(time.time()), ticket_id))
            row = conn.execute("SELECT updated_at_epoch FROM feedback_tickets WHERE id=?", (ticket_id,)).fetchone()
        return {"ok": True, "ticket_id": ticket_id, "status": "closed", "updated_at_epoch": row[0]}

    def review(self, body, admin):
        ticket_id = self.integer(body.get("ticket_id"), "ticket_id")
        status = self.choice(body["status"], STATUSES, "status") if "status" in body else None
        reply = self.text(body, "reply", 4000, True) if "reply" in body else None
        note = self.text(body, "internal_note", 4000) if "internal_note" in body else None
        if status is None and reply is None and note is None:
            raise self.error(-32602, "请追加回复、修改内部备注或设置处理状态")
        if reply is not None and status not in (None, "processing", "closed"):
            raise self.error(-32602, "管理员回复后状态只能为 processing，或显式 closed 结单")
        now = int(time.time())
        notice = None
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id, status, contact_email FROM feedback_tickets WHERE id=?", (ticket_id,)).fetchone()
            if row is None:
                self._missing()
            self._rate(conn, "feedback_review_limits", "admin_id=?", [admin["id"]], 120, 1000, now)
            conn.execute("DELETE FROM feedback_review_limits WHERE created_at_epoch < ?", (now-86400,))
            conn.execute("INSERT INTO feedback_review_limits VALUES (?,?)", (admin["id"],now))
            updates = {"reviewed_by": admin["id"], "updated_at_epoch": now}
            message_id = None
            if reply is not None:
                message_id = self._message(conn, ticket_id, "admin", admin["id"], reply, now)
                updates["status"] = "closed" if status == "closed" else "processing"
            elif status is not None:
                updates["status"] = status
            if note is not None:
                updates["internal_note"] = note
            conn.execute("UPDATE feedback_tickets SET " + ", ".join(f"{key}=?" for key in updates) +
                         " WHERE id=?", [*updates.values(), ticket_id])
            closed = updates.get("status") == "closed"
            closed_now = closed and row["status"] != "closed"
            if row["contact_email"] and (reply is not None or closed_now):
                event = conn.execute("""INSERT INTO feedback_email_events
                    (ticket_id, admin_message_id, kind, status, created_at_epoch)
                    VALUES (?,?,?,'sending',?)""",
                    (ticket_id, message_id, "reply" if reply is not None else "closed", now))
                notice = (event.lastrowid, row["contact_email"], ticket_id, reply, closed)
        # The review is committed before network I/O. Mail failure never rolls it back.
        if notice is not None:
            self._deliver_email_notice(*notice)
        return {"ok": True}

    def _deliver_email_notice(self, event_id, email, ticket_id, reply, closed):
        status, reason = "sent", ""
        try:
            if self.send_email is None:
                raise RuntimeError("mail sender missing")
            self.send_email(email, ticket_id, reply, closed)
        except Exception as exc:
            status = "failed"
            details = getattr(exc, "details", None)
            reason = details.get("reason") if isinstance(details, dict) else None
            if reason not in {"email_provider_not_configured", "email_provider_misconfigured",
                              "email_delivery_unavailable"}:
                reason = "email_delivery_unavailable"
            logging.getLogger(__name__).warning("feedback email failed: event=%s reason=%s", event_id, reason)
        try:
            with closing(self.connect()) as conn, conn:
                conn.execute("""UPDATE feedback_email_events
                    SET status=?, error=?, finished_at_epoch=?
                    WHERE id=? AND status='sending'""",
                    (status, reason, int(time.time()), event_id))
        except Exception:
            logging.getLogger(__name__).warning("feedback email outcome not recorded: event=%s", event_id)
