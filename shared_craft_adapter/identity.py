"""Read current platform membership; never provision accounts or credentials."""
import sqlite3
from contextlib import closing
from pathlib import Path


def connect_readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def require_active_account(db_path, user_id):
    """Validate the current account without requiring a human binding/room."""
    with closing(connect_readonly(db_path)) as conn:
        user = conn.execute("SELECT * FROM toy_users WHERE id=?", (int(user_id),)).fetchone()
        if not user or (dict(user).get("deleted_at") is not None
                        or dict(user).get("deletion_requested_at_epoch") is not None):
            raise PermissionError("账号不可用")


def resolve(db_path, user_id):
    with closing(connect_readonly(db_path)) as conn:
        conn.execute("BEGIN")
        user = conn.execute("SELECT * FROM toy_users WHERE id=?", (int(user_id),)).fetchone()
        if not user or (dict(user).get("deleted_at") is not None or dict(user).get("deletion_requested_at_epoch") is not None):
            raise PermissionError("账号不可用")
        if user["is_ai"]:
            rows = conn.execute(
                "SELECT human_user_id FROM user_bindings WHERE ai_user_id=?", (user["id"],)
            ).fetchall()
            if len(rows) != 1:
                raise PermissionError("请先绑定人类账号")
            room = rows[0][0]
        else:
            room = user["id"]
        human = conn.execute("SELECT * FROM toy_users WHERE id=?", (room,)).fetchone()
        if not human or human["is_ai"] or (dict(human).get("deleted_at") is not None or dict(human).get("deletion_requested_at_epoch") is not None):
            raise PermissionError("手作空间不可用")
        members = conn.execute(
            """SELECT u.* FROM toy_users u WHERE u.id=? OR
            (u.is_ai=1 AND EXISTS (SELECT 1 FROM user_bindings b
             WHERE b.human_user_id=? AND b.ai_user_id=u.id)) ORDER BY u.id""", (room, room)
        ).fetchall()
        participants = [{"id": str(row["id"]), "display_name": row["username"],
                         "kind": "ai" if row["is_ai"] else "human"}
                        for row in members if dict(row).get("deleted_at") is None and dict(row).get("deletion_requested_at_epoch") is None]
        return {"room": str(room), "actor": str(user["id"]), "is_ai": bool(user["is_ai"]),
                "participants": participants}


def author_access(db_path, user_id):
    if not Path(db_path).is_file():
        return False
    with closing(connect_readonly(db_path)) as conn:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shared_craft_authors'"
        ).fetchone() and conn.execute(
            "SELECT 1 FROM shared_craft_authors WHERE user_id=?", (int(user_id),)
        ).fetchone())


def initialize_author_grants(conn):
    """Operator-only setup, against the actual SESSIONS_DB. Grants nobody."""
    conn.execute("""CREATE TABLE IF NOT EXISTS shared_craft_authors (
        user_id INTEGER PRIMARY KEY CHECK(user_id > 0))""")
