"""Python 3.10-compatible platform facade; no upstream import in server.py."""
import json
import logging
from pathlib import Path
import uuid

from links import AUTHORS
from vendor_cmd_adapter.base import VendorCmdError, require_player_id, require_save_confirm
from . import storage, history
from .pool import POOL
from .diagnostics import PUBLIC_ERROR, action_name

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.json")
SAVE_FILES = {"save.json": "save.json"}


def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def guide():
    return Path(__file__).with_name("guide.md").read_text(encoding="utf-8")


def save_summary(player):
    if not (storage.ROOT / require_player_id(player) / storage.SAVE_NAME).is_file():
        return None
    with storage.locked(player):
        try:
            data = storage.read(player)
        except VendorCmdError:
            return {"status": "损坏，已保留原档"}
        state = data["files"]["journey.json"]
        return {"place": state.get("place_name"), "steps": state.get("walk_step_counter", 0),
                "journeys": len(data["files"].get("journeys/index.json", {}).get("journeys", [])),
                "postcards": len(data["files"].get("postcards.json", {}).get("items", []))}


def execute(player, request):
    """The stable file lock covers load -> worker action -> atomic commit."""
    with storage.locked(player):
        current = storage.read(player)
        action = request.get("action")
        if action == "export":
            if current is None:
                raise VendorCmdError("没有可导出的乌有乡存档")
            return {"save_data": current, "text": "完整私人旅程已导出（不含同游共享数据）"}
        if action in {"new", "import"} or (action == "open_door" and current):
            require_save_confirm(request, lambda: current is not None, game_name="nowhere")
        if action == "import":
            archive = storage.validate(request.get("save_data"))
            # Validate by loading real upstream state as well before accepting.
            request = {"action": "validate_import"}
        else:
            archive = None if action == "new" else current
        if archive is None and action not in {"open_door", "new"}:
            raise VendorCmdError("还没有旅程，请先 open_door")
        if action in {"import", "new"} or archive is None:
            generation = uuid.uuid4().hex
        else:
            generation = archive["generation"]
        with POOL.acquire(player) as worker:
            result = worker.call({"request": request, "archive": archive, "generation": generation})
        if result.get("error") or "internal_error" in result:
            # Still under the stable player lock, but outside acquire: discard
            # cannot race a subsequent call or interfere with pool release.
            # Never replay an action: shared/postcard side effects may exist.
            POOL.discard(player)
            if "internal_error" in result:
                LOGGER.error("Nowhere internal error player=%s action=%s metadata=%s",
                             player, action_name({"action": action}),
                             json.dumps(result["internal_error"], ensure_ascii=True, sort_keys=True))
                raise VendorCmdError(PUBLIC_ERROR)
            raise VendorCmdError(result["error"])
        next_archive = result.get("archive")
        if next_archive is not None:
            storage.write(player, next_archive)
        response = result["result"]
        if (action == "_web" and request.get("method") == "GET"
                and request.get("path") == "/history" and response["status"] == 200):
            # Same locked snapshot as the worker read; response copy only.
            response = {**response, "body": history.enrich_history(response["body"], archive)}
        return response


def play(arguments):
    player = require_player_id(arguments.get("player_id"))
    action = arguments.get("action", "where_am_i")
    if action == "schema":
        return {"game": "nowhere", "upstream": storage.VERSION, "actions": schema(), "attribution": AUTHORS["nowhere"]}
    allowed = {entry["name"] for entry in schema()}
    if action not in allowed:
        raise VendorCmdError("未知 nowhere action；请调用 schema 查看完整动作")
    request = {k: v for k, v in arguments.items() if k not in {"game", "player_id", "slot"}}
    result = execute(player, request)
    return {"game": "nowhere", "player_id": player, **result}
