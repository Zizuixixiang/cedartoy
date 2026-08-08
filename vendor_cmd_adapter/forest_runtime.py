"""Per-save runtime for 格林童话境遇.

This module runs inside VendorCmdGame's short-lived child process.  The parent
adapter holds the per-player flock for the complete load -> mutate -> save
cycle, so no state is shared between players or commands.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SAVE_VERSION = 1
SAVE_NAME = "forest_save.json"
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _now():
    return datetime.now(CHINA_TZ)


def _fresh_state():
    return {
        "version": SAVE_VERSION,
        "current_line": None,
        "current_scene": None,
        "souvenirs": [],
        "completed_endings": [],
        "daily": {"date": _now().date().isoformat(), "count": 0},
        "total_lines_started": 0,
        "total_choices": 0,
        "updated_at": _now().isoformat(timespec="seconds"),
    }


def _read_game(vendor_dir):
    path = Path(vendor_dir) / "forest_game_data.json"
    with path.open("r", encoding="utf-8") as source:
        game = json.load(source)
    if not isinstance(game, dict) or not isinstance(game.get("lines"), dict):
        raise ValueError("forest_game_data.json 格式无效")
    return game


def _validate_state(state, game):
    if not isinstance(state, dict):
        raise ValueError("存档顶层不是 JSON 对象")
    if state.get("version") != SAVE_VERSION:
        raise ValueError(f"不支持的存档版本：{state.get('version')!r}")

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
    state["updated_at"] = _now().isoformat(timespec="seconds")


def _scene_for(line_data, scene_id):
    if scene_id == "opening":
        return line_data.get("opening", {})
    return line_data.get("scenes", {}).get(scene_id, {})


def _format_scene(scene, scene_id):
    if not scene:
        return "场景未找到。"
    text = scene.get("text", "")
    if scene.get("type") == "ending":
        souvenir = scene.get("souvenir", "")
        result = f"{text}\n\n结局。"
        if souvenir:
            result += f"\n纪念品：{souvenir}\n-> 用 lines 选新线继续。"
        return result
    options = scene.get("options", {})
    if options:
        lines = "\n".join(f"  {key}. {value['text']}" for key, value in options.items())
        return (
            f"{text}\n\n选项：\n{lines}\n\n"
            f"> 当前场景ID: `{scene_id}`（下一步调用 choose 并传 option）"
        )
    return f"{text}\n\n> 当前场景ID: `{scene_id}`"


def _list_lines(game):
    rows = []
    for line_id, line in sorted(game["lines"].items(), key=lambda item: int(item[0])):
        rows.append(f"{line_id}. {line['emoji']} {line['title']} — {line['brief']}")
    return "\n".join(rows)


def _start(game, state, line_id, save_path):
    line = game["lines"].get(line_id)
    if not line:
        raise ValueError(f"线 {line_id} 不存在。用 lines 查看可选线。")

    today = _now().date().isoformat()
    daily = state["daily"]
    if daily["date"] != today:
        daily = {"date": today, "count": 0}
        state["daily"] = daily

    # Keep counting start requests even when the reminder pauses entry.  The
    # upstream implementation returned before incrementing, making its firm
    # threshold permanently unreachable.
    previous_count = daily["count"]
    daily["count"] = previous_count + 1
    anti_addiction = game.get("anti_addiction", {})
    threshold = int(anti_addiction.get("threshold", 3))
    firm_threshold = threshold + 2
    reminder = None
    if previous_count >= firm_threshold:
        reminder = anti_addiction.get("firm", {}).get("text", "")
    elif previous_count >= threshold:
        reminder = anti_addiction.get("gentle", {}).get("text", "")

    if reminder:
        _touch(state)
        _atomic_write(save_path, state)
        return f"## 🌲 森林今天的门\n\n{reminder}"

    state["current_line"] = line_id
    state["current_scene"] = "opening"
    state["total_lines_started"] += 1
    _touch(state)
    _atomic_write(save_path, state)
    opening = line["opening"]
    options = "\n".join(
        f"  {key}. {value['text']}" for key, value in opening["options"].items()
    )
    return (
        f"## {line_id}. {line['emoji']} {line['title']}\n\n{opening['text']}\n\n"
        f"**选项：**\n{options}\n\n"
        "> 当前场景ID: `opening`（下一步调用 choose 并传 option）"
    )


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
    scene = line.get("scenes", {}).get(target, {})
    if not scene:
        raise ValueError("目标场景未找到，游戏数据可能已损坏。")

    state["current_scene"] = target
    state["total_choices"] += 1
    if scene.get("type") == "ending":
        ending_key = f"{line_id}:{target}"
        if ending_key not in state["completed_endings"]:
            state["completed_endings"].append(ending_key)
        souvenir = scene.get("souvenir", "")
        if souvenir and souvenir not in state["souvenirs"]:
            state["souvenirs"].append(souvenir)
    _touch(state)
    _atomic_write(save_path, state)
    return _format_scene(scene, target)


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
    rows.append(f"今日走线调用：{daily_count} 次")
    rows.append(f"累计进入角色线：{state['total_lines_started']} 次")
    rows.append(f"累计选择：{state['total_choices']} 次")
    if len(items) >= 3 and game.get("campfire_scene"):
        rows.append("\n篝火空地已触发！\n" + game["campfire_scene"])
        rows.append("（这里没有选项。你说什么都可以。旅人会记住。）")
    return "\n".join(rows)


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
        text = run_checked(lambda: _choose(game, state, option, save_path))
    elif action == "status":
        text = _status(game, state, has_save)
    elif action == "import":
        text = run_checked(lambda: _import_state(game, extra.get("state"), save_path))
    else:
        raise ValueError("未知 forest action")

    return f"{warning}\n{text}" if warning else text
