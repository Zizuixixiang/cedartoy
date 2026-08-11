"""Persistent CedarToy runtime for the author's Forest v3 dual-axis game.

The parent adapter holds the per-player lock around this short-lived process,
so the single JSON file is the atomic unit for both the human and AI axes.
"""

import json
import os
import random
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SAVE_VERSION = 2
SAVE_NAME = "forest_save.json"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
MAX_OBSERVATION_LENGTH = 4000
DAILY_SEMANTIC = "completed_endings_v1"
HUMAN_OPTIONS = frozenset({"A", "B", "C"})
AI_OPTIONS = frozenset({"D", "E"})


def _now():
    return datetime.now(CHINA_TZ)


def _fresh_state():
    return {
        "version": SAVE_VERSION,
        "revision": 0,
        "current_line": None,
        "current_scene": None,
        "human_scene": None,
        "ai_scene": None,
        "ai_mode": "following",
        "ai_loop_count": 0,
        "pending_shared": {},
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
        "total_ai_choices": 0,
        "updated_at": _now().isoformat(timespec="seconds"),
    }


def _read_game(vendor_dir):
    path = Path(vendor_dir) / "forest_game_data.json"
    with path.open("r", encoding="utf-8") as source:
        game = json.load(source)
    if not isinstance(game, dict) or not isinstance(game.get("lines"), dict):
        raise ValueError("forest_game_data.json 格式无效")
    game["_forest_data_version"] = "v3.0-dual-axis"
    return game


def _scene_for(line_data, scene_id):
    if scene_id == "opening":
        return line_data.get("opening", {})
    return line_data.get("scenes", {}).get(scene_id, {})


def _valid_scene_ids(line_data):
    return {"opening", *line_data.get("scenes", {})}


def _normalize_legacy_position(state, game):
    """Add dual-axis fields to v1 saves without rewriting them until mutation."""
    state.setdefault("ai_mode", "following")
    state.setdefault("ai_loop_count", 0)
    state.setdefault("pending_shared", {})
    line_id = state.get("current_line")
    old_scene = state.get("current_scene")
    completed = state.get("completed_endings")
    completed = completed if isinstance(completed, list) else []

    if line_id is not None and (
        not isinstance(line_id, str) or line_id not in game["lines"]
    ):
        raise ValueError("current_line 无效")

    if line_id is None:
        if old_scene is not None:
            raise ValueError("无当前线时 current_scene 必须为空")
        state.setdefault("human_scene", None)
        state.setdefault("ai_scene", None)
        return

    valid_scenes = _valid_scene_ids(game["lines"][line_id])
    if not isinstance(old_scene, str) or old_scene not in valid_scenes:
        # v1 endings removed by v3 are historical completed positions, not
        # corruption. Production contains this exact shape.
        if isinstance(old_scene, str) and f"{line_id}:{old_scene}" in completed:
            state["current_line"] = None
            state["current_scene"] = None
            state["human_scene"] = None
            state["ai_scene"] = None
            state["ai_mode"] = "following"
            state["ai_loop_count"] = 0
            state["pending_shared"] = {}
            return
        raise ValueError("current_scene 无效")

    state.setdefault("human_scene", old_scene)
    state.setdefault("ai_scene", old_scene)


