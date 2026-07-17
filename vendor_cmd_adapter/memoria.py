import json

from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id, require_save_confirm


LEVELS = {
    "1": {
        "title": "蓝玫瑰庄园",
        "dir": "第一关-蓝玫瑰庄园",
        "module": "detective",
        "save": "detective_save.json",
        "heartbeat": "_heartbeat_l1.json",
        "difficulty": False,
    },
    "2": {
        "title": "午夜特快",
        "dir": "第二关-午夜特快",
        "module": "detective_l2",
        "save": "detective_save_l2.json",
        "heartbeat": "_heartbeat_l2.json",
        "difficulty": True,
    },
    "3": {
        "title": "褪色车站",
        "dir": "第三关-褪色车站",
        "module": "detective_l3",
        "save": "detective_save_l3.json",
        "heartbeat": "_heartbeat_l3.json",
        "difficulty": True,
    },
    "4": {
        "title": "循环车站",
        "dir": "第四关-循环车站",
        "module": "detective_l4",
        "save": "detective_save_l4.json",
        "heartbeat": "_heartbeat_l4.json",
        "difficulty": True,
    },
    "5": {
        "title": "档案室终点",
        "dir": "第五关-档案室终点",
        "module": "detective_l5",
        "save": "detective_save_l5.json",
        "heartbeat": None,
        "difficulty": False,
    },
}


