"""Platform/homepage statistics; dependencies are supplied by the caller.

No database paths or server callbacks are captured at import time.
"""

import json
import os
import re
import sqlite3
import stat
import threading
import time
from collections import OrderedDict
from contextlib import closing

from .errors import _McpError
from .display_titles import RESEARCH_TITLES, DEEPTALK_TITLES
from deeptalk.catalog import ALIASES as DEEPTALK_ALIASES
from research_scales import SCALES


def _turtle_soup_stats(
    conn, user, *,
    table_exists,
):
    if not table_exists(conn, "players"):
        return {
            "game_count": 0,
            "win_count": 0,
            "ask_count": 0,
            "ask_count_y": 0,
            "ask_count_n": 0,
            "ask_count_u": 0,
            "ask_count_p": 0,
        }
    row = conn.execute(
        """
        SELECT game_count, win_count, ask_count, ask_count_y, ask_count_n, ask_count_u, ask_count_p
        FROM players
        WHERE user_id = ? OR (user_id IS NULL AND username = ?)
        ORDER BY user_id = ? DESC
        LIMIT 1
        """,
        (int(user["id"]), user["username"], int(user["id"])),
    ).fetchone()
    if not row:
        return {
            "game_count": 0,
            "win_count": 0,
            "ask_count": 0,
            "ask_count_y": 0,
            "ask_count_n": 0,
            "ask_count_u": 0,
            "ask_count_p": 0,
        }
    return {
        "game_count": int(row["game_count"] or 0),
        "win_count": int(row["win_count"] or 0),
        "ask_count": int(row["ask_count"] or 0),
        "ask_count_y": int(row["ask_count_y"] or 0),
        "ask_count_n": int(row["ask_count_n"] or 0),
        "ask_count_u": int(row["ask_count_u"] or 0),
        "ask_count_p": int(row["ask_count_p"] or 0),
    }


