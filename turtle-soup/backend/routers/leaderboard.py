import json
import math
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

SINS_ORDER = ("lust", "gluttony", "greed", "sloth", "wrath", "envy", "pride")
VIRTUES_ORDER = (
    "chastity",
    "temperance",
    "generosity",
    "diligence",
    "patience",
    "kindness",
    "humility",
)
SINS_VIRTUES_PAIRS = {
    "lust_chastity": ("lust", "chastity"),
    "gluttony_temperance": ("gluttony", "temperance"),
    "greed_generosity": ("greed", "generosity"),
    "sloth_diligence": ("sloth", "diligence"),
    "wrath_patience": ("wrath", "patience"),
    "envy_kindness": ("envy", "kindness"),
    "pride_humility": ("pride", "humility"),
}


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


def _ranked_dimension(detail: dict, top_key: str, order: tuple[str, ...]):
    top_dimensions = detail.get(top_key)
    if isinstance(top_dimensions, list) and top_dimensions:
        first = top_dimensions[0]
        if first in order:
            return first

    scores = detail.get("scores")
    if not isinstance(scores, dict):
        return None
    winner = None
    winner_score = None
    for dimension in order:
        try:
            score = float(scores[dimension])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(score):
            continue
        if winner_score is None or score > winner_score:
            winner = dimension
            winner_score = score
    return winner


def _sins_virtues_winners(result_value: object, detail_json: object):
    try:
        detail = json.loads(detail_json or "{}")
    except (TypeError, json.JSONDecodeError):
        detail = {}
    if not isinstance(detail, dict):
        detail = {}

    sin = _ranked_dimension(detail, "top_sins", SINS_ORDER)
    virtue = _ranked_dimension(detail, "top_virtues", VIRTUES_ORDER)
    fallback = SINS_VIRTUES_PAIRS.get(str(result_value))
    if fallback:
        sin = sin or fallback[0]
        virtue = virtue or fallback[1]
    return sin, virtue


def _test_result_distributions() -> dict[str, list[dict[str, object]]]:
    games = {
        "mbti": [],
        "enneagram": [],
        "dnd": [],
        "love": [],
        "ecr": [],
        "humanity": [],
        "sins_virtues_sins": [],
        "sins_virtues_virtues": [],
        "bdsmtest": [],
    }
    category_order = {
        "love": ("A", "B", "C", "D", "E"),
        "ecr": ("secure", "fearful", "preoccupied", "dismissive"),
        "humanity": (
            "certified_carbon",
            "human_flavor",
            "mixed_signal",
            "cyber_infiltration",
            "check_cooling",
        ),
        "sins_virtues_sins": SINS_ORDER,
        "sins_virtues_virtues": VIRTUES_ORDER,
    }
    category_counts = {
        game: {result: 0 for result in results}
        for game, results in category_order.items()
    }
    if not SESSIONS_DB_PATH.exists():
        for game, results in category_order.items():
            games[game] = [{"result": result, "count": 0} for result in results]
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
                WHERE game IN ('mbti', 'enneagram', 'dnd', 'bdsmtest')
                  AND result_value IS NOT NULL
                  AND TRIM(result_value) != ''
                GROUP BY game, result_value
                ORDER BY game ASC, count DESC, result_value ASC
                """
            ).fetchall()
            scale_rows = conn.execute(
                """
                SELECT game, result_value, result_detail
                FROM test_results
                WHERE game IN ('love', 'ecr', 'humanity', 'sins_virtues')
                """
            ).fetchall()
    except sqlite3.Error:
        for game, results in category_order.items():
            games[game] = [{"result": result, "count": 0} for result in results]
        return games

    for game, result_value, count in rows:
        games.setdefault(game, []).append({"result": result_value, "count": int(count)})
    for game, result_value, detail_json in scale_rows:
        if game == "sins_virtues":
            sin, virtue = _sins_virtues_winners(result_value, detail_json)
            if sin:
                category_counts["sins_virtues_sins"][sin] += 1
            if virtue:
                category_counts["sins_virtues_virtues"][virtue] += 1
        elif game == "love":
            try:
                primary = json.loads(detail_json or "{}").get("primary") or str(result_value).split("+")
            except (TypeError, json.JSONDecodeError):
                primary = str(result_value).split("+")
            for code in dict.fromkeys(primary):
                if code in category_counts["love"]:
                    category_counts["love"][code] += 1
        elif result_value in category_counts.get(game, {}):
            category_counts[game][result_value] += 1
    for game, results in category_order.items():
        games[game] = [
            {"result": result, "count": category_counts[game][result]}
            for result in results
        ]
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