def _validate_state(state, game):
    if not isinstance(state, dict):
        raise ValueError("存档顶层不是 JSON 对象")
    version = state.get("version", 1)
    if version not in (1, SAVE_VERSION):
        raise ValueError(f"不支持的存档版本：{version!r}")

    for key in ("souvenirs", "completed_endings"):
        values = state.setdefault(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"{key} 必须是字符串数组")

    _normalize_legacy_position(state, game)
    state["version"] = SAVE_VERSION

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
    latest_key = state.setdefault(
        "latest_observation_key", next(reversed(observations), None) if observations else None
    )
    if latest_key is not None and (
        not isinstance(latest_key, str) or latest_key not in observations
    ):
        raise ValueError("latest_observation_key 格式无效")

    participants = state.setdefault("participants", {"player": "旅人", "ai": "同行者"})
    if not isinstance(participants, dict):
        raise ValueError("participants 格式无效")
    for role, fallback in (("player", "旅人"), ("ai", "同行者")):
        value = participants.setdefault(role, fallback)
        if not isinstance(value, str) or not value.strip() or len(value) > 20:
            raise ValueError(f"participants.{role} 格式无效")

    line_id = state.get("current_line")
    human_scene = state.get("human_scene")
    ai_scene = state.get("ai_scene")
    if line_id is None:
        if human_scene is not None or ai_scene is not None:
            raise ValueError("无当前线时双轴场景必须为空")
    else:
        valid_scenes = _valid_scene_ids(game["lines"][line_id])
        for key, scene_id in (("human_scene", human_scene), ("ai_scene", ai_scene)):
            if not isinstance(scene_id, str) or scene_id not in valid_scenes:
                raise ValueError(f"{key} 无效")

    ai_mode = state.get("ai_mode")
    if ai_mode not in {"following", "shared", "ai_solo"}:
        raise ValueError("ai_mode 无效")
    loop_count = state.get("ai_loop_count")
    if isinstance(loop_count, bool) or not isinstance(loop_count, int) or loop_count < 0:
        raise ValueError("ai_loop_count 必须是非负整数")

    pending = state.get("pending_shared")
    if not isinstance(pending, dict):
        raise ValueError("pending_shared 格式无效")
    for scene_id, choices in pending.items():
        if not isinstance(scene_id, str) or not isinstance(choices, dict):
            raise ValueError("pending_shared 格式无效")
        human_choice = choices.get("human_choice")
        ai_choice = choices.get("ai_choice")
        if human_choice is not None and human_choice not in HUMAN_OPTIONS:
            raise ValueError("pending_shared.human_choice 无效")
        if ai_choice is not None and ai_choice not in AI_OPTIONS:
            raise ValueError("pending_shared.ai_choice 无效")

    daily = state.setdefault(
        "daily", {"date": _now().date().isoformat(), "count": 0, "semantic": DAILY_SEMANTIC}
    )
    if not isinstance(daily, dict) or not isinstance(daily.get("date"), str):
        raise ValueError("daily 格式无效")
    count = daily.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("daily.count 必须是非负整数")
    semantic = daily.get("semantic")
    if semantic is None:
        state["daily"] = {
            "date": _now().date().isoformat(),
            "count": 0,
            "semantic": DAILY_SEMANTIC,
        }
    elif semantic != DAILY_SEMANTIC:
        raise ValueError(f"不支持的 daily 计数语义：{semantic!r}")

    for key in ("total_lines_started", "total_choices", "total_ai_choices"):
        value = state.setdefault(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} 必须是非负整数")
    state.setdefault("updated_at", _now().isoformat(timespec="seconds"))
    if not isinstance(state["updated_at"], str):
        raise ValueError("updated_at 必须是字符串")

    state["current_scene"] = state.get("human_scene")
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
    state["current_scene"] = state.get("human_scene")


def _persist(save_path, state):
    _touch(state)
    _atomic_write(save_path, state)


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
    return (
        str(text or "")
        .replace("{player}", names["player"])
        .replace("{ai_name}", names["ai"])
        .replace("{ai}", names["ai"])
    )


def _public_state_text(scene, state):
    public_state = scene.get("public_state")
    if public_state is None:
        return ""
    if isinstance(public_state, str):
        return _replace_names(public_state, state)
    return json.dumps(public_state, ensure_ascii=False, sort_keys=True)


def _options_block(options, state, allowed, label):
    rows = []
    for key, option in options.items():
        if key in allowed and isinstance(option, dict):
            rows.append(f"  {key}. {_replace_names(option.get('text', ''), state)}")
    return f"\n**{label}**\n" + "\n".join(rows) if rows else ""


