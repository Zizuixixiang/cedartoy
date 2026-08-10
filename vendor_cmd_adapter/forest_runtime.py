"""Per-save runtime for 格林童话境遇.

This module runs inside VendorCmdGame's short-lived child process.  The parent
adapter holds the per-player flock for the complete load -> mutate -> save
cycle, so no state is shared between players or commands.
"""

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vendor_cmd_adapter import forest_v3


SAVE_VERSION = 1
SAVE_NAME = "forest_save.json"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
MAX_OBSERVATION_LENGTH = 4000
DAILY_SEMANTIC = "completed_endings_v1"


def _now():
    return datetime.now(CHINA_TZ)


def _fresh_state():
    return {
        "version": SAVE_VERSION,
        "revision": 0,
        "current_line": None,
        "current_scene": None,
        "souvenirs": [],
        "completed_endings": [],
        "observations": {},
        "latest_observation_key": None,
        "participants": {"player": "旅人", "ai": "同行者"},
        "daily": {
            "date": _now().date().isoformat(),
            "count": 0,
            "semantic": DAILY_SEMANTIC,
        },
        "total_lines_started": 0,
        "total_choices": 0,
        "updated_at": _now().isoformat(timespec="seconds"),
    }


def _validated_v3_game(game, vendor_dir):
    """Overlay validated v3 copy while retaining the v2 transition graph."""
    display = forest_v3.load_display_game(vendor_dir, game)
    candidate = deepcopy(game)
    for line_id, display_line in display["lines"].items():
        normalized_line = deepcopy(candidate["lines"][line_id])
        normalized_line.update(
            {
                "title": display_line["title"],
                "emoji": display_line["emoji"],
                "brief": display_line["brief"],
            }
        )

        def normalized_scene(scene):
            result = {
                "text": scene["human_text"],
                "human_text": scene["human_text"],
                "ai_slot": deepcopy(scene["ai_slot"]),
                "options": deepcopy(scene["options"]),
            }
            if scene.get("type") == "ending":
                result["type"] = "ending"
            if scene.get("souvenir") is not None:
                result["souvenir"] = scene["souvenir"]
            return result

        normalized_line["opening"] = normalized_scene(display_line["opening"])
        normalized_line["scenes"] = {
            scene_id: normalized_scene(scene)
            for scene_id, scene in display_line["scenes"].items()
        }
        candidate["lines"][line_id] = normalized_line
    candidate["_forest_data_version"] = "v3-display/v2-runtime"
    candidate["_forest_mapping_issues"] = deepcopy(display["mapping_issues"])
    return candidate


def _has_embedded_ai_slot_prompt(game):
    """Return whether the author data already carries any scene prompt."""
    for line in game.get("lines", {}).values():
        if not isinstance(line, dict):
            continue
        line_scenes = line.get("scenes")
        line_scenes = line_scenes if isinstance(line_scenes, dict) else {}
        scenes = [line.get("opening"), *line_scenes.values()]
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            ai_slot = scene.get("ai_slot")
            if isinstance(ai_slot, dict) and isinstance(ai_slot.get("prompt"), str):
                return True
    return False


def _read_game(vendor_dir):
    path = Path(vendor_dir) / "forest_game_data.json"
    with path.open("r", encoding="utf-8") as source:
        game = json.load(source)
    if not isinstance(game, dict) or not isinstance(game.get("lines"), dict):
        raise ValueError("forest_game_data.json 格式无效")
    if _has_embedded_ai_slot_prompt(game):
        # Newer author releases embed the AI observation copy in the main data.
        # A release may migrate lines incrementally, so one embedded prompt is
        # enough to make the whole file authoritative; filling the remaining
        # gaps from drafts could replace newer author text or transitions.
        game["_forest_data_version"] = "v3-display/v2-runtime"
        return game
    try:
        return _validated_v3_game(game, vendor_dir)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        game["_forest_data_version"] = "v2-runtime/v3-unavailable"
        game["_forest_data_warning"] = f"v3 展示数据校验失败，已回退 v2 文案：{exc}"
        return game


