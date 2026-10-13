"""Private Candle feedback and its narrowly scoped author read grant.

No account is granted access by default. Never treat platform is_admin as a grant.
"""

import sqlite3
from contextlib import closing
from pathlib import Path


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS candle_authors (
        user_id INTEGER PRIMARY KEY CHECK(user_id > 0)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS candle_feedback (
        id INTEGER PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL,
        player_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('choose', 'comment')),
        chosen INTEGER CHECK(chosen BETWEEN 0 AND 13),
        comment TEXT CHECK(length(comment) <= 300),
        visibility TEXT NOT NULL DEFAULT 'private_to_author'
            CHECK(visibility = 'private_to_author'),
        created_at INTEGER NOT NULL,
        source_commit TEXT NOT NULL
    )""")


def append_events(db_path, events):
    if not events:
        return
    with closing(sqlite3.connect(db_path, timeout=15)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        initialize(conn)
        for event in events:
            conn.execute("""INSERT INTO candle_feedback
                (event_id, run_id, player_id, kind, chosen, comment, created_at, source_commit)
                VALUES (:event_id, :run_id, :player_id, :kind, :chosen, :comment,
                        :created_at, :source_commit)
                ON CONFLICT(event_id) DO NOTHING""", event)


def _has_author_grant(conn, user):
    return bool(user and user.get("id") and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candle_authors'"
    ).fetchone() and conn.execute(
        "SELECT 1 FROM candle_authors WHERE user_id=?", (int(user["id"]),)
    ).fetchone())


def _read_connection(db_path):
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)


def author_access(db_path, user):
    """Only report the caller's grant; never initialize or mutate the database."""
    if not Path(db_path).is_file():
        return False
    with closing(_read_connection(db_path)) as conn:
        return _has_author_grant(conn, user)


def read_for_author(db_path, user, *, page=1, limit=10):
    # Caller must pass the server-authenticated account, never a request body.
    if not user or not user.get("id") or not Path(db_path).is_file():
        raise PermissionError("仅本作作者可查看反馈")
    with closing(_read_connection(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        if not _has_author_grant(conn, user):
            raise PermissionError("仅本作作者可查看反馈")
        page, limit = int(page), int(limit)
        if page < 1 or not 1 <= limit <= 20:
            raise ValueError("page 必须为正数，limit 必须为 1–20")
        # Keep the append-only audit log intact. A viewing contributes one row;
        # choose may arrive after comment, and repeated comments show the latest.
        grouped = """WITH runs AS (
            SELECT MAX(id) AS last_id, MAX(created_at) AS created_at,
                   MAX(CASE WHEN chosen IS NOT NULL THEN id END) AS choice_id,
                   MAX(CASE WHEN kind='comment' THEN id END) AS comment_id
            FROM candle_feedback GROUP BY player_id, run_id
        ), feedback AS (
            SELECT runs.last_id, runs.created_at, choice.chosen, comment.comment
            FROM runs
            LEFT JOIN candle_feedback AS choice ON choice.id=runs.choice_id
            LEFT JOIN candle_feedback AS comment ON comment.id=runs.comment_id
        ) """
        total = conn.execute(grouped + "SELECT COUNT(*) FROM feedback").fetchone()[0]
        pages = max(1, (total + limit - 1) // limit)
        page = min(page, pages)
        rows = conn.execute(grouped + "SELECT created_at, chosen, comment FROM feedback" +
                            " ORDER BY created_at DESC, last_id DESC LIMIT ? OFFSET ?",
                            (limit, (page - 1) * limit)).fetchall()
    return {"game": "candle", "items": [
        dict(row) for row in rows
    ], "total": total, "page": page, "pages": pages,
        "has_prev": page > 1, "has_next": page < pages}