def _observation_for(state, line_id, scene_id):
    return state.get("observations", {}).get(f"{line_id}:{scene_id}", "")


def _format_human_scene(scene, scene_id, state):
    if not scene:
        return "场景未找到。"
    text = _replace_names(scene.get("human_text", scene.get("text", "")), state)
    public_state = _public_state_text(scene, state)
    if public_state:
        text += f"\n\n当前公开状态：{public_state}"
    observation = _observation_for(state, state.get("current_line"), scene_id)
    if observation:
        text += f"\n\n同行者已分享：{observation}"
    if scene.get("type") == "ending":
        result = text + "\n\n结局。"
        souvenir = scene.get("souvenir", "")
        if souvenir:
            result += f"\n纪念品：{souvenir}\n-> 用 lines 选新线继续。"
        return result
    result = text + _options_block(scene.get("options", {}), state, HUMAN_OPTIONS, "你的选择：")
    return result + f"\n\n> 当前场景ID: `{scene_id}`"


def _format_ai_scene(scene, scene_id, state):
    if not scene:
        return "场景未找到。"
    mode = scene.get("mode", "legacy")
    if mode == "ai_solo":
        text = _replace_names(scene.get("ai_text", ""), state)
        hidden = _replace_names(scene.get("ai_hidden", ""), state)
        if hidden:
            text += f"\n\n---\n{hidden}"
    else:
        text = _replace_names(scene.get("human_text", scene.get("text", "")), state)

    public_state = _public_state_text(scene, state)
    if public_state:
        text += f"\n\n当前公开状态：{public_state}"
    ai_slot = scene.get("ai_slot")
    if isinstance(ai_slot, dict) and ai_slot.get("prompt"):
        text += f"\n\n> ✦ AI观察提示：{_replace_names(ai_slot['prompt'], state)}"

    if mode == "shared":
        text += _options_block(scene.get("options", {}), state, HUMAN_OPTIONS, "人类的选择：")
        layer = scene.get("ai_layer") if isinstance(scene.get("ai_layer"), dict) else {}
        hidden = _replace_names(layer.get("hidden_info", ""), state)
        if hidden:
            text += f"\n\n> 🌊 暗流：{hidden}"
        text += _options_block(layer.get("options", {}), state, AI_OPTIONS, "AI 的岔路：")
        no_action = _replace_names(layer.get("no_action", ""), state)
        if no_action:
            text += f"\n\n*（或：{no_action}）*"
    elif mode == "ai_solo":
        text += _options_block(scene.get("options", {}), state, AI_OPTIONS, "AI 的岔路：")
        no_action = _replace_names(scene.get("no_action", ""), state)
        if no_action:
            text += f"\n\n*（或：{no_action}）*"
        if scene.get("max_loop"):
            text += f"\n\n> 最多可循环 {scene['max_loop']} 次。"
    elif mode != "merge" and scene.get("type") != "ending":
        text += _options_block(scene.get("options", {}), state, HUMAN_OPTIONS, "人类的选择：")

    observation = _observation_for(state, state.get("current_line"), scene_id)
    if observation:
        text += f"\n\n同行者已分享：{observation}"
    axis = "AI场景ID" if mode == "ai_solo" else "当前场景ID"
    return text + f"\n\n> {axis}: `{scene_id}`"


def _format_memory(memory, state):
    return (
        f"\n\n---\n> 🐚 随机记忆 **{_replace_names(memory.get('id', ''), state)}**"
        f"（{_replace_names(memory.get('title', ''), state)}）：\n\n"
        f"{_replace_names(memory.get('text', ''), state)}\n\n"
        f"> 代价：{_replace_names(memory.get('cost', ''), state)}"
    )


