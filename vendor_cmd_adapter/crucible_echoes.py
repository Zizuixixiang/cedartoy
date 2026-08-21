"""CedarToy adapter for the upstream Crucible Echoes agent engine.

The upstream repository stays untouched.  Each invocation runs in a short-lived
process while :class:`VendorCmdGame` holds the per-player file lock.  Only the
compact decision view crosses the MCP boundary; the complete deterministic
GameState, including RNG state, remains in the private JSON save.
"""

from __future__ import annotations

import json
from typing import Any

from .base import (
    SAVE_ROOT,
    VendorCmdError,
    VendorCmdGame,
    export_json_saves,
    require_player_id,
    require_save_confirm,
)


SAVE_NAME = "state.json"
SAVE_FILES = {SAVE_NAME: SAVE_NAME}


RUNNER_CODE = r'''
import json
import os
import sys
import time
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = Path(payload["save_dir"])
vendor_dir = Path(payload["vendor_dir"])
action = (payload.get("command") or "status").strip()
extra = payload.get("extra") or {}
save_path = save_dir / "state.json"

sys.path.insert(0, str(vendor_dir / "src"))

from crucible_echoes.engine import GameEngine, GameError
from crucible_echoes.model import GameState
from crucible_echoes.save import load_game


def atomic_save(state):
    save_dir.mkdir(parents=True, exist_ok=True)
    temporary = save_path.with_suffix(save_path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, save_path)
        directory_fd = os.open(save_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def emit(engine, completed_action, warning=None):
    result = engine.agent_payload(completed_action)
    if warning:
        result["warning"] = warning
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), end="")


def load_or_recover():
    if not save_path.is_file():
        raise GameError("找不到存档；请先调用 new 开局")
    try:
        return GameEngine().bind(load_game(save_path)), None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        stamp = f'{time.strftime("%Y%m%d-%H%M%S")}-{time.time_ns()}'
        corrupt_path = save_path.with_name(f"{save_path.name}.corrupt-{stamp}")
        os.replace(save_path, corrupt_path)
        engine = GameEngine()
        engine.new_game(1, 1)
        atomic_save(engine.s)
        warning = (
            f"原存档损坏，已备份为 {corrupt_path.name}；"
            "本次未执行请求动作，已创建 seed=1、难度=1 的恢复局。"
        )
        return engine, warning


try:
    if action == "new":
        seed = extra.get("seed", 1)
        difficulty = extra.get("difficulty", 1)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise GameError("seed 必须是整数")
        if isinstance(difficulty, bool) or not isinstance(difficulty, int) or not 1 <= difficulty <= 10:
            raise GameError("difficulty 必须是 1-10 的整数")
        engine = GameEngine()
        engine.new_game(seed, difficulty)
        atomic_save(engine.s)
        emit(engine, action)
    elif action == "__import__":
        raw_state = extra.get("save_data")
        if not isinstance(raw_state, dict):
            raise GameError("save_data 必须是 JSON 对象")
        state = GameState.from_dict(raw_state)
        engine = GameEngine().bind(state)
        # Building the public state proves catalog references and runtime
        # defaults are valid before replacing the existing save.
        engine.agent_payload("import")
        atomic_save(engine.s)
        emit(engine, "import")
    else:
        engine, warning = load_or_recover()
        if warning:
            emit(engine, "recovered", warning=warning)
        else:
            if action not in {"status", "state", "inventory", "help"}:
                if action == "spin":
                    engine.spin()
                elif action == "choose":
                    engine.choose(int(extra["index"]))
                elif action == "skip":
                    engine.skip()
                elif action == "reroll":
                    engine.reroll()
                elif action == "remove":
                    engine.remove(int(extra["index"]))
                elif action == "use":
                    engine.use_item(str(extra["item_id"]))
                else:
                    raise GameError(f"未知动作：{action}")
                atomic_save(engine.s)
            emit(engine, "status" if action == "state" else action)
except (GameError, ValueError, IndexError, KeyError, OSError, TypeError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
'''


GAME = VendorCmdGame(
    "crucible_echoes",
    "vendor/crucible-echoes",
    RUNNER_CODE,
    timeout=30,
)


def _save_path(player_id: str):
    return SAVE_ROOT / "crucible_echoes" / require_player_id(player_id) / SAVE_NAME


def _has_save(player_id: str) -> bool:
    return _save_path(player_id).is_file()


