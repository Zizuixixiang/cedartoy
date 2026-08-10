"""Validated v3 display projection over the authoritative forest v2 graph.

The author's v3 drafts are presentation data.  Scene progression, option
targets, endings, souvenirs, and saves continue to be governed by
``forest_game_data.json`` until the drafts become an authoritative release.
"""

import json
from pathlib import Path


LINE_IDS = tuple(str(index) for index in range(1, 12))


def _nodes(line):
    return {"opening": line.get("opening"), **line.get("scenes", {})}


def _read_v3_line(vendor_dir, line_id):
    path = Path(vendor_dir) / f"forest_line{line_id}_v3_draft.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"v3 展示数据无法读取：{path.name}（{exc}）") from None
    if not isinstance(data, dict):
        raise ValueError(f"v3 展示数据格式无效：{path.name}")
    # Line 1 is wrapped as {"1": {...}}; lines 2-11 are direct objects.
    line = data.get(line_id, data)
    if not isinstance(line, dict):
        raise ValueError(f"v3 展示数据格式无效：{path.name}")
    declared = line.get("line")
    if declared is not None and str(declared) != line_id:
        raise ValueError(f"v3 展示数据线号不匹配：{path.name}")
    return line


def load_display_game(vendor_dir, v2_game):
    """Build a safe display graph and return target mismatch diagnostics.

    Option keys are the API contract used by ``choose``.  A v3 option is used
    only when its target equals v2; otherwise both its text and target fall
    back to v2 so the UI never promises a different transition from the one
    that will actually be persisted.
    """
    if not isinstance(v2_game, dict) or not isinstance(v2_game.get("lines"), dict):
        raise ValueError("forest v2 游戏数据格式无效")
    if set(v2_game["lines"]) != set(LINE_IDS):
        raise ValueError("forest v2 角色线必须恰好为 1-11")

    display_lines = {}
    mapping_issues = []
    for line_id in LINE_IDS:
        v2_line = v2_game["lines"][line_id]
        v3_line = _read_v3_line(vendor_dir, line_id)
        if not isinstance(v2_line, dict):
            raise ValueError(f"forest v2 线 {line_id} 格式无效")
        for key in ("title", "emoji"):
            if v3_line.get(key) != v2_line.get(key):
                raise ValueError(f"v3 线 {line_id} 的 {key} 与 v2 不一致")

        v2_nodes = _nodes(v2_line)
        v3_nodes = _nodes(v3_line)
        if set(v3_nodes) != set(v2_nodes):
            missing = sorted(set(v2_nodes) - set(v3_nodes))
            extra = sorted(set(v3_nodes) - set(v2_nodes))
            raise ValueError(
                f"v3 线 {line_id} 场景 ID 与 v2 不一致：missing={missing}, extra={extra}"
            )

        display_nodes = {}
        for scene_id, v2_scene in v2_nodes.items():
            v3_scene = v3_nodes[scene_id]
            if not isinstance(v2_scene, dict) or not isinstance(v3_scene, dict):
                raise ValueError(f"v3 线 {line_id} 场景 {scene_id} 格式无效")
            human_text = v3_scene.get("human_text")
            ai_slot = v3_scene.get("ai_slot")
            if not isinstance(human_text, str) or not isinstance(ai_slot, dict):
                raise ValueError(f"v3 线 {line_id} 场景 {scene_id} 缺少 human_text/ai_slot")
            if not isinstance(ai_slot.get("prompt"), str):
                raise ValueError(f"v3 线 {line_id} 场景 {scene_id} 缺少 ai_slot.prompt")
            if v3_scene.get("type") != v2_scene.get("type"):
                raise ValueError(f"v3 线 {line_id} 场景 {scene_id} 结局类型与 v2 不一致")
            if v3_scene.get("souvenir") != v2_scene.get("souvenir"):
                raise ValueError(f"v3 线 {line_id} 场景 {scene_id} 纪念品与 v2 不一致")

            v2_options = v2_scene.get("options", {})
            v3_options = v3_scene.get("options", {})
            if not isinstance(v2_options, dict) or not isinstance(v3_options, dict):
                raise ValueError(f"v3 线 {line_id} 场景 {scene_id} options 格式无效")
            if set(v3_options) != set(v2_options):
                raise ValueError(f"v3 线 {line_id} 场景 {scene_id} 选项键与 v2 不一致")

            options = {}
            for option_key, v2_option in v2_options.items():
                v3_option = v3_options[option_key]
                if not isinstance(v2_option, dict) or not isinstance(v3_option, dict):
                    raise ValueError(
                        f"v3 线 {line_id} 场景 {scene_id} 选项 {option_key} 格式无效"
                    )
                v2_target = v2_option.get("target")
                v3_target = v3_option.get("target")
                if v2_target != "free_play" and v2_target not in v2_nodes:
                    raise ValueError(
                        f"v2 线 {line_id} 场景 {scene_id} 选项 {option_key} target 无效"
                    )
                if v3_target != "free_play" and v3_target not in v3_nodes:
                    raise ValueError(
                        f"v3 线 {line_id} 场景 {scene_id} 选项 {option_key} target 无效"
                    )
                compatible = v3_target == v2_target
                if not compatible:
                    mapping_issues.append(
                        {
                            "line": line_id,
                            "scene": scene_id,
                            "option": option_key,
                            "v2_target": v2_target,
                            "v3_target": v3_target,
                        }
                    )
                option_text = v3_option.get("text") if compatible else v2_option.get("text")
                if not isinstance(option_text, str):
                    raise ValueError(
                        f"v3 线 {line_id} 场景 {scene_id} 选项 {option_key} text 无效"
                    )
                options[option_key] = {
                    "text": option_text,
                    "target": v2_target,
                    "source": "v3" if compatible else "v2_fallback",
                }

            display_nodes[scene_id] = {
                "id": scene_id,
                "type": v2_scene.get("type", "scene"),
                "souvenir": v2_scene.get("souvenir"),
                "human_text": human_text,
                "ai_slot": {
                    "prompt": ai_slot["prompt"],
                    "free": bool(ai_slot.get("free")),
                },
                "options": options,
            }

        brief = v3_line.get("brief")
        if not isinstance(brief, str):
            raise ValueError(f"v3 线 {line_id} brief 无效")
        display_lines[line_id] = {
            "id": line_id,
            "title": v2_line["title"],
            "emoji": v2_line["emoji"],
            "brief": brief,
            "opening": display_nodes.pop("opening"),
            "scenes": display_nodes,
        }

    return {"lines": display_lines, "mapping_issues": mapping_issues}


def scene_for(display_game, line_id, scene_id):
    line = display_game["lines"][line_id]
    return line["opening"] if scene_id == "opening" else line["scenes"][scene_id]


def replace_roles(text, player_name="旅人", ai_name="同行者"):
    return str(text or "").replace("{player}", player_name).replace("{ai}", ai_name)