def _list_lines(game, state=None):
    rows = []
    for line_id, line in sorted(game["lines"].items(), key=lambda item: int(item[0])):
        rows.append(
            f"{line_id}. {line['emoji']} {line['title']} — "
            f"{_replace_names(line['brief'], state or {})} ⚡双轴"
        )
    return "\n".join(rows)


def _set_participants(state, player_name=None, ai_name=None):
    participants = state.setdefault("participants", {"player": "旅人", "ai": "同行者"})
    for role, raw, fallback in (("player", player_name, "旅人"), ("ai", ai_name, "同行者")):
        if raw is None:
            continue
        value = str(raw).strip() or fallback
        participants[role] = value[:20]


def _pending_for(state, scene_id):
    return state["pending_shared"].setdefault(
        scene_id, {"human_choice": None, "ai_choice": None}
    )


def _direct_human_target(scene, choice):
    option = scene.get("options", {}).get(choice)
    return option.get("target") if isinstance(option, dict) else None


def _direct_ai_target(scene, choice):
    layer = scene.get("ai_layer")
    layer = layer if isinstance(layer, dict) else {}
    option = layer.get("options", {}).get(choice)
    return option.get("target") if isinstance(option, dict) else None


def _follow_auto_chain(line, target):
    """Return the author scene chain, auto-following ai-return and merge nodes."""
    visited = []
    seen = set()
    while target:
        if target in seen:
            raise ValueError("场景自动跳转形成循环，游戏数据可能已损坏。")
        seen.add(target)
        scene = _scene_for(line, target)
        if not scene:
            raise ValueError("目标场景未找到，游戏数据可能已损坏。")
        visited.append(target)
        mode = scene.get("mode", "legacy")
        if mode == "merge":
            target = scene.get("next") or scene.get("auto_next")
            continue
        if mode == "ai_solo" and not scene.get("options") and scene.get("auto_next"):
            target = scene["auto_next"]
            continue
        break
    return visited


def _set_human_target(state, line, target):
    chain = _follow_auto_chain(line, target)
    state["human_scene"] = chain[-1]
    if state.get("ai_mode") == "following":
        state["ai_scene"] = chain[-1]
    return chain


def _set_ai_target(state, line, target, *, following=False):
    chain = _follow_auto_chain(line, target)
    final_scene = _scene_for(line, chain[-1])
    state["ai_scene"] = chain[-1]
    state["ai_loop_count"] = 0
    if following:
        state["ai_mode"] = "following"
    elif final_scene.get("mode") == "ai_solo":
        state["ai_mode"] = "ai_solo"
    else:
        state["ai_mode"] = "shared"
    return chain


def _complete_ending(state, line_id, ending_id, scene):
    state["human_scene"] = ending_id
    state["ai_scene"] = ending_id
    state["ai_mode"] = "following"
    state["ai_loop_count"] = 0
    state["pending_shared"] = {}
    _daily_for_today(state)["count"] += 1
    ending_key = f"{line_id}:{ending_id}"
    if ending_key not in state["completed_endings"]:
        state["completed_endings"].append(ending_key)
    souvenir = scene.get("souvenir", "")
    if souvenir and souvenir not in state["souvenirs"]:
        state["souvenirs"].append(souvenir)


def _resolve_combo(state, line_id, line, scene_id, scene):
    pending = _pending_for(state, scene_id)
    human_choice = pending.get("human_choice")
    ai_choice = pending.get("ai_choice")
    if not human_choice or not ai_choice:
        return None
    target = scene.get("combo", {}).get(f"{human_choice}+{ai_choice}")
    if not target:
        raise ValueError(f"Combo {human_choice}+{ai_choice} 无匹配。")
    ending = _scene_for(line, target)
    if not ending:
        raise ValueError("Combo 目标场景未找到，游戏数据可能已损坏。")
    if ending.get("type") == "ending":
        _complete_ending(state, line_id, target, ending)
    else:
        if state.get("human_scene") != scene_id:
            # Opening combos select the human branch. The human direct target
            # may already have advanced further down that branch, so follow
            # the persisted human axis instead of rewinding it to the combo's
            # first node.
            state["ai_scene"] = state["human_scene"]
            state["ai_mode"] = "following"
            state["ai_loop_count"] = 0
            target = state["ai_scene"]
        else:
            _set_human_target(state, line, target)
            _set_ai_target(state, line, target, following=True)
        state["pending_shared"].pop(scene_id, None)
    return target


