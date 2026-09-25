"""Account-owned avatar appearances; catalog stays in code, ownership in SQLite."""

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ONE_W_ID = 10000
ONE_W_BINDING_END = "2026-09-26 00:00:00"  # Exclusive; DB timestamps are Beijing time.
ONE_W_END_EPOCH = datetime(2026, 9, 26, tzinfo=timezone(timedelta(hours=8))).timestamp()

CATALOG = {
    "cedartoy_1w_decoration": {
        "name": "1W 小机器人",
        "kind": "decoration",
        "src": "/assets/icons/cedartoy-1w-decoration.webp",
    },
    "cedartoy_1w": {
        "name": "1W 万人纪念框",
        "kind": "frame",
        "src": "/assets/icons/cedartoy-1w-frame.webp?v=20260925c",
    },
}
TRIAL_SEED_KEY = "avatar_appearance_trial_user_1_v1"


def init_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_avatar_inventory (
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            granted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (user_id, item_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_avatar_selection (
            user_id INTEGER PRIMARY KEY REFERENCES toy_users(id) ON DELETE CASCADE,
            item_key TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id, item_key)
                REFERENCES account_avatar_inventory(user_id, item_key)
        )
    """)


def grant(conn, user_ids, item_keys):
    """Idempotent bulk grant in the caller's transaction; never changes selection."""
    keys = tuple(dict.fromkeys(item_keys))
    if any(key not in CATALOG for key in keys):
        raise ValueError("未知头像框")
    conn.executemany(
        "INSERT OR IGNORE INTO account_avatar_inventory (user_id, item_key) VALUES (?, ?)",
        ((user_id, key) for user_id in user_ids for key in keys),
    )


def grant_one_w(conn, *, user_id=None):
    """Reconcile eligible ownership in the caller's transaction, never selection.

    Request hooks may limit the ordinary grant to one account; startup reconciles
    all missing ordinary grants. No completion flag: late registration,
    bindings and process restarts must remain recoverable from the source rows.
    """
    ordinary = conn.execute("""
        SELECT u.id FROM toy_users u
        WHERE u.id <= ? AND u.deleted_at IS NULL
          AND (? IS NULL OR u.id = ?)
          AND NOT EXISTS (
              SELECT 1 FROM account_avatar_inventory i
              WHERE i.user_id = u.id AND i.item_key = 'cedartoy_1w_decoration'
          )
    """, (ONE_W_ID, user_id, user_id)).fetchall()
    before = conn.total_changes
    grant(conn, (row[0] for row in ordinary), ["cedartoy_1w_decoration"])
    ordinary_count = conn.total_changes - before

    # Both the anchor AI's owner link and its siblings' links must predate midnight.
    # Do not follow arbitrary graph edges or merge inventories by binding identity.
    exclusive = conn.execute("""
        WITH anchor AS (
            SELECT id, is_ai FROM toy_users WHERE id = ? AND deleted_at IS NULL
        ), humans AS (
            SELECT id FROM anchor WHERE is_ai = 0
            UNION
            SELECT b.human_user_id FROM user_bindings b JOIN anchor a ON b.ai_user_id = a.id
            JOIN toy_users h ON h.id = b.human_user_id
            WHERE a.is_ai = 1 AND h.is_ai = 0 AND h.deleted_at IS NULL
              AND b.created_at < ?
        ), recipients AS (
            SELECT id FROM anchor
            UNION SELECT id FROM humans
            UNION
            SELECT b.ai_user_id FROM user_bindings b JOIN humans h ON b.human_user_id = h.id
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE ai.is_ai = 1 AND b.created_at < ?
        )
        SELECT u.id FROM recipients r JOIN toy_users u ON u.id = r.id
        WHERE u.deleted_at IS NULL AND NOT EXISTS (
            SELECT 1 FROM account_avatar_inventory i
            WHERE i.user_id = u.id AND i.item_key = 'cedartoy_1w'
        )
    """, (ONE_W_ID, ONE_W_BINDING_END, ONE_W_BINDING_END)).fetchall()
    before = conn.total_changes
    grant(conn, (row[0] for row in exclusive), ["cedartoy_1w"])
    return {"decoration": ordinary_count, "frame": conn.total_changes - before}


def reconcile_one_w(db_path):
    """Standalone backfill; fail on a wrong/missing DB instead of creating one."""
    uri = Path(db_path).resolve().as_uri() + "?mode=rw"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        init_schema(conn)
        result = grant_one_w(conn)
        conn.commit()
        return result
    finally:
        conn.close()


def watch_one_w(db_path):
    """Temporary catch-up for other writers; final sweep at midnight, then exit.

    Safe to start after the date: one reconciliation only, no polling. Failures
    retry during the window; a failed final sweep raises for the service manager.
    """
    while True:
        final_sweep = time.time() >= ONE_W_END_EPOCH
        try:
            result = reconcile_one_w(db_path)
            if any(result.values()):
                logging.info("1W appearance grants: %s", result)
        except sqlite3.Error:
            logging.exception("1W appearance reconciliation failed")
            if final_sweep:
                raise
        if final_sweep:
            return
        time.sleep(max(0, min(10, ONE_W_END_EPOCH - time.time())))


def seed_trial(conn):
    """One-time test seed, called after platform settings/schema initialization."""
    if conn.execute("SELECT 1 FROM settings WHERE key = ?", (TRIAL_SEED_KEY,)).fetchone():
        return
    if not conn.execute("SELECT 1 FROM toy_users WHERE id = 1 AND deleted_at IS NULL").fetchone():
        return
    grant(conn, [1], CATALOG)
    conn.execute(
        "INSERT OR IGNORE INTO account_avatar_selection (user_id, item_key) VALUES (1, ?)",
        ("cedartoy_1w_decoration",),
    )
    conn.execute("INSERT INTO settings (key, value) VALUES (?, 'done')", (TRIAL_SEED_KEY,))


def selected(conn, user_id):
    row = conn.execute("""
        SELECT s.item_key FROM account_avatar_selection s
        JOIN account_avatar_inventory i ON i.user_id = s.user_id AND i.item_key = s.item_key
        WHERE s.user_id = ?
    """, (user_id,)).fetchone()
    return row[0] if row and row[0] in CATALOG else None


def owned(conn, user_id):
    keys = {row[0] for row in conn.execute(
        "SELECT item_key FROM account_avatar_inventory WHERE user_id = ?", (user_id,)
    )}
    return {
        "items": [{"key": key, **item} for key, item in CATALOG.items() if key in keys],
        "selected": selected(conn, user_id),
    }


def select(conn, user_id, item_key):
    if item_key is not None and not isinstance(item_key, str):
        raise ValueError("selected 必须为头像框 key 或 null")
    if item_key in ("", "none"):
        item_key = None
    if item_key is not None:
        if item_key not in CATALOG or not conn.execute(
            "SELECT 1 FROM account_avatar_inventory WHERE user_id = ? AND item_key = ?",
            (user_id, item_key),
        ).fetchone():
            raise ValueError("你尚未拥有此头像框")
    conn.execute("""
        INSERT INTO account_avatar_selection (user_id, item_key) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET item_key = excluded.item_key,
            updated_at = datetime('now', 'localtime')
    """, (user_id, item_key))
