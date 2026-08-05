"""CedarToy adapter for Empty Glass Club.

Based on "空杯俱乐部 / Empty Glass Club" by 西兰花（小红书号 1033358978）.
Original source: https://github.com/dan521627-hash/ai-bar-game

The upstream game is unmodified.  This file only supplies CedarToy identity,
save isolation, version selection, validation, and API packaging.
"""

import fcntl
import json
import math
import os
import re
import tempfile
from pathlib import Path

from .base import (
    MAX_IMPORT_SAVE_BYTES,
    SAVE_ROOT,
    VendorCmdError,
    VendorCmdGame,
    export_json_saves,
    import_json_saves,
    parse_import_save_data,
    require_player_id,
    require_save_confirm,
)


FULL = "full"
LITE = "lite"
UNSELECTED = "unselected"
SELECTION_SCHEMA_VERSION = 1
SELECTION_NAME = "selection.json"
FULL_SAVE_NAME = "bar_save.json"
LITE_SAVE_NAME = "bar_lite_save.json"
SAVE_FILES = {
    SELECTION_NAME: SELECTION_NAME,
    FULL_SAVE_NAME: FULL_SAVE_NAME,
    LITE_SAVE_NAME: LITE_SAVE_NAME,
}
SAVE_TYPES = {name: dict for name in SAVE_FILES}

VERSION_ALIASES = {
    "full": FULL,
    "normal": FULL,
    "完整版": FULL,
    "完整": FULL,
    "lite": LITE,
    "light": LITE,
    "生成式轻量版": LITE,
    "轻量版": LITE,
    "轻量": LITE,
}

# Every non-private top-level function in bar_game_lite.py that is a gameplay
# entry point.  Keep this explicit: never replace it with getattr-based export.
LITE_PUBLIC_FUNCTIONS = (
    "register_creation_direction",
    "draw_creation_direction",
    "register_guest_domain",
    "draw_guest_domain",
    "new_game",
    "summary",
    "define_product",
    "purchase",
    "define_recipe",
    "recipe_profile",
    "register_person",
    "serve",
    "owner_drink",
    "score_drink",
    "quote_decision",
    "stars",
    "record_review",
    "intox_stage",
    "advance_turn",
    "conversation_turn",
    "roll_event",
    "spend",
    "buy_asset",
    "upgrade_asset",
    "record_asset_story",
    "earn",
    "take_loan",
    "repay_loan",
    "close_shift",
    "export_archive",
    "restore_archive",
    "viewer_link",
    "start",
)

ATTRIBUTION = (
    "原作：空杯俱乐部 / Empty Glass Club，作者西兰花（小红书号 1033358978）；"
    "原仓库：https://github.com/dan521627-hash/ai-bar-game。"
    "本站仅做 CedarToy 身份、存档隔离与接口适配，未修改原作玩法。"
    "代码 MIT；原创规则与创意材料 CC BY 4.0。"
)


RUNNER_CODE = r'''
import inspect
import json
import math
import sys
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = Path(payload["save_dir"])
vendor_dir = Path(payload["vendor_dir"])
command = payload["command"]
extra = payload.get("extra") or {}
version = extra.get("version")

sys.dont_write_bytecode = True
sys.path.insert(0, str(vendor_dir))

if version == "full":
    # Only the selected full module is imported in this process.
    import bar_game as game
    game.SAVE_PATH = save_dir / "bar_save.json"

    if command == "new":
        seed = extra.get("new_arguments", {}).get("seed")
        result = game.new_game(seed)
    elif command == "rules":
        result = game._help()
    elif command == "cmd":
        result = game.cmd(extra["game_command"])
    elif command == "summary":
        state = json.loads(game.SAVE_PATH.read_text(encoding="utf-8"))
        result = game._cmd_status(state, [])
    else:
        raise SystemExit("完整版不支持该平台调用")
    print(result, end="")

elif version == "lite":
    # Only the selected lite module is imported in this process.
    import bar_game_lite as game
    game.SAVE_PATH = save_dir / "bar_lite_save.json"

    allowed = {
        "register_creation_direction": game.register_creation_direction,
        "draw_creation_direction": game.draw_creation_direction,
        "register_guest_domain": game.register_guest_domain,
        "draw_guest_domain": game.draw_guest_domain,
        "new_game": game.new_game,
        "summary": game.summary,
        "define_product": game.define_product,
        "purchase": game.purchase,
        "define_recipe": game.define_recipe,
        "recipe_profile": game.recipe_profile,
        "register_person": game.register_person,
        "serve": game.serve,
        "owner_drink": game.owner_drink,
        "score_drink": game.score_drink,
        "quote_decision": game.quote_decision,
        "stars": game.stars,
        "record_review": game.record_review,
        "intox_stage": game.intox_stage,
        "advance_turn": game.advance_turn,
        "conversation_turn": game.conversation_turn,
        "roll_event": game.roll_event,
        "spend": game.spend,
        "buy_asset": game.buy_asset,
        "upgrade_asset": game.upgrade_asset,
        "record_asset_story": game.record_asset_story,
        "earn": game.earn,
        "take_loan": game.take_loan,
        "repay_loan": game.repay_loan,
        "close_shift": game.close_shift,
        "export_archive": game.export_archive,
        "restore_archive": game.restore_archive,
        "viewer_link": game.viewer_link,
        "start": game.start,
    }

    if command == "new":
        kwargs = extra.get("new_arguments") or {}
        bound = inspect.signature(game.new_game).bind(**kwargs)
        result = game.new_game(*bound.args, **bound.kwargs)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), end="")
    elif command == "rules":
        print(game.start(), end="")
    elif command == "summary":
        result = game.summary()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), end="")
    elif command == "call":
        function_name = extra["function"]
        function = allowed[function_name]
        kwargs = extra["arguments"]
        bound = inspect.signature(function).bind(**kwargs)
        result = function(*bound.args, **bound.kwargs)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), end="")
    else:
        raise SystemExit("生成式轻量版不支持该平台调用")
else:
    raise SystemExit("版本必须是 full 或 lite")
'''