def _test_stats(
    user, *,
    game_player_ids,
    sessions_db_path,
    sessions_db_connect,
):
    player_ids = game_player_ids(user)
    if not player_ids or not sessions_db_path.exists():
        return {game: {"test_count": 0} for game in ("mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest")}
    placeholders = ",".join("?" * len(player_ids))
    counts = {game: 0 for game in ("mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest")}
    with sessions_db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT game, COUNT(*) AS test_count
            FROM test_results
            WHERE player_id IN ({placeholders}) AND game IN ('mbti', 'enneagram', 'dnd', 'love', 'ecr', 'humanity', 'sins_virtues', 'bdsmtest')
            GROUP BY game
            """,
            player_ids,
        ).fetchall()
        for row in rows:
            counts[row["game"]] = int(row["test_count"])
    return {
        "mbti": {"test_count": counts["mbti"]},
        "enneagram": {"test_count": counts["enneagram"]},
        "dnd": {"test_count": counts["dnd"]},
        "love": {"test_count": counts["love"]},
        "ecr": {"test_count": counts["ecr"]},
        "humanity": {"test_count": counts["humanity"]},
        "sins_virtues": {"test_count": counts["sins_virtues"]},
        "bdsmtest": {"test_count": counts["bdsmtest"]},
    }


def _game_overview(
    conn, user, *,
    test_stats,
    turtle_soup_stats,
):
    tests = test_stats(user)
    soup = turtle_soup_stats(conn, user)
    return {
        "turtle_soup": soup,
        "mbti": tests["mbti"],
        "enneagram": tests["enneagram"],
        "dnd": tests["dnd"],
        "love": tests["love"],
        "ecr": tests["ecr"],
        "humanity": tests["humanity"],
        "sins_virtues": tests["sins_virtues"],
        "bdsmtest": tests["bdsmtest"],
    }


def _count_table_rows(
    table_name, *,
    sessions_db_path,
    read_only_connect,
    table_exists,
):
    if not sessions_db_path.exists():
        return 0
    with closing(read_only_connect(sessions_db_path)) as conn:
        if not table_exists(conn, table_name):
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0)


def _count_puzzle_box_saves(
    *,
    missing=0, busy_timeout_ms=2000,
    sessions_db_path,
    read_only_connect,
    table_exists,
):
    if not sessions_db_path.exists():
        return missing
    with closing(read_only_connect(sessions_db_path)) as conn:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        if not table_exists(conn, "puzzle_box_progress"):
            return missing
        return int(conn.execute(
            "SELECT COUNT(DISTINCT ai_user_id) FROM puzzle_box_progress"
        ).fetchone()[0])


def _prefill_puzzle_box_homepage_metric(
    source, *,
    count_puzzle_box_saves,
    logger,
):
    """Seed only this catalog entry; keep the client live-stats refresh intact."""
    marker = '      {\n        id: "puzzle_box",\n'
    placeholder = '        metric: "--",'
    if source.count(marker) != 1:
        return source
    before, _, remaining = source.partition(marker)
    card, end, after = remaining.partition("\n      },")
    if not end or card.count(placeholder) != 1:
        return source
    try:
        count = count_puzzle_box_saves(missing=None, busy_timeout_ms=200)
    except (OSError, sqlite3.Error):
        logger.warning("puzzle_box homepage save count unavailable")
        return source
    if count is None:
        return source
    card = card.replace(placeholder, f'        metric: "{count}",', 1)
    return before + marker + card + end + after


def _sum_ciyuwu_runs(
    *,
    sessions_db_path,
    sessions_db_connect,
    table_exists,
):
    if not sessions_db_path.exists():
        return 0
    total = 0
    with sessions_db_connect() as conn:
        if not table_exists(conn, "ciyuwu_sessions"):
            return 0
        rows = conn.execute("SELECT meta_data FROM ciyuwu_sessions").fetchall()
    for row in rows:
        try:
            meta = json.loads(row["meta_data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            meta = {}
        try:
            total += max(1, int(meta.get("runs") or 1))
        except (TypeError, ValueError):
            total += 1
    return total


def _vendor_save_stats(
    game, *,
    vendor_save_root,
    ai_life_adapter,
    detroit_adapter,
    bar_adapter,
    get_adapter,
):
    root = vendor_save_root / game
    if not root.exists():
        return {"save_count": 0, "file_count": 0}
    player_dirs = [path for path in root.iterdir() if path.is_dir()]
    # ai_life 的锁文件与损坏存档备份都不代表一份可继续的有效存档；
    # 只把经过严格重放校验入口使用的 save.json 计入平台存档数。
    if game == "ai_life":
        player_dirs = [
            path
            for path in player_dirs
            if (path / ai_life_adapter.SAVE_NAME).is_file()
        ]
    elif game == "detroit":
        player_dirs = [path for path in player_dirs if detroit_adapter.has_save(path.name)]
    else:
        adapter = get_adapter(game)
        filenames = tuple(getattr(adapter, "SAVE_FILES", {}).values())
        if game == "bar":
            filenames = (bar_adapter.FULL_SAVE_NAME, bar_adapter.LITE_SAVE_NAME)
        elif game == "workkk":
            filenames = ("game_state.json",)
        elif game == "garden_cat":
            filenames = ("state.json",)
        if filenames:
            player_dirs = [path for path in player_dirs if any((path / name).is_file() for name in filenames)]
        else:
            # A newly integrated game without a save contract is unknown, not 0.
            return {"save_count": None, "file_count": 0}
    file_count = 0
    for path in player_dirs:
        file_count += sum(1 for child in path.iterdir() if child.is_file() and child.name != ".lock")
    return {"save_count": len(player_dirs), "file_count": file_count}


# Cache validated metadata signatures, never private content. Every request
# still opens and checks each file; only changed saves require JSON decoding.
# Bounding the cache per root also prevents a process from retaining old test
# fixtures and transient workspaces indefinitely.
_CRAFT_COUNT_LOCK = threading.Lock()
_CRAFT_FILE_CACHE = OrderedDict()
_CRAFT_FILE_LIMIT = 2 * 1024**2
_CRAFT_BYTE_BUDGET = 64 * 1024**2
_CRAFT_ENTRY_LIMIT = 2048


def _count_shared_craft_saves(*, data_dir, strict=False):
    """Count rooms with real craft progress, not empty visits or participants.

    Read-only and fail-closed: no partial counts on invalid, missing-budget or
    unreadable storage. Atomic replacement changes the inode/mtime signature;
    revalidate only changed JSON files. No player content is retained in RAM.
    """
    root = data_dir / "rooms"
    deadline = time.monotonic() + (0.1 if strict else 2.0)
    if not _CRAFT_COUNT_LOCK.acquire(timeout=0.05 if strict else 2.0):
        return None
    try:
        if root.is_symlink():
            return None
        try:
            entries = os.scandir(root)
        except FileNotFoundError:
            return None if strict else 0
        key = str(root.absolute())
        previous = _CRAFT_FILE_CACHE.get(key, {})
        fresh = {}
        total_bytes = count = 0
        with entries:
            for number, entry in enumerate(entries, 1):
                if number > _CRAFT_ENTRY_LIMIT or time.monotonic() > deadline:
                    return None
                if not re.fullmatch(r"[1-9][0-9]*", entry.name) or not entry.is_dir(follow_symlinks=False):
                    continue
                active = False
                for name, field in (("canvas", "strokes"), ("beads", "boards"), ("gallery", "items")):
                    relative = (entry.name, name)
                    try:
                        fd = os.open(root / entry.name / (name + ".json"),
                                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    except FileNotFoundError:
                        continue
                    with os.fdopen(fd, "rb") as stream:
                        info = os.fstat(stream.fileno())
                        total_bytes += info.st_size
                        if (not stat.S_ISREG(info.st_mode) or info.st_size > _CRAFT_FILE_LIMIT
                                or total_bytes > _CRAFT_BYTE_BUDGET):
                            return None
                        signature = (info.st_dev, info.st_ino, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)
                        cached = previous.get(relative)
                        if cached and cached[0] == signature:
                            file_active = cached[1]
                        else:
                            raw = stream.read(_CRAFT_FILE_LIMIT + 1)
                            if len(raw) > _CRAFT_FILE_LIMIT:
                                return None
                            data = json.loads(raw)
                            if not isinstance(data, dict) or not isinstance(data.get(field), list):
                                return None
                            file_active = False
                            for item in data[field]:
                                if not isinstance(item, dict):
                                    return None
                                if name == "canvas":
                                    if not isinstance(item.get("points"), list) or not isinstance(item.get("actor"), str):
                                        return None
                                    file_active |= bool(item["points"])
                                elif name == "beads":
                                    if (not isinstance(item.get("cells"), dict) or not isinstance(item.get("id"), str)
                                            or item.get("mode") not in ("together", "separate", "gift")):
                                        return None
                                    file_active |= bool(item["cells"])
                                else:
                                    if not isinstance(item.get("id"), str) or not isinstance(item.get("authors"), list):
                                        return None
                                    file_active = True
                        fresh[relative] = (signature, file_active)
                        active |= file_active
                    if time.monotonic() > deadline:
                        return None
                count += int(active)
        # Publish only a fully validated snapshot. Deleted rooms/files disappear
        # from the next cache, so metrics cannot remain stale after deletion.
        _CRAFT_FILE_CACHE[key] = fresh
        _CRAFT_FILE_CACHE.move_to_end(key)
        if len(_CRAFT_FILE_CACHE) > 8:
            _CRAFT_FILE_CACHE.popitem(last=False)
        return count
    except (OSError, ValueError, RecursionError):
        return None
    finally:
        _CRAFT_COUNT_LOCK.release()


def _activity_catalog(
    *,
    index_path,
    ritual_display_name,
    identity_games,
):
    """Reuse homepage names; include MCP-only games without a second UI catalog."""
    source = index_path.read_text(encoding="utf-8")
    block = re.search(r"const games = \[(.*?)\n    \];", source, re.S)
    if block is None:
        raise ValueError("Homepage catalog unavailable")
    names = {}
    for item in re.split(r'\n      \{', block.group(1)):
        game = re.search(r'\bid: "([a-z0-9_]+)"', item)
        name = re.search(r'\bname: "([^"\n]+)"', item)
        if game and name and game.group(1) != "admin":
            names[{"soup": "turtle_soup"}.get(game.group(1), game.group(1))] = name.group(1)
    names.setdefault("tarot", ritual_display_name)
    names.setdefault("bdsmtest", "BDSM倾向测试")
    names.setdefault("autochess", "私人自走棋")
    # These enabled packages are appended with games.push on the homepage.
    names.update({game: RESEARCH_TITLES[game] for game in SCALES})
    names.update({game: DEEPTALK_TITLES[topic] for game, topic in DEEPTALK_ALIASES.items()})
    names["deeptalk"] = "DeepTalk（旧记录未细分）"
    # Translate only Station; retain the author's Memoria brand. The upstream
    # README describes this as a 文字推理游戏, with station-themed chapters.
    if "memoria" in names:
        names["memoria"] = "Memoria 车站"
    for game in identity_games | {"turtle_soup"}:
        names.setdefault(game, game)
    return [{"game": game, "name": name} for game, name in names.items()]


def _public_game_stats(
    *,
    strict=False,
    count_puzzle_box_saves,
    count_table_rows,
    sum_ciyuwu_runs,
    count_saved_tarot_sessions,
    count_shared_craft_saves,
    vendor_save_stats,
    camping_plaza_db_path,
    camping_plaza_save_admin,
):
    stats = {
        "puzzle_box": {
            "metric_label": "存档数",
            "metric": count_puzzle_box_saves(),
        },
        "eco": {
            "metric_label": "存档数",
            "metric": count_table_rows("eco_sessions"),
        },
        "ciyuwu": {
            "metric_label": "对局数",
            "metric": None if strict else sum_ciyuwu_runs(),
            "save_count": count_table_rows("ciyuwu_sessions"),
        },
        "tarot": {
            "metric_label": "存档数",
            "metric": count_saved_tarot_sessions(strict=strict),
        },
        "shared_craft": {
            "metric_label": "存档数",
            "metric": count_shared_craft_saves(strict=strict),
        },
    }
    for game in ("ai_life", "detroit", "arcade", "bar", "burger", "crucible_echoes", "leek", "delve", "travel", "nowhere", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "candle", "market", "workkk", "garden_cat"):
        vendor_stats = vendor_save_stats(game)
        stats[game] = {
            "metric_label": "存档数",
            "metric": vendor_stats["save_count"],
            "file_count": vendor_stats["file_count"],
        }
    camping_count = 0
    if camping_plaza_db_path.is_file():
        try:
            camping_stats = camping_plaza_save_admin("stats", timeout=2 if strict else 20)
            camping_count = int(camping_stats["save_count"])
        except (KeyError, TypeError, ValueError, _McpError):
            camping_count = None if strict else 0
    stats["camping_plaza"] = {
        "metric_label": "存档数",
        "metric": camping_count,
        "file_count": 1 if camping_count else 0,
    }
    return stats