def _anti_addiction_reminder(game, state):
    daily = _daily_for_today(state)
    config = game.get("anti_addiction", {})
    threshold = int(config.get("threshold", 3))
    if daily["count"] >= threshold + 2:
        return config.get("firm", {}).get("text", "")
    if daily["count"] >= threshold:
        return config.get("gentle", {}).get("text", "")
    return ""


def _start(game, state, line_id, save_path, *, private=True):
    line = game["lines"].get(line_id)
    if not line:
        raise ValueError(f"线 {line_id} 不存在。用 lines 查看可选线。")
    reminder = _anti_addiction_reminder(game, state)
    if reminder:
        return f"## 🌲 森林今天的门\n\n{reminder}"

    state["current_line"] = line_id
    state["human_scene"] = "opening"
    state["ai_scene"] = "opening"
    state["ai_mode"] = "shared"
    state["ai_loop_count"] = 0
    state["pending_shared"] = {}
    state["total_lines_started"] += 1
    _persist(save_path, state)
    opening = line["opening"]
    body = _format_ai_scene(opening, "opening", state) if private else _format_human_scene(opening, "opening", state)
    return f"## {line_id}. {line['emoji']} {line['title']}\n\n{body}"


def _choose(game, state, option, save_path, *, private=False):
    choice = option.upper()
    if choice not in HUMAN_OPTIONS:
        raise ValueError("人类 choose 只接受 A/B/C；AI 请使用 ai_choose 选择 D/E。")
    line_id = state.get("current_line")
    scene_id = state.get("human_scene")
    if not line_id or not scene_id:
        raise ValueError("还没有进入角色线。请先调用 lines，再用 start 选择线号。")
    line = game["lines"][line_id]
    scene = _scene_for(line, scene_id)
    if scene.get("type") == "ending":
        raise ValueError("这已经是结局了。请用 lines 选新线，再调用 start。")
    mode = scene.get("mode", "legacy")
    if mode == "ai_solo":
        raise ValueError("这是 AI 独行场景，人类无法在此选择。")
    option_data = scene.get("options", {}).get(choice)
    if not isinstance(option_data, dict):
        available = ", ".join(k for k in scene.get("options", {}) if k in HUMAN_OPTIONS)
        raise ValueError(f"选项 {option} 无效。可用：{available}。")

    target = option_data.get("target")
    if target == "free_play":
        text = game.get("free_play", {}).get("text", "你走了自己的路。森林接住了。")
        return _replace_names(text.replace("__return_scene__", scene_id), state)

    pending = None
    if mode == "shared":
        pending = _pending_for(state, scene_id)
        pending["human_choice"] = choice
        if not target and scene.get("combo"):
            resolved = _resolve_combo(state, line_id, line, scene_id, scene)
            state["total_choices"] += 1
            _persist(save_path, state)
            if resolved:
                ending = _scene_for(line, resolved)
                return (_format_ai_scene if private else _format_human_scene)(ending, resolved, state)
            return f"你的选择「{choice}」已记录。等待同行者的 D/E 选择。"

    if not target:
        raise ValueError(f"选项 {option} 在作者数据中没有目标，无法前进。")
    chain = _set_human_target(state, line, target)
    if mode == "shared" and pending and pending.get("ai_choice") == "E" and not _direct_ai_target(scene, "E"):
        state["ai_mode"] = "following"
        state["ai_scene"] = state["human_scene"]
    if mode == "shared" and pending and pending.get("ai_choice"):
        state["pending_shared"].pop(scene_id, None)
    state["total_choices"] += 1
    _persist(save_path, state)
    final_id = chain[-1]
    final_scene = _scene_for(line, final_id)
    return (_format_ai_scene if private else _format_human_scene)(final_scene, final_id, state)


