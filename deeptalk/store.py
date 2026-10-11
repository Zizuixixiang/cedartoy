"""All answer serialization enforces the persisted round visibility mode."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3

from .catalog import DeeptalkError, load_topic
from cedar_backend.display_titles import DEEPTALK_TITLES


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise DeeptalkError(f"{name} 须为 {low}–{high} 的整数")
    return value


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", value):
        raise DeeptalkError("request_id 须为8–100位字母、数字、下划线或短横线")
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path, *, bound_human_ids=()):
        self.db_path = str(db_path)
        # Server-resolved bindings only; never populate this from request parameters.
        self.bound_human_ids = tuple(bound_human_ids)

    @contextmanager
    def connection(self, write=False):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS deeptalk_rounds (
                    id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, title TEXT NOT NULL,
                    human_id INTEGER NOT NULL, ai_id INTEGER NOT NULL,
                    human_name TEXT NOT NULL, ai_name TEXT NOT NULL,
                    snapshot TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    human_answers TEXT NOT NULL DEFAULT '{}', ai_answers TEXT NOT NULL DEFAULT '{}',
                    human_revision INTEGER NOT NULL DEFAULT 0, ai_revision INTEGER NOT NULL DEFAULT 0,
                    human_submitted TEXT, ai_submitted TEXT,
                    create_key TEXT NOT NULL, UNIQUE(human_id, create_key)
                );
                CREATE INDEX IF NOT EXISTS deeptalk_human ON deeptalk_rounds(human_id, created_at);
                CREATE INDEX IF NOT EXISTS deeptalk_ai ON deeptalk_rounds(ai_id, created_at);
                CREATE TABLE IF NOT EXISTS deeptalk_requests (
                    round_id TEXT NOT NULL, role TEXT NOT NULL, request_key TEXT NOT NULL,
                    digest TEXT NOT NULL, PRIMARY KEY(round_id, role, request_key)
                );
            """)
            # The default deliberately preserves the original single-AI access rule.
            # Serialize migration with writes, including concurrent first requests.
            columns = {r[1] for r in conn.execute("PRAGMA table_info(deeptalk_rounds)")}
            if not {"sharing_mode", "mode"} <= columns:
                conn.execute("BEGIN IMMEDIATE")
                columns = {r[1] for r in conn.execute("PRAGMA table_info(deeptalk_rounds)")}
                if "sharing_mode" not in columns:
                    conn.execute("ALTER TABLE deeptalk_rounds ADD COLUMN sharing_mode TEXT NOT NULL DEFAULT 'single_ai'")
                if "mode" not in columns:
                    conn.execute("ALTER TABLE deeptalk_rounds ADD COLUMN mode TEXT NOT NULL DEFAULT 'blind'")
                conn.commit()
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def role(self, row, user):
        role = "ai" if user.get("is_ai") else "human"
        if role == "ai" and row["sharing_mode"] == "bound_shared":
            allowed = row["human_id"] in self.bound_human_ids
        else:
            allowed = int(user["id"]) == row[f"{role}_id"]
        if not allowed:
            raise DeeptalkError("轮次不存在或无权访问", 404)
        return role

    def row(self, conn, round_id, user):
        if not isinstance(round_id, str):
            raise DeeptalkError("缺少 round_id")
        row = conn.execute("SELECT * FROM deeptalk_rounds WHERE id=?", (round_id,)).fetchone()
        if row is None:
            raise DeeptalkError("轮次不存在或无权访问", 404)
        return row, self.role(row, user)

    @staticmethod
    def summary(row):
        unlocked = bool(row["human_submitted"] and row["ai_submitted"])
        return dict(round_id=row["id"], title=DEEPTALK_TITLES.get(row["topic_id"], row["title"]), human_name=row["human_name"],
                    ai_name=row["ai_name"], sharing_mode=row["sharing_mode"], mode=row["mode"], status="complete" if unlocked else "in_progress",
                    created_at=row["created_at"], updated_at=row["updated_at"],
                    url=f"/deeptalk?round_id={row['id']}")

    def history(self, user, offset=0, limit=20):
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 100)
        role = "ai" if user.get("is_ai") else "human"
        if role == "ai":
            placeholders = ",".join("?" for _ in self.bound_human_ids) or "NULL"
            where = f"((sharing_mode='single_ai' AND ai_id=?) OR (sharing_mode='bound_shared' AND human_id IN ({placeholders})))"
            args = (int(user["id"]), *self.bound_human_ids)
        else:
            where, args = "human_id=?", (int(user["id"]),)
        with self.connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM deeptalk_rounds WHERE {where}", args).fetchone()[0]
            rows = conn.execute(f"SELECT id,topic_id,title,human_name,ai_name,sharing_mode,mode,human_submitted,ai_submitted,created_at,updated_at FROM deeptalk_rounds WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                                (*args, limit, offset)).fetchall()
            return dict(rounds=[self.summary(r) for r in rows], total=total, offset=offset, limit=limit)

    def create(self, human, ai, topic_id, key, *, sharing_mode="single_ai", mode="blind"):
        key = request_key(key)
        if sharing_mode not in {"single_ai", "bound_shared"}:
            raise DeeptalkError("未知共享模式")
        if not isinstance(mode, str) or mode not in {"blind", "chat"}:
            raise DeeptalkError("请选择答完再看或边聊边答")
        ai_id = 0 if sharing_mode == "bound_shared" else int(ai["id"])
        ai_name = "小机侧（共享）" if sharing_mode == "bound_shared" else ai["username"]
        with self.connection(write=True) as conn:
            old = conn.execute("SELECT * FROM deeptalk_rounds WHERE human_id=? AND create_key=?", (int(human["id"]), key)).fetchone()
            if old:
                if old["topic_id"] != topic_id or old["ai_id"] != ai_id or old["sharing_mode"] != sharing_mode or old["mode"] != mode:
                    raise DeeptalkError("同一 request_id 不可用于不同开局", 409)
                return self.serialize(old, "human")
            topic = load_topic(topic_id)
            rid, stamp = secrets.token_urlsafe(24), now()
            conn.execute("""INSERT INTO deeptalk_rounds
                (id,topic_id,title,human_id,ai_id,human_name,ai_name,snapshot,created_at,updated_at,create_key,sharing_mode,mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (rid, topic_id, topic["title"], int(human["id"]), ai_id,
                human["username"], ai_name, json.dumps(topic, ensure_ascii=False), stamp, stamp, key, sharing_mode, mode))
            row, role = self.row(conn, rid, human)
            return self.serialize(row, role)

    @staticmethod
    def serialize(row, role, *, number=None, limit=1, full=False):
        """The only detailed read path. Blind peer answers require both submissions."""
        topic = json.loads(row["snapshot"])
        own = json.loads(row[f"{role}_answers"])
        unlocked = bool(row["human_submitted"] and row["ai_submitted"])
        out = Store.summary(row)
        out.update(topic_id=row["topic_id"], total=len(topic["questions"]), unlocked=unlocked,
                   submitted=bool(row[f"{role}_submitted"]), revision=row[f"{role}_revision"],
                   next_number=next((i for i, q in enumerate(topic["questions"], 1) if q["id"] not in own), 1),
                   progress={r: {"done": len(json.loads(row[f"{r}_answers"])), "submitted": bool(row[f"{r}_submitted"])} for r in ("human", "ai")})
        if full:
            out["description"] = topic.get("description", "")
        if full or number is not None:
            start = 0 if full else integer(number, "number", 1, len(topic["questions"])) - 1
            count = len(topic["questions"]) if full else integer(limit, "limit", 1, 20)
            peer_role = "human" if role == "ai" else "ai"
            peer = json.loads(row[f"{peer_role}_answers"]) if unlocked or row["mode"] == "chat" else {}
            items = []
            for i, q in enumerate(topic["questions"][start:start + count], start + 1):
                item = dict(number=i, id=q["id"], text=q["text"], own=own.get(q["id"]))
                if q["id"] in peer:
                    item["partner"] = peer.get(q["id"])
                items.append(item)
            out["questions"] = items
        return out

    def read(self, user, round_id, *, action="status", number=1, limit=1):
        with self.connection() as conn:
            row, role = self.row(conn, round_id, user)
            if action == "result" and row["mode"] != "chat" and not (row["human_submitted"] and row["ai_submitted"]):
                raise DeeptalkError("双方正式提交后才可查看双人回答", 409)
            return self.serialize(row, role, number=number if action in {"question", "result"} else None,
                                  limit=limit, full=action == "view")

    def source(self, user, round_id):
        with self.connection() as conn:
            row, _role = self.row(conn, round_id, user)
            return {k: v for k, v in json.loads(row["snapshot"]).items() if k != "questions"}

    def answer(self, user, params):
        key = request_key(params.get("request_id"))
        revision = integer(params.get("revision"), "revision", 0, 1000000000)
        qid = params.get("question_id")
        if type(qid) not in (str, int):
            raise DeeptalkError("缺少 question_id")
        qid = str(qid)
        skip = params.get("skip", False)
        if type(skip) is not bool:
            raise DeeptalkError("skip 须为布尔值")
        value = params.get("text", "")
        if skip:
            answer = {"skipped": True}
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > 8000:
                raise DeeptalkError("回答须为1–8000字，或明确跳过")
            answer = {"text": value, "skipped": False}
        digest = hashlib.sha256(json.dumps([qid, answer, revision], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.connection(write=True) as conn:
            row, role = self.row(conn, params.get("round_id"), user)
            previous = conn.execute("SELECT digest FROM deeptalk_requests WHERE round_id=? AND role=? AND request_key=?", (row["id"], role, key)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise DeeptalkError("同一 request_id 不可用于不同回答", 409)
                return self.serialize(row, role)
            if row[f"{role}_submitted"]:
                raise DeeptalkError("已正式提交，不能修改", 409)
            if revision != row[f"{role}_revision"]:
                raise DeeptalkError("答案已在其他页面更新，请刷新后再改", 409)
            if qid not in {q["id"] for q in json.loads(row["snapshot"])["questions"]}:
                raise DeeptalkError("未知题号")
            answers = json.loads(row[f"{role}_answers"])
            answers[qid] = answer
            conn.execute(f"UPDATE deeptalk_rounds SET {role}_answers=?, {role}_revision={role}_revision+1, updated_at=? WHERE id=?",
                         (json.dumps(answers, ensure_ascii=False), now(), row["id"]))
            conn.execute("INSERT INTO deeptalk_requests VALUES (?,?,?,?)", (row["id"], role, key, digest))
            row, role = self.row(conn, row["id"], user)
            return self.serialize(row, role)

    def submit(self, user, params):
        if params.get("confirm") is not True:
            raise DeeptalkError("请确认提交整卷：confirm=true")
        revision = integer(params.get("revision"), "revision", 0, 1000000000)
        with self.connection(write=True) as conn:
            row, role = self.row(conn, params.get("round_id"), user)
            if not row[f"{role}_submitted"]:
                if revision != row[f"{role}_revision"]:
                    raise DeeptalkError("答案已更新，请检查后重新确认提交", 409)
                if len(json.loads(row[f"{role}_answers"])) != len(json.loads(row["snapshot"])["questions"]):
                    raise DeeptalkError("请先回答或明确跳过每一道题")
                stamp = now()
                conn.execute(f"UPDATE deeptalk_rounds SET {role}_submitted=?, updated_at=? WHERE id=?", (stamp, stamp, row["id"]))
                row, role = self.row(conn, row["id"], user)
            return self.serialize(row, role)