def _validate_state(state, game):
    if not isinstance(state, dict):
        raise ValueError("存档顶层不是 JSON 对象")
    if state.get("version") != SAVE_VERSION:
        raise ValueError(f"不支持的存档版本：{state.get('version')!r}")

    revision = state.setdefault("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("revision 必须是非负整数")
    observations = state.setdefault("observations", {})
    if not isinstance(observations, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or len(value) > MAX_OBSERVATION_LENGTH
        for key, value in observations.items()
    ):
        raise ValueError("observations 格式无效")
    latest_observation_key = state.setdefault(
        "latest_observation_key", next(reversed(observations), None) if observations else None
    )
    if latest_observation_key is not None and (
        not isinstance(latest_observation_key, str)
        or latest_observation_key not in observations
    ):
        raise ValueError("latest_observation_key 格式无效")
    participants = state.setdefault(
        "participants", {"player": "旅人", "ai": "同行者"}
    )
    if not isinstance(participants, dict):
        raise ValueError("participants 格式无效")
    for role, fallback in (("player", "旅人"), ("ai", "同行者")):
        value = participants.setdefault(role, fallback)
        if not isinstance(value, str) or not value.strip() or len(value) > 20:
            raise ValueError(f"participants.{role} 格式无效")

    current_line = state.get("current_line")
    current_scene = state.get("current_scene")
    if current_line is not None and (
        not isinstance(current_line, str) or current_line not in game["lines"]
    ):
        raise ValueError("current_line 无效")
    if current_line is None:
        if current_scene is not None:
            raise ValueError("无当前线时 current_scene 必须为空")
    else:
        valid_scenes = {"opening", *game["lines"][current_line].get("scenes", {})}
        if not isinstance(current_scene, str) or current_scene not in valid_scenes:
            raise ValueError("current_scene 无效")

    for key in ("souvenirs", "completed_endings"):
        values = state.get(key)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"{key} 必须是字符串数组")

    daily = state.get("daily")
    if not isinstance(daily, dict) or not isinstance(daily.get("date"), str):
        raise ValueError("daily 格式无效")
    count = daily.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("daily.count 必须是非负整数")
    semantic = daily.get("semantic")
    if semantic is None:
        # v2.7 and earlier counted starts. Treat that current-day value as zero
        # under the new completed-ending semantics; the next mutation persists it.
        state["daily"] = {
            "date": _now().date().isoformat(),
            "count": 0,
            "semantic": DAILY_SEMANTIC,
        }
    elif semantic != DAILY_SEMANTIC:
        raise ValueError(f"不支持的 daily 计数语义：{semantic!r}")
    for key in ("total_lines_started", "total_choices"):
        value = state.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} 必须是非负整数")
    if not isinstance(state.get("updated_at"), str):
        raise ValueError("updated_at 必须是字符串")
    return state