def _loop_return_target(scene, scene_id):
    for key in ("E", "D"):
        option = scene.get("options", {}).get(key)
        target = option.get("target") if isinstance(option, dict) else None
        if target and target != scene_id:
            return target
    return None


def _ai_choose(game, state, option, save_path, *, supplied_line=None, supplied_scene=None):
    choice = option.upper()
    if choice not in AI_OPTIONS:
        raise ValueError("AI ai_choose 只接受 D/E；人类请使用 choose 选择 A/B/C。")
    line_id = state.get("current_line")
    scene_id = state.get("ai_scene")
    if not line_id or not scene_id:
        raise ValueError("还没有进入角色线。请先用 start 选择线号。")
    if supplied_line is not None and str(supplied_line).strip() != line_id:
        raise ValueError(f"line 与存档不一致；当前线为 {line_id}。")
    if supplied_scene is not None and str(supplied_scene).strip() != scene_id:
        raise ValueError(f"scene_id 与 AI 存档位置不一致；当前 AI 场景为 {scene_id}。")

    line = game["lines"][line_id]
    scene = _scene_for(line, scene_id)
    if scene.get("type") == "ending":
        raise ValueError("这已经是结局了。请用 lines 选新线，再调用 start。")
    mode = scene.get("mode", "legacy")

    if mode == "shared":
        layer = scene.get("ai_layer") if isinstance(scene.get("ai_layer"), dict) else {}
        option_data = layer.get("options", {}).get(choice)
        if not isinstance(option_data, dict):
            raise ValueError("此共享场景不支持 AI 选择。")
        pending = _pending_for(state, scene_id)
        pending["ai_choice"] = choice
        target = option_data.get("target")
        if target:
            chain = _set_ai_target(state, line, target)
            if pending.get("human_choice"):
                state["pending_shared"].pop(scene_id, None)
            final_id = chain[-1]
            final_scene = _scene_for(line, final_id)
            memory = None
            if final_scene.get("mode") == "ai_solo" and final_scene.get("max_loop"):
                state["ai_loop_count"] = 1
                if line.get("memory_pool"):
                    memory = random.choice(line["memory_pool"])
            state["total_ai_choices"] += 1
            _persist(save_path, state)
            result = "\n\n---\n\n".join(
                _format_ai_scene(_scene_for(line, item), item, state) for item in chain
            )
            if memory:
                result += _format_memory(memory, state)
                result += f"\n\n> 已触碰 {state['ai_loop_count']}/{final_scene['max_loop']} 次。"
            return result

        if scene.get("combo"):
            resolved = _resolve_combo(state, line_id, line, scene_id, scene)
            if resolved:
                state["total_ai_choices"] += 1
                _persist(save_path, state)
                return _format_ai_scene(_scene_for(line, resolved), resolved, state)

            # E with no direct target means persistent follow-human. If the
            # human already left this shared scene, catch up to its current
            # axis; otherwise remember E and move together on the human choice.
            if choice == "E":
                state["ai_mode"] = "following"
                if state.get("human_scene") != scene_id:
                    state["ai_scene"] = state["human_scene"]
            state["total_ai_choices"] += 1
            _persist(save_path, state)
            if choice == "E" and state.get("ai_scene") != scene_id:
                caught = state["ai_scene"]
                return "AI 选择 E 并跟随人类轴。\n\n" + _format_ai_scene(
                    _scene_for(line, caught), caught, state
                )
            return f"AI 选择「{choice}」已记录。等待人类的 A/B/C 选择。"

        raise ValueError("此共享场景没有 combo，无法处理 AI 选择。")

    if mode != "ai_solo":
        raise ValueError("当前 AI 位置没有 D/E 选择。")
    option_data = scene.get("options", {}).get(choice)
    target = option_data.get("target") if isinstance(option_data, dict) else None
    if not target:
        available = ", ".join(k for k in scene.get("options", {}) if k in AI_OPTIONS)
        raise ValueError(f"选项 {option} 无效。可用：{available}。")

    max_loop = int(scene.get("max_loop") or 0)
    if target == scene_id and max_loop:
        next_count = state.get("ai_loop_count", 0) + 1
        if next_count >= max_loop:
            target = _loop_return_target(scene, scene_id)
            if not target:
                raise ValueError("AI 循环达到上限，但作者数据没有返回路径。")
            chain = _set_ai_target(state, line, target)
            memory = None
        else:
            chain = [scene_id]
            state["ai_loop_count"] = next_count
            memory = random.choice(line["memory_pool"]) if line.get("memory_pool") else None
    else:
        chain = _set_ai_target(state, line, target)
        final_scene = _scene_for(line, chain[-1])
        memory = None
        if final_scene.get("mode") == "ai_solo" and final_scene.get("max_loop"):
            state["ai_loop_count"] = 1
            if line.get("memory_pool"):
                memory = random.choice(line["memory_pool"])

    state["total_ai_choices"] += 1
    _persist(save_path, state)
    result = "\n\n---\n\n".join(
        _format_ai_scene(_scene_for(line, item), item, state) for item in chain
    )
    final_scene = _scene_for(line, chain[-1])
    if memory:
        result += _format_memory(memory, state)
        result += f"\n\n> 已触碰 {state['ai_loop_count']}/{final_scene['max_loop']} 次。"
    return result


