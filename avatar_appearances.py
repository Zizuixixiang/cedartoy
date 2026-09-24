"""Account-owned avatar appearances; catalog stays in code, ownership in SQLite."""

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