def save_summary(player_id: str) -> dict[str, Any] | None:
    try:
        state = json.loads(_save_path(player_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    tokens = state.get("tokens") if isinstance(state.get("tokens"), dict) else {}
    return {
        "turn": state.get("spin", 0),
        "gold": state.get("gold", 0),
        "difficulty": state.get("difficulty"),
        "order": int(state.get("order_index") or 0) + 1,
        "status": state.get("status"),
        "tokens": {
            "roll": int(tokens.get("roll") or 0),
            "remove": int(tokens.get("remove") or 0),
            "essence": int(tokens.get("essence") or 0),
        },
    }


def _definition_summary(definition: Any) -> dict[str, Any]:
    if not isinstance(definition, dict):
        return {}
    return {
        key: definition[key]
        for key in ("id", "name", "rarity", "base", "tags", "description")
        if key in definition
    }


def _compact_ingredient(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    definition = _definition_summary(row.get("definition"))
    return {
        "slot": row.get("slot"),
        "id": row.get("id"),
        "name": definition.get("name"),
        "rarity": definition.get("rarity"),
        "base": definition.get("base"),
        "permanent_bonus": row.get("permanent_bonus", 0),
        "age": row.get("age", 0),
        "description": definition.get("description"),
    }


def _compact_definition_list(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [_definition_summary(row) for row in rows if isinstance(row, dict)]


def _compact_payload(raw: str, player_id: str, requested_action: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise VendorCmdError(f"crucible_echoes 上游返回无法解析：{exc}") from None
    if not isinstance(payload, dict):
        raise VendorCmdError("crucible_echoes 上游返回不是 JSON 对象")

    pending_rows = payload.get("pending_choices")
    current_choice = pending_rows[0] if isinstance(pending_rows, list) and pending_rows else None
    decision = None
    if isinstance(current_choice, dict):
        offers = []
        for offer in current_choice.get("offers") or []:
            if not isinstance(offer, dict):
                continue
            compact_offer = {
                "index": offer.get("index"),
                "id": offer.get("id"),
                **_definition_summary(offer.get("definition")),
            }
            offers.append(compact_offer)
        decision = {
            "kind": current_choice.get("kind"),
            "source": current_choice.get("source"),
            "can_skip": bool(current_choice.get("can_skip")),
            "tag_filter": current_choice.get("tag_filter"),
            "offers": offers,
            "queued_choices": len(pending_rows),
        }

    action_specs = payload.get("available_action_specs")
    actions = action_specs if isinstance(action_specs, list) else []
    state_summary = {
        key: payload.get(key)
        for key in (
            "status",
            "seed",
            "difficulty",
            "spin",
            "gold",
            "order",
            "order_amount",
            "spins_left",
            "pool_size",
            "board_capacity",
            "tokens",
        )
    }

    result: dict[str, Any] = {
        "game": "crucible_echoes",
        "player_id": player_id,
        "protocol": "cedartoy-crucible-echoes/v1",
        "upstream_protocol": payload.get("protocol"),
        "action": payload.get("action") or requested_action,
        "state": state_summary,
        "decision": decision,
        "last_board": payload.get("last_board") if isinstance(payload.get("last_board"), list) else [],
        "last_log": (payload.get("last_log") or [])[-20:],
        "actions": actions,
        "owned_items": _compact_definition_list(payload.get("items_detail")),
        "owned_essences": _compact_definition_list(payload.get("essences_detail")),
    }
    if payload.get("warning"):
        result["warning"] = payload["warning"]

    # Ingredient detail is necessary when the current decision can remove one,
    # and when inventory was explicitly requested.  It is omitted from routine
    # spin/choose responses to keep the MCP result small in long runs.
    if requested_action == "inventory" or any(
        isinstance(spec, dict) and spec.get("action") == "remove" for spec in actions
    ):
        result["ingredients"] = [
            _compact_ingredient(row)
            for row in (payload.get("ingredients") or [])
            if isinstance(row, dict)
        ]
    return result


def _integer_param(arguments: dict[str, Any], name: str) -> int:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise VendorCmdError(f"{name} 必须是整数")
    return value


def play(arguments: dict[str, Any]) -> dict[str, Any]:
    action = str(arguments.get("action") or "state").strip().lower()
    player_id = require_player_id(arguments.get("player_id"))

    if action == "new":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "crucible_echoes",
        )
        seed = arguments.get("seed", 1)
        difficulty = arguments.get("difficulty", 1)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise VendorCmdError("seed 必须是整数")
        if isinstance(difficulty, bool) or not isinstance(difficulty, int) or not 1 <= difficulty <= 10:
            raise VendorCmdError("difficulty 必须是 1-10 的整数")
        raw = GAME.run(player_id, "new", extra={"seed": seed, "difficulty": difficulty})
        return _compact_payload(raw, player_id, action)

    if action in {"state", "status", "spin", "skip", "reroll", "inventory", "help"}:
        raw = GAME.run(player_id, action)
        return _compact_payload(raw, player_id, action)

    if action in {"choose", "remove"}:
        index = _integer_param(arguments, "index")
        if index < 1:
            raise VendorCmdError("index 必须是正整数")
        raw = GAME.run(player_id, action, extra={"index": index})
        return _compact_payload(raw, player_id, action)

    if action == "use":
        item_id = arguments.get("item_id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise VendorCmdError("item_id 必填")
        raw = GAME.run(player_id, action, extra={"item_id": item_id.strip()})
        return _compact_payload(raw, player_id, action)

    if action == "export":
        return {
            "game": "crucible_echoes",
            "player_id": player_id,
            "text": export_json_saves("crucible_echoes", player_id, SAVE_FILES),
        }

    if action == "import":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "crucible_echoes",
        )
        save_data = arguments.get("save_data")
        if isinstance(save_data, str):
            try:
                save_data = json.loads(save_data)
            except (json.JSONDecodeError, ValueError):
                raise VendorCmdError("save_data 不是合法 JSON 字符串") from None
        if not isinstance(save_data, dict):
            raise VendorCmdError("save_data 必须是 JSON 对象或 JSON 字符串")
        serialized = json.dumps(save_data, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > 2 * 1024 * 1024:
            raise VendorCmdError("save_data 序列化后超过 2MB")
        raw = GAME.run(player_id, "__import__", extra={"save_data": save_data})
        return _compact_payload(raw, player_id, action)

    raise VendorCmdError(
        "未知 crucible_echoes action；支持 new/state/spin/choose/skip/reroll/remove/"
        "inventory/use/help/export/import"
    )