GAME = VendorCmdGame("bar", "vendor/ai-bar-game", RUNNER_CODE, timeout=45)


def _save_dir(player_id):
    return SAVE_ROOT / "bar" / require_player_id(player_id)


def _save_path(player_id, version):
    filename = FULL_SAVE_NAME if version == FULL else LITE_SAVE_NAME
    return _save_dir(player_id) / filename


def _selection_path(player_id):
    return _save_dir(player_id) / SELECTION_NAME


def _normalize_version(value, *, required=True):
    if value is None:
        if required:
            raise VendorCmdError("version 必须明确选择 full（完整版）或 lite（生成式轻量版）")
        return None
    if not isinstance(value, str):
        raise VendorCmdError("version 必须是字符串 full 或 lite")
    canonical = VERSION_ALIASES.get(value.strip().lower())
    if canonical is None:
        raise VendorCmdError("version 只支持 full（完整版）或 lite（生成式轻量版）")
    return canonical


def _validate_selection(value):
    if not isinstance(value, dict):
        raise VendorCmdError("selection.json 的内容必须是 JSON 对象")
    if set(value) != {"schema_version", "version"}:
        raise VendorCmdError("selection.json 只能包含 schema_version 和 version")
    if value.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise VendorCmdError("selection.json schema_version 不受支持")
    version = _normalize_version(value.get("version"))
    if version != value.get("version"):
        raise VendorCmdError("selection.json version 必须使用 canonical 值 full 或 lite")
    return version


def _read_selection(player_id):
    path = _selection_path(player_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise VendorCmdError("selection.json 损坏，请用 select 重新选择版本") from None
    return _validate_selection(value)


def _atomic_write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(value, temp_file, ensure_ascii=False, indent=2, allow_nan=False)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _write_selection(player_id, version):
    save_dir = _save_dir(player_id)
    save_dir.mkdir(parents=True, exist_ok=True)
    lock_path = save_dir / ".lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        _atomic_write_json(
            save_dir / SELECTION_NAME,
            {"schema_version": SELECTION_SCHEMA_VERSION, "version": version},
        )


def _save_exists(player_id, version):
    return _save_path(player_id, version).is_file()


def _has_any_game_save(player_id):
    return _save_exists(player_id, FULL) or _save_exists(player_id, LITE)


def _selection_required_text(player_id):
    full_exists = _save_exists(player_id, FULL)
    lite_exists = _save_exists(player_id, LITE)
    return (
        "尚未选择版本，未加载或运行任何游戏模块。\n"
        "完整版（full）：代码提供 244 位人物、168 款核心酒和大量导演流程，开局更稳定，首次读取更重。\n"
        "生成式轻量版（lite）：完整规则与数值引擎内置，由执行 AI 自主创造人物、酒单、商店和剧情，"
        "上下文更小、每家店更独特，也更依赖 AI 持续遵守规则；它不是删减版。\n"
        f"当前槽存档：完整版={'有' if full_exists else '无'}，轻量版={'有' if lite_exists else '无'}。\n"
        '请明确调用 play(game="bar", action="select", params={"version":"full|lite"})，'
        "或在 new 时明确传 version。\n" + ATTRIBUTION
    )


