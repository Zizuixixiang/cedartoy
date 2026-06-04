import os
from pathlib import Path
from typing import Any, Iterable

import aiosqlite


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("TURTLE_SOUP_DB", BASE_DIR / "turtle_soup.db"))


DEFAULT_SETTINGS = {
    "max_rooms": "5",
    "hint_trigger_count": "30",
    "ai_cooldown_questions": "5",
    "ai_cooldown_seconds": "3",
    "generate_cooldown_seconds": "5",
    "judge_prompt": "你是海龟汤游戏裁判。",
    "generate_prompt": "你是海龟汤出题人。返回 JSON，字段 surface 和 answer。生成一道适合多人推理、无血腥露骨描写的中文海龟汤。",
    "guest_expire_hours": "1",
    "room_inactive_expire_hours": "48",
    "lobby_finished_visible_hours": "3",
    "finished_room_retention_hours": "24",
}

TIMEZONE_MIGRATION_KEY = "timezone_utc_to_shanghai_20260602"


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    return db


def row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


async def fetch_one(query: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    db = await get_db()
    try:
        async with db.execute(query, tuple(params)) as cur:
            return row_to_dict(await cur.fetchone())
    finally:
        await db.close()


async def fetch_all(query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        async with db.execute(query, tuple(params)) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def execute(query: str, params: Iterable[Any] = ()) -> int:
    db = await get_db()
    try:
        cur = await db.execute(query, tuple(params))
        await db.commit()
        return int(cur.lastrowid or 0)
    finally:
        await db.close()


async def get_setting(key: str, default: str | None = None) -> str:
    row = await fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row:
        return str(row["value"])
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, "")


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    rows = await db.execute_fetchall(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return bool(rows)


async def _create_localtime_triggers(db: aiosqlite.Connection) -> None:
    trigger_specs = {
        "players": ("id", ("created_at", "last_active_at")),
        "puzzles": ("id", ("created_at",)),
        "puzzle_submissions": ("id", ("created_at",)),
        "rooms": ("id", ("created_at",)),
        "game_logs": ("id", ("created_at",)),
        "room_notes": ("id", ("created_at", "updated_at")),
        "judge_api_configs": ("id", ("created_at",)),
        "reports": ("id", ("created_at",)),
        "ban_ips": ("id", ("created_at",)),
        "flagged_content": ("id", ("created_at",)),
        "room_presence": (("room_id", "player_id"), ("joined_at", "last_active_at")),
        "toy_users": ("id", ("created_at", "last_active_at")),
        "user_bindings": ("id", ("created_at",)),
    }
    for table, (pk, columns) in trigger_specs.items():
        if not await _table_exists(db, table):
            continue
        assignments = ", ".join(f"{column} = datetime('now', 'localtime')" for column in columns)
        if isinstance(pk, tuple):
            where = " AND ".join(f"{column} = NEW.{column}" for column in pk)
        else:
            where = f"{pk} = NEW.{pk}"
        await db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_insert_localtime
            AFTER INSERT ON {table}
            BEGIN
                UPDATE {table}
                SET {assignments}
                WHERE {where};
            END
            """
        )


async def _migrate_existing_utc_timestamps(db: aiosqlite.Connection) -> None:
    row = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?",
        (TIMEZONE_MIGRATION_KEY,),
    )
    if row:
        return

    for table in ("players", "room_notes", "room_presence"):
        if await _table_exists(db, table):
            await db.execute(
                f"""
                UPDATE {table}
                SET last_active_at = datetime(last_active_at, '+8 hours')
                WHERE last_active_at IS NOT NULL
                  AND created_at IS NOT NULL
                  AND last_active_at = created_at
                """
                if table == "players"
                else (
                    f"""
                    UPDATE {table}
                    SET updated_at = datetime(updated_at, '+8 hours')
                    WHERE updated_at IS NOT NULL
                      AND created_at IS NOT NULL
                      AND updated_at = created_at
                    """
                    if table == "room_notes"
                    else """
                    UPDATE room_presence
                    SET last_active_at = datetime(last_active_at, '+8 hours')
                    WHERE last_active_at IS NOT NULL
                      AND joined_at IS NOT NULL
                      AND last_active_at = joined_at
                    """
                )
            )

    timestamp_columns = {
        "players": ("created_at",),
        "puzzles": ("created_at",),
        "puzzle_submissions": ("created_at",),
        "rooms": ("created_at",),
        "game_logs": ("created_at",),
        "room_notes": ("created_at",),
        "judge_api_configs": ("created_at",),
        "reports": ("created_at",),
        "ban_ips": ("created_at",),
        "flagged_content": ("created_at",),
        "room_presence": ("joined_at",),
    }
    for table, columns in timestamp_columns.items():
        if not await _table_exists(db, table):
            continue
        for column in columns:
            await db.execute(
                f"""
                UPDATE {table}
                SET {column} = datetime({column}, '+8 hours')
                WHERE {column} IS NOT NULL
                """
            )
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, '1')",
        (TIMEZONE_MIGRATION_KEY,),
    )


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await get_db()
    try:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password_hash TEXT,
                is_guest INTEGER DEFAULT 0,
                is_ai INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                source TEXT DEFAULT 'web',
                ask_count INTEGER DEFAULT 0,
                ask_count_y INTEGER DEFAULT 0,
                ask_count_n INTEGER DEFAULT 0,
                ask_count_u INTEGER DEFAULT 0,
                ask_count_p INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                game_count INTEGER DEFAULT 0,
                user_id INTEGER,
                last_active_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS puzzles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT DEFAULT '',
                surface TEXT NOT NULL,
                answer TEXT NOT NULL,
                tags TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_by INTEGER REFERENCES players(id),
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS puzzle_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                surface TEXT NOT NULL,
                answer TEXT NOT NULL,
                tags TEXT DEFAULT '',
                submitted_by INTEGER REFERENCES players(id),
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS rooms (
                id TEXT PRIMARY KEY,
                puzzle_id INTEGER REFERENCES puzzles(id),
                title TEXT DEFAULT '',
                surface TEXT NOT NULL,
                answer TEXT NOT NULL,
                status TEXT DEFAULT 'waiting',
                created_by INTEGER REFERENCES players(id),
                winner_id INTEGER REFERENCES players(id),
                manual_hint_count INTEGER DEFAULT 0,
                last_hint_at_ask_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                finished_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS game_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT REFERENCES rooms(id),
                player_id INTEGER REFERENCES players(id),
                type TEXT NOT NULL,
                content TEXT,
                judgment TEXT,
                hint_text TEXT,
                resolved INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS room_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT REFERENCES rooms(id),
                player_id INTEGER REFERENCES players(id),
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS judge_api_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                api_url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                model TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                priority INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER REFERENCES players(id),
                target_player_id INTEGER REFERENCES players(id),
                room_id TEXT REFERENCES rooms(id),
                log_id INTEGER REFERENCES game_logs(id),
                reason TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS ban_ips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT UNIQUE NOT NULL,
                reason TEXT,
                banned_by INTEGER REFERENCES players(id),
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS flagged_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                ref_id INTEGER NOT NULL,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS room_presence (
                room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                joined_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                last_active_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (room_id, player_id)
            );
            CREATE INDEX IF NOT EXISTS idx_room_presence_active
                ON room_presence(room_id, last_active_at);
            """
        )
        for key, value in DEFAULT_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        await _create_localtime_triggers(db)
        await _migrate_existing_utc_timestamps(db)
        async with db.execute("PRAGMA table_info(players)") as cur:
            player_cols = {row[1] for row in await cur.fetchall()}
        if "user_id" not in player_cols:
            await db.execute("ALTER TABLE players ADD COLUMN user_id INTEGER")
        async with db.execute("PRAGMA table_info(puzzles)") as cur:
            puzzle_cols = {row[1] for row in await cur.fetchall()}
        if "title" not in puzzle_cols:
            await db.execute("ALTER TABLE puzzles ADD COLUMN title TEXT DEFAULT ''")
        async with db.execute("PRAGMA table_info(rooms)") as cur:
            room_cols = {row[1] for row in await cur.fetchall()}
        if "title" not in room_cols:
            await db.execute("ALTER TABLE rooms ADD COLUMN title TEXT DEFAULT ''")
        if "manual_hint_count" not in room_cols:
            await db.execute("ALTER TABLE rooms ADD COLUMN manual_hint_count INTEGER DEFAULT 0")
        if "last_hint_at_ask_count" not in room_cols:
            await db.execute("ALTER TABLE rooms ADD COLUMN last_hint_at_ask_count INTEGER DEFAULT 0")
        seed_count = await db.execute_fetchall("SELECT COUNT(*) AS c FROM puzzles")
        if int(seed_count[0]["c"]) == 0:
            await db.executemany(
                "INSERT INTO puzzles (title, surface, answer, tags, enabled) VALUES (?, ?, ?, ?, 1)",
                [
                    ("餐厅海龟汤", "一个人走进餐厅点了一碗海龟汤，喝了一口后就自杀了。为什么？", "他曾经在海难中被同伴骗吃了所谓海龟汤，实际是妻子的肉。餐厅真正的海龟汤让他发现真相。", "经典"),
                    ("电梯与雨伞", "男人每天坐电梯到 10 楼，再爬楼梯到 15 楼。下雨天却能直接到 15 楼。为什么？", "他个子矮，只能按到 10 楼；下雨天带伞，可以用伞尖按到 15 楼。", "日常"),
                    ("鱼与鱼缸", "房间里有一具尸体、一滩水和碎玻璃。发生了什么？", "死者是一条鱼，鱼缸碎了，水流出，鱼死了。", "经典"),
                ],
            )
        await db.commit()
    finally:
        await db.close()