RUNNER_CODE = r'''
import importlib
import json
import os
import re
import sys
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = Path(payload["save_dir"])
vendor_dir = Path(payload["vendor_dir"])
command = (payload.get("command") or "status").strip()
command = re.sub(r'[\u3000\u00A0\u2002\u2003\u2009\u200A\uFEFF]+', ' ', command)
extra = payload.get("extra") or {}

levels = {
    "1": {"title": "蓝玫瑰庄园", "dir": "第一关-蓝玫瑰庄园", "module": "detective", "save": "detective_save.json", "heartbeat": "_heartbeat_l1.json", "difficulty": False},
    "2": {"title": "午夜特快", "dir": "第二关-午夜特快", "module": "detective_l2", "save": "detective_save_l2.json", "heartbeat": "_heartbeat_l2.json", "difficulty": True},
    "3": {"title": "褪色车站", "dir": "第三关-褪色车站", "module": "detective_l3", "save": "detective_save_l3.json", "heartbeat": "_heartbeat_l3.json", "difficulty": True},
    "4": {"title": "循环车站", "dir": "第四关-循环车站", "module": "detective_l4", "save": "detective_save_l4.json", "heartbeat": "_heartbeat_l4.json", "difficulty": True},
    "5": {"title": "档案室终点", "dir": "第五关-档案室终点", "module": "detective_l5", "save": "detective_save_l5.json", "heartbeat": None, "difficulty": False},
}

def level_key(raw):
    value = str(raw or "1").strip().lower()
    aliases = {"l1": "1", "level1": "1", "第一关": "1", "蓝玫瑰庄园": "1",
               "l2": "2", "level2": "2", "第二关": "2", "午夜特快": "2",
               "l3": "3", "level3": "3", "第三关": "3", "褪色车站": "3",
               "l4": "4", "level4": "4", "第四关": "4", "循环车站": "4",
               "l5": "5", "level5": "5", "第五关": "5", "档案室终点": "5"}
    value = aliases.get(value, value)
    if value not in levels:
        raise SystemExit("level 必须是 1-5")
    return value

def meta_path():
    return save_dir / "progress.json"

def read_meta():
    try:
        data = json.loads(meta_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def write_meta(meta):
    save_dir.mkdir(parents=True, exist_ok=True)
    meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

def configure_module(mod, level, cfg, level_save_dir):
    save_path = level_save_dir / cfg["save"]
    setattr(mod, "_SAVE_PATH", str(save_path))
    if cfg.get("heartbeat"):
        setattr(mod, "_HEARTBEAT_PATH", str(level_save_dir / cfg["heartbeat"]))
    if level in {"1", "4"} and hasattr(mod, "_dispatch"):
        original_dispatch = mod._dispatch

        def _dispatch_with_backtrack_count_fix(raw):
            # L1 also supports semicolon-chained commands, so patch each dispatch
            # segment instead of wrapping cmd(). All non-backtrack paths go
            # straight to the vendor dispatcher without touching game state.
            parts = raw.split(maxsplit=1) if isinstance(raw, str) else []
            if not parts or parts[0].lower() != "backtrack":
                return original_dispatch(raw)

            state_before = getattr(mod, "_STATE", None)
            if not isinstance(state_before, dict):
                return original_dispatch(raw)
            remaining_before = state_before.get("active_backtracks")
            if not isinstance(remaining_before, int):
                return original_dispatch(raw)
            snapshots_before = state_before.get("_snapshots")
            snapshot_count_before = (
                len(snapshots_before) if isinstance(snapshots_before, list) else None
            )

            text = original_dispatch(raw)

            state_after = getattr(mod, "_STATE", None)
            if not isinstance(state_after, dict):
                return text
            snapshots_after = state_after.get("_snapshots")
            snapshot_count_after = (
                len(snapshots_after) if isinstance(snapshots_after, list) else None
            )
            result_text = text if isinstance(text, str) else ""
            failed = (
                "没有可用的存档点" in result_text
                or "没有剩余的主动回溯次数" in result_text
            )
            snapshot_consumed = (
                snapshot_count_before is not None
                and snapshot_count_after is not None
                and snapshot_count_after < snapshot_count_before
            )
            succeeded = not failed and (
                ("已回溯" in result_text and "存档点" in result_text)
                or snapshot_consumed
            )
            if not failed and not succeeded:
                return text

            expected_remaining = max(0, remaining_before - 1) if succeeded else remaining_before
            if state_after.get("active_backtracks") != expected_remaining:
                state_after["active_backtracks"] = expected_remaining
                # Both engines save inside their dispatcher, before this platform
                # correction runs. Persist the corrected value explicitly.
                if hasattr(mod, "_save"):
                    mod._save()
                if isinstance(text, str):
                    remaining_pattern = (
                        r"(?m)^(剩余主动回溯：)\d+(次)$"
                        if level == "1"
                        else r"(?m)^(剩余回溯次数：)\d+$"
                    )
                    text = re.sub(
                        remaining_pattern,
                        lambda match: (
                            f"{match.group(1)}{expected_remaining}"
                            + (match.group(2) if level == "1" else "")
                        ),
                        text,
                        count=1,
                    )
            return text

        setattr(mod, "_dispatch", _dispatch_with_backtrack_count_fix)
    if level == "5":
        setattr(mod, "_L4_SAVE_PATH", str(save_dir / "level4" / "detective_save_l4.json"))
        if hasattr(mod, "_forgetting_label") and getattr(mod, "fg_label", None) is None:
            class _ForgettingLabelProxy:
                def __init__(self, module):
                    self._module = module

                def __format__(self, spec):
                    try:
                        state = getattr(self._module, "_STATE", None) or {}
                        label = self._module._forgetting_label(state.get("forgetting_level", 0))
                        return format(label, spec)
                    except Exception:
                        return ""

                def __str__(self):
                    return self.__format__("")

            setattr(mod, "fg_label", _ForgettingLabelProxy(mod))
    if hasattr(mod, "_RNG"):
        setattr(mod, "_RNG", None)
    if hasattr(mod, "_HEARTBEAT_HINT"):
        setattr(mod, "_HEARTBEAT_HINT", None)
    return save_path

def import_level(level):
    cfg = levels[level]
    level_dir = vendor_dir / cfg["dir"]
    sys.path.insert(0, str(level_dir))
    mod = importlib.import_module(cfg["module"])
    level_save_dir = save_dir / f"level{level}"
    level_save_dir.mkdir(parents=True, exist_ok=True)
    save_path = configure_module(mod, level, cfg, level_save_dir)
    return cfg, mod, save_path

def load_existing_state(mod):
    if hasattr(mod, "_STATE"):
        setattr(mod, "_STATE", None)
    if hasattr(mod, "_RNG"):
        setattr(mod, "_RNG", None)
    if hasattr(mod, "_auto_load"):
        mod._auto_load()
    elif hasattr(mod, "_load"):
        mod._load()
    if getattr(mod, "_STATE", None) is None:
        if hasattr(mod, "new_game"):
            try:
                mod.new_game()
            except TypeError:
                mod.new_game("normal")
            if hasattr(mod, "_save"):
                try:
                    mod._save()
                except Exception:
                    pass
        elif hasattr(mod, "_init_state"):
            try:
                setattr(mod, "_STATE", mod._init_state())
            except TypeError:
                setattr(mod, "_STATE", mod._init_state("normal"))

level = level_key(extra.get("level") or read_meta().get("current_level"))
cfg, mod, save_path = import_level(level)

if payload.get("reset"):
    for pattern in (cfg["save"], cfg.get("heartbeat")):
        if not pattern:
            continue
        try:
            (save_path.parent / pattern).unlink()
        except FileNotFoundError:
            pass
    if hasattr(mod, "_STATE"):
        setattr(mod, "_STATE", {})
else:
    load_existing_state(mod)

meta = read_meta()
meta["current_level"] = level
meta.setdefault("levels", {}).setdefault(level, {})["title"] = cfg["title"]

if payload.get("reset"):
    difficulty = str(extra.get("difficulty") or "normal").strip().lower()
    if cfg["difficulty"]:
        text = mod.new_game(difficulty)
    else:
        text = mod.new_game()
    if hasattr(mod, "_save"):
        try:
            mod._save()
        except Exception:
            pass
else:
    text = mod.cmd(command)

meta["levels"][level]["has_save"] = save_path.exists()
write_meta(meta)
print(text, end="")
'''