def _atomic_write(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(state, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _corrupt_backup_path(save_path):
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    candidate = save_path.with_name(f"{save_path.name}.corrupt-{stamp}-{os.getpid()}")
    suffix = 1
    while candidate.exists():
        candidate = save_path.with_name(
            f"{save_path.name}.corrupt-{stamp}-{os.getpid()}-{suffix}"
        )
        suffix += 1
    return candidate


def _load_state(save_path, game):
    if not save_path.exists():
        return _fresh_state(), "", False
    try:
        with save_path.open("r", encoding="utf-8") as source:
            state = json.load(source)
        return _validate_state(state, game), "", True
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        backup_path = _corrupt_backup_path(save_path)
        os.replace(save_path, backup_path)
        state = _fresh_state()
        _atomic_write(save_path, state)
        warning = (
            f"⚠️ 存档告警：原存档损坏，已备份为 {backup_path.name}"
            f"（原因：{exc}），本次已重建新档。"
        )
        return state, warning, True


def _touch(state):
    state["revision"] = state.get("revision", 0) + 1
    state["updated_at"] = _now().isoformat(timespec="seconds")


def _scene_for(line_data, scene_id):
    if scene_id == "opening":
        return line_data.get("opening", {})
    return line_data.get("scenes", {}).get(scene_id, {})


def _daily_for_today(state):
    today = _now().date().isoformat()
    daily = state["daily"]
    if daily["date"] != today:
        daily = {"date": today, "count": 0, "semantic": DAILY_SEMANTIC}
        state["daily"] = daily
    return daily


def _participant_names(state):
    participants = state.get("participants") if isinstance(state, dict) else None
    participants = participants if isinstance(participants, dict) else {}
    return {
        "player": str(participants.get("player") or "旅人"),
        "ai": str(participants.get("ai") or "同行者"),
    }


def _replace_names(text, state):
    names = _participant_names(state)
    return str(text or "").replace("{player}", names["player"]).replace("{ai}", names["ai"])


def _format_scene(scene, scene_id, state):
    if not scene:
        return "场景未找到。"
    text = _replace_names(scene.get("text", ""), state)
    ai_slot = scene.get("ai_slot") if isinstance(scene.get("ai_slot"), dict) else {}
    ai_prompt = _replace_names(ai_slot.get("prompt", ""), state)
    observation = state.get("observations", {}).get(
        f"{state.get('current_line')}:{scene_id}", ""
    )
    if scene.get("type") == "ending":
        souvenir = scene.get("souvenir", "")
        result = f"{text}\n\n结局。"
        if souvenir:
            result += f"\n纪念品：{souvenir}\n-> 用 lines 选新线继续。"
        if ai_prompt:
            result += f"\n\n同行者观察提示：{ai_prompt}"
        if observation:
            result += f"\n同行者已写：{observation}"
        return result
    options = scene.get("options", {})
    if options:
        lines = "\n".join(
            f"  {key}. {_replace_names(value['text'], state)}"
            for key, value in options.items()
        )
        result = (
            f"{text}\n\n选项：\n{lines}\n\n"
            f"> 当前场景ID: `{scene_id}`（下一步调用 choose 并传 option）"
        )
        if ai_prompt:
            result += f"\n\n同行者观察提示：{ai_prompt}"
        if observation:
            result += f"\n同行者已写：{observation}"
        return result
    result = f"{text}\n\n> 当前场景ID: `{scene_id}`"
    if ai_prompt:
        result += f"\n\n同行者观察提示：{ai_prompt}"
    if observation:
        result += f"\n同行者已写：{observation}"
    return result


def _list_lines(game, state=None):
    rows = []
    for line_id, line in sorted(game["lines"].items(), key=lambda item: int(item[0])):
        rows.append(
            f"{line_id}. {line['emoji']} {line['title']} — "
            f"{_replace_names(line['brief'], state or {})}"
        )
    return "\n".join(rows)


def _start(game, state, line_id, save_path):
    line = game["lines"].get(line_id)
    if not line:
        raise ValueError(f"线 {line_id} 不存在。用 lines 查看可选线。")

    daily = _daily_for_today(state)

    anti_addiction = game.get("anti_addiction", {})
    threshold = int(anti_addiction.get("threshold", 3))
    firm_threshold = threshold + 2
    reminder = None
    if daily["count"] >= firm_threshold:
        reminder = anti_addiction.get("firm", {}).get("text", "")
    elif daily["count"] >= threshold:
        reminder = anti_addiction.get("gentle", {}).get("text", "")

    if reminder:
        return f"## 🌲 森林今天的门\n\n{reminder}"

    state["current_line"] = line_id
    state["current_scene"] = "opening"
    state["total_lines_started"] += 1
    _touch(state)
    _atomic_write(save_path, state)
    opening = line["opening"]
    options = "\n".join(
        f"  {key}. {_replace_names(value['text'], state)}"
        for key, value in opening["options"].items()
    )
    result = (
        f"## {line_id}. {line['emoji']} {line['title']}\n\n"
        f"{_replace_names(opening['text'], state)}\n\n"
        f"**选项：**\n{options}\n\n"
        "> 当前场景ID: `opening`（下一步调用 choose 并传 option）"
    )
    ai_prompt = _replace_names((opening.get("ai_slot") or {}).get("prompt", ""), state)
    if ai_prompt:
        result += f"\n\n同行者观察提示：{ai_prompt}"
    return result


def _choose(game, state, option, save_path):
    line_id = state.get("current_line")
    scene_id = state.get("current_scene")
    if not line_id or not scene_id:
        raise ValueError("还没有进入角色线。请先调用 lines，再用 start 选择线号。")
    line = game["lines"][line_id]
    current = _scene_for(line, scene_id)
    if current.get("type") == "ending":
        raise ValueError("这已经是结局了。请用 lines 选新线，再调用 start。")
    options = current.get("options", {})
    choice = option.upper()
    target = options.get(choice, {}).get("target", "")
    if not target:
        raise ValueError(f"选项 {option} 无效。可用：{', '.join(options)}。")
    if target == "free_play":
        text = game.get("free_play", {}).get("text", "你走了自己的路。森林接住了。")
        return text.replace("__return_scene__", scene_id)
    scene = line.get("scenes", {}).get(target, {})
    if not scene:
        raise ValueError("目标场景未找到，游戏数据可能已损坏。")

    state["current_scene"] = target
    state["total_choices"] += 1
    if scene.get("type") == "ending":
        _daily_for_today(state)["count"] += 1
        ending_key = f"{line_id}:{target}"
        if ending_key not in state["completed_endings"]:
            state["completed_endings"].append(ending_key)
        souvenir = scene.get("souvenir", "")
        if souvenir and souvenir not in state["souvenirs"]:
            state["souvenirs"].append(souvenir)
    _touch(state)
    _atomic_write(save_path, state)
    return _format_scene(scene, target, state)


def _status(game, state, has_save):
    if not has_save:
        return "还没有森林存档。先调用 lines 查看角色线，再用 start 进入。"
    items = state["souvenirs"]
    rows = [f"纪念品 {len(items)} 个" + ("：" + "、".join(items) if items else "")]
    if state.get("current_line"):
        line = game["lines"][state["current_line"]]
        rows.append(
            f"当前位置：{state['current_line']}. {line['title']} / {state['current_scene']}"
        )
    else:
        rows.append("当前位置：尚未进入角色线")
    today = _now().date().isoformat()
    daily_count = state["daily"]["count"] if state["daily"]["date"] == today else 0
    rows.append(f"今日完成角色线：{daily_count} 次")
    rows.append(f"累计进入角色线：{state['total_lines_started']} 次")
    rows.append(f"累计选择：{state['total_choices']} 次")
    if len(items) >= 3 and game.get("campfire_scene"):
        rows.append("\n篝火空地已触发！\n" + game["campfire_scene"])
        rows.append("（这里没有选项。你说什么都可以。旅人会记住。）")
    if state.get("current_line") and state.get("current_scene"):
        line = game["lines"][state["current_line"]]
        scene = _scene_for(line, state["current_scene"])
        rows.append("\n--- 当前故事 ---\n" + _format_scene(scene, state["current_scene"], state))
    if game.get("_forest_data_warning"):
        rows.append("\n⚠️ " + game["_forest_data_warning"])
    return "\n".join(rows)


def _set_participants(state, player_name=None, ai_name=None):
    participants = state.setdefault("participants", {"player": "旅人", "ai": "同行者"})
    for role, raw, fallback in (
        ("player", player_name, "旅人"),
        ("ai", ai_name, "同行者"),
    ):
        if raw is None:
            continue
        value = str(raw).strip() or fallback
        if len(value) > 20:
            value = value[:20]
        participants[role] = value


def _set_observation(state, content):
    line_id = state.get("current_line")
    scene_id = state.get("current_scene")
    if not line_id or not scene_id:
        raise ValueError("还没有进入角色线，暂时没有可写观察的场景。")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content 必须是非空字符串")
    content = content.strip()
    if len(content) > MAX_OBSERVATION_LENGTH:
        raise ValueError(f"观察文字不能超过 {MAX_OBSERVATION_LENGTH} 字")
    key = f"{line_id}:{scene_id}"
    state["observations"][key] = content
    state["latest_observation_key"] = key


def _observe(state, content, save_path):
    _set_observation(state, content)
    _touch(state)
    _atomic_write(save_path, state)
    return "同行者的观察已写进当前场景。\n\n" + content


def _web_snapshot(game, state, has_save, notice=""):
    names = _participant_names(state)
    lines = [
        {
            "id": line_id,
            "title": line["title"],
            "emoji": line["emoji"],
            "brief": _replace_names(line["brief"], state),
        }
        for line_id, line in sorted(game["lines"].items(), key=lambda item: int(item[0]))
    ]
    current = None
    line_id = state.get("current_line")
    scene_id = state.get("current_scene")
    if line_id and scene_id:
        line = game["lines"][line_id]
        scene = _scene_for(line, scene_id)
        options = [
            {
                "key": key,
                "text": _replace_names(option["text"], state),
            }
            for key, option in scene.get("options", {}).items()
        ]
        current = {
            "line_id": line_id,
            "line_title": line["title"],
            "line_emoji": line["emoji"],
            "scene_id": scene_id,
            "human_text": _replace_names(scene.get("human_text", scene.get("text", "")), state),
            "ai_prompt": _replace_names(
                (scene.get("ai_slot") or {}).get("prompt", ""), state
            ),
            "observation": state.get("observations", {}).get(f"{line_id}:{scene_id}", ""),
            "options": options,
            "is_ending": scene.get("type") == "ending",
            "souvenir": scene.get("souvenir"),
        }
    latest_key = state.get("latest_observation_key")
    latest_observation = None
    if latest_key and latest_key in state.get("observations", {}):
        latest_line, separator, latest_scene = latest_key.partition(":")
        if separator:
            latest_observation = {
                "line_id": latest_line,
                "scene_id": latest_scene,
                "text": state["observations"][latest_key],
            }
    return {
        "ok": True,
        "has_save": has_save,
        "data_version": game.get("_forest_data_version", "v2.7"),
        "data_warning": game.get("_forest_data_warning", ""),
        "mapping_fallbacks": len(game.get("_forest_mapping_issues", [])),
        "revision": state.get("revision", 0),
        "updated_at": state.get("updated_at"),
        "participants": names,
        "lines": lines,
        "current": current,
        "latest_observation": latest_observation,
        "souvenirs": list(state.get("souvenirs", [])),
        "completed_endings": list(state.get("completed_endings", [])),
        "daily": dict(state.get("daily", {})),
        "total_lines_started": state.get("total_lines_started", 0),
        "total_choices": state.get("total_choices", 0),
        "notice": notice,
    }


def _check_web_revision(state, extra):
    expected = extra.get("expected_revision")
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise ValueError("expected_revision 必须是整数")
    if expected != state.get("revision", 0):
        raise ValueError("FOREST_CONFLICT:存档已被另一端更新，请刷新后再选择。")
    expected_scene = extra.get("expected_scene")
    if expected_scene is not None and expected_scene != state.get("current_scene"):
        raise ValueError("FOREST_CONFLICT:当前场景已经变化，请刷新后再选择。")


def _web_action(game, state, has_save, extra, save_path):
    _check_web_revision(state, extra)
    _set_participants(state, extra.get("player_name"), extra.get("ai_name"))
    web_action = str(extra.get("web_action") or "").strip().lower()
    if web_action == "start":
        line_id = str(extra.get("line") or "").strip()
        notice = _start(game, state, line_id, save_path)
    elif web_action == "choose":
        option = str(extra.get("option") or "").strip()
        if not option:
            raise ValueError("option 参数必填")
        notice = _choose(game, state, option, save_path)
    else:
        raise ValueError("网页动作只支持 start / choose")
    return _web_snapshot(game, state, True, notice=notice)


def _import_state(game, state, save_path):
    state = _validate_state(state, game)
    _touch(state)
    _atomic_write(save_path, state)
    return "存档导入成功"


def run_payload(payload):
    game = _read_game(payload["vendor_dir"])
    save_path = Path(payload["save_dir"]) / SAVE_NAME
    extra = payload.get("extra") or {}
    action = str(extra.get("action") or "status").strip().lower()

    if action == "lines":
        return _list_lines(game)

    state, warning, has_save = _load_state(save_path, game)

    def run_checked(callback):
        try:
            return callback()
        except ValueError as exc:
            if warning:
                raise ValueError(f"{warning}\n{exc}") from None
            raise

    if action in {"new", "reset"}:
        state = _fresh_state()
        _atomic_write(save_path, state)
        text = "新的森林存档已建立。\n\n" + _list_lines(game)
    elif action == "start":
        text = run_checked(
            lambda: _start(game, state, str(extra.get("line") or "").strip(), save_path)
        )
    elif action == "choose":
        option = str(extra.get("option") or "").strip()
        if not option:
            raise ValueError("option 参数必填")
        observation = extra.get("observation")
        if observation is not None:
            run_checked(lambda: _observe(state, observation, save_path))
        text = run_checked(lambda: _choose(game, state, option, save_path))
    elif action == "observe":
        text = run_checked(lambda: _observe(state, extra.get("content"), save_path))
    elif action == "status":
        text = _status(game, state, has_save)
    elif action == "import":
        text = run_checked(lambda: _import_state(game, extra.get("state"), save_path))
    elif action == "web_state":
        _set_participants(state, extra.get("player_name"), extra.get("ai_name"))
        snapshot = _web_snapshot(game, state, has_save)
        if warning:
            snapshot["data_warning"] = warning
        return json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    elif action == "web_action":
        snapshot = run_checked(lambda: _web_action(game, state, has_save, extra, save_path))
        if warning:
            snapshot["data_warning"] = warning
        return json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    else:
        raise ValueError("未知 forest action")

    return f"{warning}\n{text}" if warning else text
