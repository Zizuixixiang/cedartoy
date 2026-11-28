"""Read-only homepage viewer for travel-mcp's existing postcard files."""
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote

from vendor_cmd_adapter.travel import SAVE_FILES

from . import player_identity

UPSTREAM = Path(__file__).resolve().parents[1] / "vendor/travel-mcp"
ART_PREFIX = "/api/travel/postcard-art/"
ART_FILENAME = re.compile(r"[\w-]+\.jpg")
PRIVATE = {"Cache-Control": "private, no-store"}


@lru_cache(maxsize=1)
def destination_names():
    catalogue = json.loads((UPSTREAM / "data/destinations.json").read_text(encoding="utf-8"))
    return {dest["id"]: dest["name_zh"] for dest in catalogue}


def art_file(name):
    if not ART_FILENAME.fullmatch(name):
        return None
    path = UPSTREAM / "assets/postcards" / name
    try:
        if not path.is_symlink() and path.is_file():
            return path
    except OSError:
        return None
    return None


def serve_art(handler, path):
    image = art_file(unquote(path[len(ART_PREFIX):]))
    try:
        body = image.read_bytes() if image else None
    except OSError:
        body = None
    if body is None:
        return handler._send_json({"error": "图片不存在"}, status=404)
    handler.send_response(200)
    handler.send_header("Content-Type", "image/jpeg")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Cache-Control", "public, max-age=3600")
    handler.end_headers()
    handler.wfile.write(body)


def read_cards(path):
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(history, list):
        raise ValueError("invalid postcard history")
    names = destination_names()
    cards = []
    for item in reversed(history):
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str)
                                                for key in ("dest_id", "at", "line")):
            raise ValueError("invalid postcard")
        image = item.get("image_url")
        art = None
        if isinstance(image, str) and image.startswith("assets/postcards/"):
            art = art_file(image.removeprefix("assets/postcards/"))
        cards.append({
            "destination": names.get(item["dest_id"], item["dest_id"]),
            "date": item["at"].partition("T")[0],
            "message": item["line"],
            "image": ART_PREFIX + quote(art.name) if art else None,
        })
    return cards


def serve(handler, params, platform):
    try:
        user = platform._current_account(platform._extract_bearer(handler.headers))
    except platform._McpError:
        return handler._send_json({"error": "请重新登录后查看明信片"}, 401, PRIVATE)
    if user["is_ai"]:
        return handler._send_json({"error": "只有人类账号可以查看明信片"}, 403, PRIVATE)
    if set(params) - {"player"} or ("player" in params and len(params["player"]) != 1):
        return handler._send_json({"error": "无效的存档选择"}, 400, PRIVATE)
    requested = params.get("player", [None])[0]
    with platform._db_connect() as conn:
        machines = conn.execute("""
            SELECT ai.id, ai.username FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ? AND ai.is_ai = 1 AND ai.deleted_at IS NULL
                AND ai.deletion_requested_at_epoch IS NULL
            ORDER BY ai.username, ai.id
        """, (user["id"],)).fetchall()

    # Never use a browser-supplied ID as a filesystem path. Enumerate canonical
    # slots for the current household on every request, including list refreshes.
    slots = []
    for owner in [user, *machines]:
        for slot in range(platform.MIN_SAVE_SLOT, player_identity.max_save_slot("travel") + 1):
            player = platform._account_slot_player_id(owner["id"], slot)
            if requested is not None and requested != player:
                continue
            directory = platform.VENDOR_SAVE_ROOT / "travel" / player
            path = directory / "postcards.json"
            if directory.is_symlink() or path.is_symlink():
                if requested is not None:
                    return handler._send_json({"error": "无法读取这个存档"}, 403, PRIVATE)
                continue
            if requested is not None:
                try:
                    cards = read_cards(path)
                except (OSError, ValueError, RecursionError):
                    return handler._send_json({"error": "明信片存档读取失败"}, 500, PRIVATE)
                return handler._send_json({"postcards": cards}, 200, PRIVATE)
            if directory.is_dir() and any(
                not entry.is_symlink() and entry.is_file()
                for entry in map(directory.joinpath, SAVE_FILES)
            ):
                slots.append({"player": player, "owner": "我" if owner is user else owner["username"],
                              "slot": slot})
    if requested is not None:
        return handler._send_json({"error": "无权查看这个存档"}, 403, PRIVATE)
    return handler._send_json({"slots": slots, "machines": [dict(row) for row in machines]}, 200, PRIVATE)