GAME = VendorCmdGame("memoria", "vendor/Memoria-Station", RUNNER_CODE)


def _normalize_level(value):
    value = str(value or "1").strip().lower()
    aliases = {
        "l1": "1", "level1": "1", "第一关": "1", "蓝玫瑰庄园": "1",
        "l2": "2", "level2": "2", "第二关": "2", "午夜特快": "2",
        "l3": "3", "level3": "3", "第三关": "3", "褪色车站": "3",
        "l4": "4", "level4": "4", "第四关": "4", "循环车站": "4",
        "l5": "5", "level5": "5", "第五关": "5", "档案室终点": "5",
    }
    value = aliases.get(value, value)
    if value not in LEVELS:
        raise VendorCmdError("level 必须是 1-5")
    return value


def save_summary(player_id):
    root = SAVE_ROOT / "memoria" / require_player_id(player_id)
    progress_path = root / "progress.json"
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        progress = {}
    levels = {}
    for level, cfg in LEVELS.items():
        path = root / f"level{level}" / cfg["save"]
        if path.exists():
            levels[level] = {"title": cfg["title"], "has_save": True}
    if not levels:
        return None
    return {
        "current_level": progress.get("current_level"),
        "levels": levels,
    }


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    requested_level = arguments.get("level") or arguments.get("chapter")
    extra = {}
    if action in {"new", "memoria_new"}:
        level = _normalize_level(requested_level)
        extra["level"] = level

        def _memoria_has_save():
            root = SAVE_ROOT / "memoria" / require_player_id(player_id)
            return (root / f"level{level}" / LEVELS[level]["save"]).exists()
        require_save_confirm(arguments, _memoria_has_save, save_summary, "memoria")
        difficulty = str(arguments.get("difficulty") or "normal").strip().lower()
        if difficulty not in {"normal", "hard", "hell"}:
            raise VendorCmdError("difficulty 支持 normal/hard/hell")
        extra["difficulty"] = difficulty
        text = GAME.run(player_id, "status", reset=True, extra=extra)
    elif action in {"cmd", "memoria_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        if requested_level:
            level = _normalize_level(requested_level)
            extra["level"] = level
        else:
            level = None
        text = GAME.run(player_id, command, extra=extra)
        if level is None:
            root = SAVE_ROOT / "memoria" / require_player_id(player_id)
            try:
                progress = json.loads((root / "progress.json").read_text(encoding="utf-8"))
                if isinstance(progress, dict):
                    level = progress.get("current_level")
            except (OSError, json.JSONDecodeError, ValueError):
                pass
    elif action in {"levels", "memoria_levels"}:
        level = _normalize_level(requested_level)
        lines = ["Memoria Station 关卡："]
        for key, cfg in LEVELS.items():
            lines.append(f"{key}. {cfg['title']}")
        text = "\n".join(lines)
    else:
        raise VendorCmdError("未知 memoria action")
    return {"game": "memoria", "player_id": player_id, "level": level, "text": text}