def _set_observation(state, content):
    line_id = state.get("current_line")
    scene_id = state.get("human_scene")
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
    _persist(save_path, state)
    return "同行者的观察已写进当前场景。\n\n" + content.strip()


def _status(game, state, has_save):
    if not has_save:
        return "还没有森林存档。先调用 lines 查看角色线，再用 start 进入。"
    items = state["souvenirs"]
    rows = [f"纪念品 {len(items)} 个" + ("：" + "、".join(items) if items else "")]
    line_id = state.get("current_line")
    if line_id:
        line = game["lines"][line_id]
        rows.append(f"当前线：{line_id}. {line['title']}")
        rows.append(f"人类位置：{state['human_scene']}")
        rows.append(f"AI 位置：{state['ai_scene']}（{state['ai_mode']}）")
        if state.get("ai_loop_count"):
            rows.append(f"AI 循环进度：{state['ai_loop_count']}")
        pending = state.get("pending_shared", {})
        waiting = [
            f"{scene_id}: human={choices.get('human_choice') or '-'}, ai={choices.get('ai_choice') or '-'}"
            for scene_id, choices in pending.items()
            if not choices.get("human_choice") or not choices.get("ai_choice")
        ]
        if waiting:
            rows.append("待组合选择：" + "；".join(waiting))
    else:
        rows.append("当前位置：尚未进入角色线")
    today = _now().date().isoformat()
    daily_count = state["daily"]["count"] if state["daily"]["date"] == today else 0
    rows.extend(
        [
            f"今日完成角色线：{daily_count} 次",
            f"累计进入角色线：{state['total_lines_started']} 次",
            f"累计人类选择：{state['total_choices']} 次",
            f"累计 AI 选择：{state['total_ai_choices']} 次",
        ]
    )
    if len(items) >= 3 and game.get("campfire_scene"):
        rows.append("\n篝火空地已触发！\n" + _replace_names(game["campfire_scene"], state))
        rows.append("（这里没有选项。你说什么都可以。旅人会记住。）")
    if line_id:
        line = game["lines"][line_id]
        human_id = state["human_scene"]
        ai_id = state["ai_scene"]
        rows.append("\n--- 人类故事轴（公开） ---\n" + _format_human_scene(
            _scene_for(line, human_id), human_id, state
        ))
        rows.append("\n--- AI 故事轴（私密） ---\n" + _format_ai_scene(
            _scene_for(line, ai_id), ai_id, state
        ))
    return "\n".join(rows)


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
    scene_id = state.get("human_scene")
    if line_id and scene_id:
        line = game["lines"][line_id]
        scene = _scene_for(line, scene_id)
        ai_scene = _scene_for(line, state.get("ai_scene"))
        ai_round_max = 0
        if ai_scene.get("mode") == "ai_solo":
            ai_round_max = int(ai_scene.get("max_loop") or 0)
        ai_round = None
        if ai_round_max:
            ai_round = min(max(state.get("ai_loop_count", 0), 1), ai_round_max)
        options = [
            {"key": key, "text": _replace_names(option.get("text", ""), state)}
            for key, option in scene.get("options", {}).items()
            if key in HUMAN_OPTIONS and isinstance(option, dict)
        ]
        pending = state.get("pending_shared", {}).get(scene_id, {})
        waiting_for = None
        if scene.get("mode") == "shared" and scene.get("combo"):
            if pending.get("human_choice") and not pending.get("ai_choice"):
                waiting_for = "ai"
            elif pending.get("ai_choice") and not pending.get("human_choice"):
                waiting_for = "human"
            elif state.get("ai_scene") != scene_id:
                waiting_for = "ai"
        current = {
            "line_id": line_id,
            "line_title": line["title"],
            "line_emoji": line["emoji"],
            "scene_id": scene_id,
            "mode": scene.get("mode", "legacy"),
            "human_text": _replace_names(scene.get("human_text", scene.get("text", "")), state),
            "epilogue": _replace_names(scene.get("epilogue", ""), state),
            "public_state": _public_state_text(scene, state),
            "observation": _observation_for(state, line_id, scene_id),
            "options": options,
            "is_ending": scene.get("type") == "ending",
            "souvenir": scene.get("souvenir"),
            "waiting_for": waiting_for,
            "selected_human_choice": pending.get("human_choice"),
            "ai_round": ai_round,
            "ai_round_max": ai_round_max or None,
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
        "data_version": game.get("_forest_data_version", "v3.0-dual-axis"),
        "data_warning": game.get("_forest_data_warning", ""),
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
    if expected_scene is not None and expected_scene != state.get("human_scene"):
        raise ValueError("FOREST_CONFLICT:当前场景已经变化，请刷新后再选择。")


def _web_action(game, state, has_save, extra, save_path):
    _check_web_revision(state, extra)
    _set_participants(state, extra.get("player_name"), extra.get("ai_name"))
    action = str(extra.get("web_action") or "").strip().lower()
    if action == "start":
        notice = _start(game, state, str(extra.get("line") or "").strip(), save_path, private=False)
    elif action == "choose":
        option = str(extra.get("option") or "").strip()
        if not option:
            raise ValueError("option 参数必填")
        notice = _choose(game, state, option, save_path, private=False)
    else:
        raise ValueError("网页动作只支持 start / choose")
    return _web_snapshot(game, state, True, notice=notice)


def _import_state(game, imported, save_path):
    state = _validate_state(imported, game)
    _persist(save_path, state)
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
        text = "新的森林存档已建立。\n\n" + _list_lines(game, state)
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
    elif action == "ai_choose":
        option = str(extra.get("option") or "").strip()
        if not option:
            raise ValueError("option 参数必填")
        text = run_checked(
            lambda: _ai_choose(
                game,
                state,
                option,
                save_path,
                supplied_line=extra.get("line"),
                supplied_scene=extra.get("scene_id"),
            )
        )
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