def _version_text(player_id, selected):
    if selected is None:
        return _selection_required_text(player_id)
    return (
        f"当前活动版本：{selected}。完整版存档：{'有' if _save_exists(player_id, FULL) else '无'}；"
        f"生成式轻量版存档：{'有' if _save_exists(player_id, LITE) else '无'}。\n"
        "select 只切换活动版本，不重置或删除任何一版存档。\n" + ATTRIBUTION
    )


def _selected_or_error(player_id):
    selected = _read_selection(player_id)
    if selected is None:
        raise VendorCmdError(_selection_required_text(player_id))
    return selected


def _validate_json_arguments(value):
    if not isinstance(value, dict):
        raise VendorCmdError("arguments 必须是 JSON 对象")
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise VendorCmdError("arguments 必须只包含可序列化的标准 JSON 值") from None
    if len(serialized.encode("utf-8")) > MAX_IMPORT_SAVE_BYTES:
        raise VendorCmdError("arguments 序列化后超过 32MB")
    return value


def _validate_seed(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise VendorCmdError("seed 必须是整数")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d{1,20}", value.strip()):
        return value.strip()
    raise VendorCmdError("seed 必须是整数或整数字符串")


def _validate_lite_number(name, value, *, integer=False):
    if isinstance(value, bool):
        raise VendorCmdError(f"{name} 必须是{'整数' if integer else '有限数字'}")
    if integer:
        if not isinstance(value, int):
            raise VendorCmdError(f"{name} 必须是整数")
        return value
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise VendorCmdError(f"{name} 必须是有限数字")
    return value


def _new_arguments(arguments, version):
    common = {"action", "player_id", "version", "confirm"}
    allowed = common | ({"seed"} if version == FULL else {
        "seed", "cash", "owner_tolerance", "owner_absorption"
    })
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise VendorCmdError("new 包含不支持的参数：" + "、".join(unknown))
    result = {}
    if "seed" in arguments:
        result["seed"] = _validate_seed(arguments.get("seed"))
    if version == LITE:
        if "cash" in arguments:
            result["cash"] = _validate_lite_number("cash", arguments["cash"], integer=True)
        if "owner_tolerance" in arguments:
            result["owner_tolerance"] = _validate_lite_number(
                "owner_tolerance", arguments["owner_tolerance"]
            )
        if "owner_absorption" in arguments:
            result["owner_absorption"] = _validate_lite_number(
                "owner_absorption", arguments["owner_absorption"]
            )
    return result


def _platform_import(player_id, raw):
    archive = parse_import_save_data(raw)
    unknown = sorted(set(archive) - set(SAVE_FILES))
    if unknown:
        raise VendorCmdError("save_data 包含未知存档文件：" + "、".join(unknown))
    if not archive:
        raise VendorCmdError("save_data 存档包不能为空")
    for filename, value in archive.items():
        if not isinstance(value, dict):
            raise VendorCmdError(f"{filename} 的内容必须是 JSON 对象")
    if SELECTION_NAME in archive:
        _validate_selection(archive[SELECTION_NAME])
    else:
        versions = []
        if FULL_SAVE_NAME in archive:
            versions.append(FULL)
        if LITE_SAVE_NAME in archive:
            versions.append(LITE)
        if len(versions) == 1:
            archive = dict(archive)
            archive[SELECTION_NAME] = {
                "schema_version": SELECTION_SCHEMA_VERSION,
                "version": versions[0],
            }
    return import_json_saves(
        "bar",
        player_id,
        archive,
        SAVE_FILES,
        packaged=True,
        expected_types=SAVE_TYPES,
    )


def save_summary(player_id):
    """Read a small platform-safe summary without importing either game."""
    player_id = require_player_id(player_id)
    full_exists = _save_exists(player_id, FULL)
    lite_exists = _save_exists(player_id, LITE)
    if not full_exists and not lite_exists:
        return None
    try:
        selected = _read_selection(player_id)
    except VendorCmdError:
        selected = None
    result = {
        "version": selected or UNSELECTED,
        "full_save": full_exists,
        "lite_save": lite_exists,
    }
    target_version = selected
    if target_version is None and full_exists != lite_exists:
        target_version = FULL if full_exists else LITE
    if target_version is None:
        return result
    try:
        state = json.loads(_save_path(player_id, target_version).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return result
    if not isinstance(state, dict):
        return result
    if target_version == FULL:
        result.update({
            "bar": state.get("bar_name") or "未命名",
            "phase": state.get("phase"),
            "cash": state.get("cash"),
            "reputation": state.get("reputation"),
            "visit": state.get("visit"),
            "turn": state.get("turn"),
        })
    else:
        result.update({
            "cash": state.get("cash"),
            "reputation": state.get("reputation"),
            "turn": state.get("turn"),
            "shift": state.get("shift"),
            "products": len(state.get("products") or {}),
            "recipes": len(state.get("recipes") or {}),
        })
    return result


def play(arguments):
    if not isinstance(arguments, dict):
        raise VendorCmdError("play 参数必须是对象")
    action = str(arguments.get("action") or "version").strip().lower()
    player_id = require_player_id(arguments.get("player_id"))

    if action == "version":
        selected = _read_selection(player_id)
        text = _version_text(player_id, selected)

    elif action == "select":
        version = _normalize_version(arguments.get("version"))
        _write_selection(player_id, version)
        text = (
            f"已选择 {version}；未创建、重置或删除任何游戏存档。\n"
            f"完整版存档：{'有' if _save_exists(player_id, FULL) else '无'}；"
            f"生成式轻量版存档：{'有' if _save_exists(player_id, LITE) else '无'}。\n"
            '下一步可调用 action="rules"；开新局用 action="new"；继续游戏时，'
            f"{('完整版使用 cmd' if version == FULL else '轻量版使用 call')}。"
        )

    elif action == "new":
        explicit = arguments.get("version")
        version = _normalize_version(explicit) if explicit is not None else _read_selection(player_id)
        if version is None:
            raise VendorCmdError(_selection_required_text(player_id))
        require_save_confirm(
            arguments,
            lambda: _save_exists(player_id, version),
            save_summary,
            "空杯俱乐部" + ("完整版" if version == FULL else "生成式轻量版"),
        )
        new_arguments = _new_arguments(arguments, version)
        text = GAME.run(
            player_id,
            "new",
            extra={"version": version, "new_arguments": new_arguments},
        )
        _write_selection(player_id, version)

    elif action == "rules":
        version = _selected_or_error(player_id)
        text = GAME.run(player_id, "rules", extra={"version": version})

    elif action == "cmd":
        version = _selected_or_error(player_id)
        if version != FULL:
            raise VendorCmdError(
                '生成式轻量版没有 cmd 接口；请用 action="call" 调用公开函数，或先 action="rules" 阅读完整规则书'
            )
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填且必须是非空字符串")
        text = GAME.run(
            player_id,
            "cmd",
            extra={"version": FULL, "game_command": command},
        )

    elif action == "call":
        version = _selected_or_error(player_id)
        if version != LITE:
            raise VendorCmdError(
                '完整版没有 call 接口；请用 action="cmd" 原样执行原作命令，或先 action="rules" 查看帮助'
            )
        function = arguments.get("function")
        if not isinstance(function, str) or function not in LITE_PUBLIC_FUNCTIONS:
            raise VendorCmdError("未知或未开放的轻量版 function")
        call_arguments = _validate_json_arguments(arguments.get("arguments"))
        if function == "new_game":
            require_save_confirm(
                arguments,
                lambda: _save_exists(player_id, LITE),
                save_summary,
                "空杯俱乐部生成式轻量版",
            )
        text = GAME.run(
            player_id,
            "call",
            extra={
                "version": LITE,
                "function": function,
                "arguments": call_arguments,
            },
        )

    elif action in {"summary", "status"}:
        version = _selected_or_error(player_id)
        if not _save_exists(player_id, version):
            raise VendorCmdError("当前已选版本还没有存档；请先 action=\"new\"")
        text = GAME.run(player_id, "summary", extra={"version": version})

    elif action == "export":
        text = export_json_saves(
            "bar",
            player_id,
            SAVE_FILES,
            packaged=True,
            expected_types=SAVE_TYPES,
        )
        archive = json.loads(text)
        if SELECTION_NAME in archive:
            _validate_selection(archive[SELECTION_NAME])
        text = json.dumps(archive, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)

    elif action == "import":
        require_save_confirm(
            arguments,
            lambda: _has_any_game_save(player_id),
            save_summary,
            "空杯俱乐部",
        )
        text = _platform_import(player_id, arguments.get("save_data"))
        selected = _read_selection(player_id)
        if selected is None and _save_exists(player_id, FULL) and _save_exists(player_id, LITE):
            text += "；两版存档均已导入但未指定活动版本，请先 action=\"select\" 明确选择"

    else:
        raise VendorCmdError(
            "未知 bar action；支持 version/select/new/rules/cmd/call/summary/export/import"
        )

    selected = _read_selection(player_id)
    return {
        "game": "bar",
        "player_id": player_id,
        "version": selected or UNSELECTED,
        "text": text,
    }
