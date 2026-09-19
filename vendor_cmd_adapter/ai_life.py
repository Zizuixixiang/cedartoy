"""CedarToy thin adapter for racy1501/ai-life-boardgame.

The upstream runtime is intentionally left untouched.  A save contains only the
random seed and actions that the upstream :class:`GameSession` accepted.  Every
request rebuilds the session from those facts while ``VendorCmdGame`` holds the
player/slot file lock, so a process restart cannot change the random sequence or
the pending decision.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .base import (
    ROOT_DIR,
    SAVE_ROOT,
    VendorCmdError,
    VendorCmdGame,
    export_json_saves,
    parse_import_save_data,
    require_player_id,
    require_save_confirm,
)


GAME_ID = "ai_life"
SAVE_NAME = "save.json"
SAVE_FILES = {SAVE_NAME: SAVE_NAME}
SAVE_FORMAT = "cedartoy.ai_life.replay"
SAVE_VERSION = 1
UPSTREAM_COMMIT = "7068acc2cb69089475f70a5a9051d7b61064f131"
VENDOR_DIR = ROOT_DIR / "vendor" / "ai-life-boardgame"
FRONTEND_ROOT = VENDOR_DIR / "frontend"
LICENSE_PATH = VENDOR_DIR / "LICENSE"
MAX_ACTIONS = 4096
MAX_ACTION_BYTES = 128 * 1024
_SNAPSHOT_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
_SNAPSHOT_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: dict[str, Any] | None = None
_CATALOG_CACHE_LOCK = threading.Lock()


RUNNER_CODE = r'''
import copy
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = Path(payload["save_dir"])
vendor_dir = Path(payload["vendor_dir"])
save_path = save_dir / "save.json"
operation = (payload.get("command") or "current_decision").strip()
extra = payload.get("extra") or {}

sys.path.insert(0, str(vendor_dir / "simulation"))
from ailife.runtime import GameSession

SAVE_FORMAT = "cedartoy.ai_life.replay"
SAVE_VERSION = 1
UPSTREAM_COMMIT = "7068acc2cb69089475f70a5a9051d7b61064f131"
MAX_ACTIONS = 4096
MAX_ACTION_BYTES = 128 * 1024
ARCHIVE_KEYS = {
    "format", "version", "upstream_commit", "session_id", "seed",
    "forced_goals", "player_name", "player_emoji", "actions",
    "summary", "created_at", "updated_at",
}


class SaveError(ValueError):
    pass


def reject_json_constant(value):
    raise SaveError("存档含非标准 JSON 常量：%s" % value)


def slim_decision(decision):
    if not isinstance(decision, dict):
        return decision
    canonical = decision.get("remaining_normal_rerolls")
    redundant = {"normal_rerolls_remaining", "normal_rerolls_available"}
    return {
        key: value for key, value in decision.items()
        if key not in redundant or value != canonical
    }


def validate_start_fields(seed, forced_goals, player_name, player_emoji):
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SaveError("seed 必须是整数")
    if forced_goals is not None:
        if (not isinstance(forced_goals, list) or len(forced_goals) != 2
                or any(isinstance(goal, bool) or not isinstance(goal, int)
                       for goal in forced_goals)):
            raise SaveError("forced_goals 必须是两个整数，例如 [1, 2]")
    if not isinstance(player_name, str) or not player_name:
        raise SaveError("player_name 必须是非空字符串")
    if not isinstance(player_emoji, str) or not player_emoji:
        raise SaveError("player_emoji 必须是非空字符串")
    if len(player_name) > 100 or len(player_emoji) > 32:
        raise SaveError("player_name 或 player_emoji 过长")


def validate_archive(raw):
    if not isinstance(raw, dict):
        raise SaveError("存档必须是 JSON 对象")
    if set(raw) != ARCHIVE_KEYS:
        missing = sorted(ARCHIVE_KEYS - set(raw))
        unknown = sorted(set(raw) - ARCHIVE_KEYS)
        details = []
        if missing:
            details.append("缺少字段 " + ", ".join(missing))
        if unknown:
            details.append("包含未知字段 " + ", ".join(unknown))
        raise SaveError("存档结构无效：" + "；".join(details))
    if raw.get("format") != SAVE_FORMAT or raw.get("version") != SAVE_VERSION:
        raise SaveError("存档格式或版本不受支持")
    if raw.get("upstream_commit") != UPSTREAM_COMMIT:
        raise SaveError("存档的上游版本与当前已验证版本不一致")
    try:
        parsed_session_id = str(uuid.UUID(raw.get("session_id")))
    except (TypeError, ValueError, AttributeError):
        raise SaveError("session_id 无效") from None
    if parsed_session_id != raw.get("session_id"):
        raise SaveError("session_id 必须是规范 UUID")
    validate_start_fields(
        raw.get("seed"), raw.get("forced_goals"),
        raw.get("player_name"), raw.get("player_emoji"),
    )
    actions = raw.get("actions")
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise SaveError("actions 必须是长度不超过 %d 的数组" % MAX_ACTIONS)
    for index, item in enumerate(actions):
        if not isinstance(item, dict) or set(item) != {"decision_id", "action"}:
            raise SaveError("actions[%d] 结构无效" % index)
        if (not isinstance(item["decision_id"], str)
                or not item["decision_id"]
                or len(item["decision_id"]) > 256):
            raise SaveError("actions[%d].decision_id 无效" % index)
        if not isinstance(item["action"], dict):
            raise SaveError("actions[%d].action 必须是对象" % index)
        try:
            action_size = len(json.dumps(
                item["action"], ensure_ascii=False, separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise SaveError("actions[%d].action 不是严格 JSON" % index) from None
        if action_size > MAX_ACTION_BYTES:
            raise SaveError("actions[%d].action 超过 128KB" % index)
    if not isinstance(raw.get("summary"), dict):
        raise SaveError("summary 必须是对象")
    if not isinstance(raw.get("created_at"), str) or not raw["created_at"]:
        raise SaveError("created_at 无效")
    if not isinstance(raw.get("updated_at"), str) or not raw["updated_at"]:
        raise SaveError("updated_at 无效")
    return copy.deepcopy(raw)


def replay(raw):
    archive = validate_archive(raw)
    session = GameSession(
        seed=archive["seed"],
        forced_goals=archive["forced_goals"],
        player_name=archive["player_name"],
        player_emoji=archive["player_emoji"],
    )
    for index, item in enumerate(archive["actions"]):
        current = session.current_decision()
        if current.get("decision_id") != item["decision_id"]:
            raise SaveError(
                "动作日志在第 %d 步与当前上游 decision 不一致" % (index + 1)
            )
        result = session.submit_action(item["decision_id"], item["action"])
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise SaveError("动作日志在第 %d 步无法重放" % (index + 1))
        if result.get("accepted_action") != item["action"]:
            raise SaveError("动作日志在第 %d 步接受结果不一致" % (index + 1))
    return archive, session


def decision_summary(decision, action_count):
    summary = {
        "status": "game_over" if decision.get("kind") == "game_over" else "in_progress",
        "kind": decision.get("kind"),
        "action_count": action_count,
    }
    for key in ("turn", "completed_turn", "current_stage", "draft_round"):
        value = decision.get(key)
        if isinstance(value, (int, str)) and not isinstance(value, bool):
            summary[key] = value
    score = decision.get("score")
    if isinstance(score, dict):
        for key in ("total", "total_score", "score"):
            if isinstance(score.get(key), (int, float)) and not isinstance(score.get(key), bool):
                summary["score"] = score[key]
                break
    return summary


def refresh_summary(archive, session):
    decision = session.current_decision()
    archive["summary"] = decision_summary(decision, len(archive["actions"]))
    return decision


def atomic_save(archive):
    save_dir.mkdir(parents=True, exist_ok=True)
    temporary = save_path.with_name(save_path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(archive, handle, ensure_ascii=False, indent=2, allow_nan=False)
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


def load_existing():
    if not save_path.is_file():
        raise SaveError("还没有 AI 人生桌游存档，请先调用 start_game")
    try:
        raw = json.loads(
            save_path.read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
        return replay(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, SaveError,
            TypeError, ValueError, KeyError) as exc:
        stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), time.time_ns())
        backup = save_path.with_name(save_path.name + ".corrupt-" + stamp)
        try:
            os.replace(save_path, backup)
        except OSError:
            raise SaveError("AI 人生桌游存档损坏且无法备份：%s" % exc) from None
        raise SaveError(
            "AI 人生桌游存档损坏或无法按已验证版本重放，已备份为 %s；本次未执行动作"
            % backup.name
        ) from None


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False), end="")


try:
    if operation == "start_game":
        if save_path.is_file() and extra.get("confirm") is not True:
            raise SaveError("已有 ai_life 存档；重开会覆盖，请加 confirm=true")
        seed = extra.get("seed")
        if seed is None:
            seed = secrets.randbits(63)
        forced_goals = extra.get("forced_goals")
        player_name = extra.get("player_name") or "AI玩家"
        player_emoji = extra.get("player_emoji") or "🤖"
        validate_start_fields(seed, forced_goals, player_name, player_emoji)
        session = GameSession(
            seed=seed,
            forced_goals=forced_goals,
            player_name=player_name,
            player_emoji=player_emoji,
        )
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        archive = {
            "format": SAVE_FORMAT,
            "version": SAVE_VERSION,
            "upstream_commit": UPSTREAM_COMMIT,
            "session_id": str(uuid.uuid4()),
            "seed": seed,
            "forced_goals": copy.deepcopy(forced_goals),
            "player_name": player_name,
            "player_emoji": player_emoji,
            "actions": [],
            "summary": {},
            "created_at": now,
            "updated_at": now,
        }
        decision = refresh_summary(archive, session)
        atomic_save(archive)
        emit({"session_id": archive["session_id"], "decision": slim_decision(decision)})
    elif operation == "current_decision":
        archive, session = load_existing()
        emit(slim_decision(session.current_decision()))
    elif operation == "submit_action":
        decision_id = extra.get("decision_id")
        action = extra.get("game_action")
        if not isinstance(decision_id, str) or not decision_id:
            raise SaveError("decision_id 必须是非空字符串")
        if not isinstance(action, dict):
            raise SaveError("game_action 必须是原版动作 JSON 对象")
        archive, session = load_existing()
        duplicate = next(
            (item for item in archive["actions"]
             if item["decision_id"] == decision_id and item["action"] == action),
            None,
        )
        if duplicate is not None:
            emit({
                "ok": True,
                "duplicate": True,
                "accepted_action": copy.deepcopy(action),
                "decision": slim_decision(session.current_decision()),
            })
        else:
            result = session.submit_action(decision_id, action)
            if result.get("ok") is True:
                if len(archive["actions"]) >= MAX_ACTIONS:
                    raise SaveError("动作日志已达到安全上限")
                archive["actions"].append({
                    "decision_id": decision_id,
                    "action": copy.deepcopy(result["accepted_action"]),
                })
                archive["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                archive["summary"] = decision_summary(
                    result["decision"], len(archive["actions"])
                )
                atomic_save(archive)
            if isinstance(result.get("decision"), dict):
                result["decision"] = slim_decision(result["decision"])
            emit(result)
    elif operation == "__snapshot__":
        archive, session = load_existing()
        emit(session.spectator_snapshot())
    elif operation == "__import__":
        if save_path.is_file() and extra.get("confirm") is not True:
            raise SaveError("已有 ai_life 存档；导入会覆盖，请加 confirm=true")
        incoming = extra.get("save_data")
        archive, session = replay(incoming)
        decision = refresh_summary(archive, session)
        archive["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        atomic_save(archive)
        emit({
            "imported": True,
            "session_id": archive["session_id"],
            "decision": slim_decision(decision),
        })
    else:
        raise SaveError("未知 AI 人生桌游 action")
except Exception as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
'''


GAME = VendorCmdGame(
    GAME_ID,
    "vendor/ai-life-boardgame",
    RUNNER_CODE,
    timeout=120,
)


def _save_path(player_id: str) -> Path:
    return SAVE_ROOT / GAME_ID / require_player_id(player_id) / SAVE_NAME


def _has_save(player_id: str) -> bool:
    return _save_path(player_id).is_file()


def _parse_runner_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise VendorCmdError("ai_life 上游返回无法解析") from None
    if not isinstance(value, dict):
        raise VendorCmdError("ai_life 上游返回不是 JSON 对象")
    return value


def _reject_json_constant(value: str):
    raise ValueError(f"非标准 JSON 常量：{value}")


def _validated_submit_fields(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    decision_id = arguments.get("decision_id")
    action = arguments.get("game_action")
    if (
        not isinstance(decision_id, str)
        or not decision_id
        or len(decision_id) > 256
    ):
        raise VendorCmdError("decision_id 必须是长度不超过 256 的非空字符串")
    if not isinstance(action, dict):
        raise VendorCmdError("game_action 必须是原版动作 JSON 对象")
    try:
        size = len(json.dumps(
            action,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise VendorCmdError("game_action 必须是严格 JSON 对象") from None
    if size > MAX_ACTION_BYTES:
        raise VendorCmdError("game_action 序列化后超过 128KB")
    return decision_id, action


def save_summary(player_id: str) -> dict[str, Any] | None:
    try:
        archive = json.loads(
            _save_path(player_id).read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if (
        not isinstance(archive, dict)
        or archive.get("format") != SAVE_FORMAT
        or archive.get("version") != SAVE_VERSION
        or archive.get("upstream_commit") != UPSTREAM_COMMIT
    ):
        return None
    summary = archive.get("summary")
    if not isinstance(summary, dict):
        return None
    result = {
        key: summary[key]
        for key in (
            "status", "kind", "turn", "completed_turn", "current_stage",
            "draft_round", "action_count", "score",
        )
        if key in summary
    }
    result["updated_at"] = archive.get("updated_at")
    return result


def spectator_snapshot(player_id: str) -> dict[str, Any]:
    player_id = require_player_id(player_id)
    save_path = _save_path(player_id)
    try:
        stat = save_path.stat()
    except FileNotFoundError:
        raise VendorCmdError("还没有 AI 人生桌游存档，请先调用 start_game") from None
    signature = (stat.st_mtime_ns, stat.st_size)
    cache_key = str(save_path)
    with _SNAPSHOT_CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature:
            return json.loads(json.dumps(cached[1], ensure_ascii=False))
    snapshot = _parse_runner_json(GAME.run(player_id, "__snapshot__"))
    try:
        final_stat = save_path.stat()
        final_signature = (final_stat.st_mtime_ns, final_stat.st_size)
    except FileNotFoundError:
        return snapshot
    # A move may finish after snapshot generation; never cache old state
    # under the newer save revision, or spectators can miss that move forever.
    if final_signature != signature:
        return snapshot
    with _SNAPSHOT_CACHE_LOCK:
        if len(_SNAPSHOT_CACHE) >= 128:
            _SNAPSHOT_CACHE.pop(next(iter(_SNAPSHOT_CACHE)))
        _SNAPSHOT_CACHE[cache_key] = (final_signature, snapshot)
    return json.loads(json.dumps(snapshot, ensure_ascii=False))


def card_catalog() -> dict[str, Any]:
    global _CATALOG_CACHE
    with _CATALOG_CACHE_LOCK:
        if _CATALOG_CACHE is None:
            code = (
                "import json\n"
                "from ailife.runtime import card_catalog\n"
                "print(json.dumps(card_catalog(), ensure_ascii=False, separators=(',', ':')), end='')\n"
            )
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", code],
                    cwd=str(VENDOR_DIR / "simulation"),
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise VendorCmdError(f"ai_life 卡牌目录读取失败：{exc}") from None
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                raise VendorCmdError(detail or "ai_life 卡牌目录读取失败")
            _CATALOG_CACHE = _parse_runner_json(proc.stdout)
        return json.loads(json.dumps(_CATALOG_CACHE, ensure_ascii=False))


def play(arguments: dict[str, Any]) -> dict[str, Any]:
    action = str(arguments.get("action") or "current_decision").strip()
    player_id = require_player_id(arguments.get("player_id"))

    if action == "start_game":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "ai_life",
        )
        extra = {
            key: arguments.get(key)
            for key in ("seed", "forced_goals", "player_name", "player_emoji")
            if key in arguments
        }
        extra["confirm"] = str(arguments.get("confirm", "")).lower() == "true"
        return _parse_runner_json(GAME.run(player_id, action, extra=extra))

    if action == "current_decision":
        if not _has_save(player_id):
            raise VendorCmdError("还没有 AI 人生桌游存档，请先调用 start_game")
        return _parse_runner_json(GAME.run(player_id, action))

    if action == "submit_action":
        if not _has_save(player_id):
            raise VendorCmdError("还没有 AI 人生桌游存档，请先调用 start_game")
        decision_id, game_action = _validated_submit_fields(arguments)
        return _parse_runner_json(
            GAME.run(
                player_id,
                action,
                extra={
                    "decision_id": decision_id,
                    "game_action": game_action,
                },
            )
        )

    if action == "export":
        return {
            "game": GAME_ID,
            "player_id": player_id,
            "text": export_json_saves(GAME_ID, player_id, SAVE_FILES),
        }

    if action == "import":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "ai_life",
        )
        save_data = parse_import_save_data(arguments.get("save_data"))
        return _parse_runner_json(
            GAME.run(
                player_id,
                "__import__",
                extra={
                    "save_data": save_data,
                    "confirm": str(arguments.get("confirm", "")).lower() == "true",
                },
            )
        )

    raise VendorCmdError(
        "未知 ai_life action；支持 start_game/current_decision/submit_action/export/import"
    )
