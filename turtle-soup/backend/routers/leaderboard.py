import os
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends

from auth_utils import current_player
from database import DB_PATH, fetch_all

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

ROOT_DIR = Path(__file__).resolve().parents[3]
SESSIONS_DB_PATH = Path(os.getenv("SESSIONS_DB", ROOT_DIR / "data" / "sessions.db"))
VENDOR_SAVES_DIR = ROOT_DIR / "data" / "vendor_saves"
STATS_CACHE_TTL = 60

_stats_cache: dict[str, object] = {"expires_at": 0.0, "data": None}


def _table_count(db_path: Path, table: str, where: str = "") -> int:
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if not exists:
                return 0
            row = conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()
            return int(row[0] if row else 0)
    except sqlite3.Error:
        return 0


def _test_result_distributions() -> dict[str, list[dict[str, object]]]:
    games = {"mbti": [], "dnd": [], "bdsmtest": []}
    if not SESSIONS_DB_PATH.exists():
        return games
    try:
        with sqlite3.connect(SESSIONS_DB_PATH) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'test_results'"
            ).fetchone()
            if not exists:
                return games
            rows = conn.execute(
                """
                SELECT game, result_value, COUNT(*) AS count
                FROM test_results
                WHERE game IN ('mbti', 'dnd', 'bdsmtest')
                  AND result_value IS NOT NULL
                  AND TRIM(result_value) != ''
                GROUP BY game, result_value
                ORDER BY game ASC, count DESC, result_value ASC
                """
            ).fetchall()
    except sqlite3.Error:
        return games

    for game, result_value, count in rows:
        games.setdefault(game, []).append({"result": result_value, "count": int(count)})
    return games


def _vendor_save_counts() -> dict[str, int]:
    counts = {}
    for game in ("arcade", "burger", "fishing", "imitator_td", "leek"):
        save_dir = VENDOR_SAVES_DIR / game
        if not save_dir.exists():
            counts[game] = 0
            continue
        counts[game] = sum(1 for item in save_dir.iterdir() if item.is_dir())
    return counts


def _build_platform_stats() -> dict[str, object]:
    return {
        "cache_ttl_seconds": STATS_CACHE_TTL,
        "generated_at": int(time.time()),
        "save_counts": {
            "eco": _table_count(SESSIONS_DB_PATH, "eco_sessions"),
            "ciyuwu": _table_count(SESSIONS_DB_PATH, "ciyuwu_sessions"),
            **_vendor_save_counts(),
            "turtle_soup_accounts": _table_count(DB_PATH, "toy_users", "WHERE deleted_at IS NULL"),
            "turtle_soup_rooms": _table_count(DB_PATH, "rooms"),
        },
        "test_result_distributions": _test_result_distributions(),
    }


@router.get("/platform-stats")
async def platform_stats():
    now = time.time()
    cached = _stats_cache.get("data")
    if cached is not None and now < float(_stats_cache.get("expires_at", 0.0)):
        return cached
    data = _build_platform_stats()
    _stats_cache["data"] = data
    _stats_cache["expires_at"] = now + STATS_CACHE_TTL
    return data


@router.get("/{metric}")
async def leaderboard(metric: str, player: dict = Depends(current_player)):
    del player
    columns = {
        "games": "game_count",
        "wins": "win_count",
        "asks": "ask_count",
        "yes": "ask_count_y",
        "no": "ask_count_n",
    }
    col = columns.get(metric, "game_count")
    return await fetch_all(
        f"""
        SELECT id, COALESCE(NULLIF(TRIM(username), ''), '玩家' || id) AS username, is_ai, {col} AS score
        FROM players
        WHERE is_guest = 0
          AND {col} > 0
        ORDER BY {col} DESC, id ASC
        LIMIT 20
        """
    )
