"""Platform game action forwarding.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _play_mbti(arguments, *,
    _McpError,
    handle_mbti_mcp,
):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"mbti-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"mbti_start", "mbti_answer", "mbti_answer_batch", "mbti_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 MBTI action")
    return handle_mbti_mcp(payload)


def _play_dnd(arguments, *,
    _McpError,
    handle_dnd_mcp,
):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"dnd-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"dnd_start", "dnd_answer", "dnd_answer_batch", "dnd_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 DND action")
    return handle_dnd_mcp(payload)


def _play_scale(handler, game, arguments, *,
    _McpError,
):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"{game}-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {
        f"{game}_start",
        f"{game}_answer",
        f"{game}_answer_batch",
        f"{game}_get_result",
        f"{game}_compare",
    }:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": action,
                "arguments": {key: value for key, value in extra.items() if value is not None},
            },
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, f"未知 {game} action")
    return handler.handle_mcp(payload)


def _play_bdsmtest(arguments, *,
    _McpError,
    handle_bdsmtest_mcp,
):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"bdsmtest-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"bdsmtest_start", "bdsmtest_answer", "bdsmtest_answer_batch", "bdsmtest_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 BDSMTest action")
    return handle_bdsmtest_mcp(payload)


def _play_eco(arguments, *,
    _McpError,
    handle_eco_mcp,
):
    # action（顶层）= 路由到哪个 eco 工具；子参数（含同名的 action，如 summon）放在 params 里。
    # 先取顶层路由 action，再把 params 内容并入 extra，避免被 merge 覆盖。
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    request_id = extra.pop("id", None) or f"eco-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"eco_new", "eco_observe", "eco_act", "eco_info", "eco_save", "eco_play"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 eco action")
    return handle_eco_mcp(payload)


def _play_ciyuwu(arguments, *,
    _McpError,
    handle_ciyuwu_mcp,
):
    # 顶层 action = 路由到哪个 ciyuwu 工具；子参数（含同名 action，如 status）放 params 里。
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    request_id = extra.pop("id", None) or f"ciyuwu-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"ciyuwu_new", "ciyuwu_cmd", "ciyuwu_info", "ciyuwu_save"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 ciyuwu action。本游戏使用专用接口：ciyuwu_new / ciyuwu_cmd / ciyuwu_info / ciyuwu_save，请先 get_guide(game=\"ciyuwu\") 查看用法。")
    return handle_ciyuwu_mcp(payload)


def _parse_json_import_save_data(raw, *,
    VendorCmdError,
    _McpError,
    parse_import_save_data,
):
    try:
        return parse_import_save_data(raw)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc)) from exc


def _play_workkk(arguments, *,
    WORKKK_BASE,
    _McpError,
    _parse_json_import_save_data,
    _reported_player_id,
    _workkk_save_admin,
    _workkk_save_summary,
    httpx,
    json,
):
    # 顶层 action = 路由到哪个 workkk 工具或 MCP 方法；子参数（含同名子 action）放 params 里。
    # 参考海龟汤 SOUP_BASE 那套转发：JSON-RPC 打到独立进程 8770 的 /mcp，身份走 X-Player-Id。
    action = arguments.get("action")
    player_id = _reported_player_id(arguments)
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params", "player_id"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update({key: value for key, value in params.items() if key != "player_id"})

    if action == "export":
        result = _workkk_save_admin("export", player_id=player_id)
        save_data = result.get("save_data")
        if not isinstance(save_data, dict):
            raise _McpError(-32603, "workkk 存档管理服务未返回 JSON 对象存档")
        return {
            "game": "workkk",
            "player_id": player_id,
            "text": json.dumps(save_data, ensure_ascii=False, indent=2),
        }
    if action == "import":
        save_data = _parse_json_import_save_data(extra.get("save_data"))
        confirm = extra.get("confirm") is True
        if _workkk_save_summary(player_id) is not None and not confirm:
            raise _McpError(-32602, "workkk 当前槽已有存档；确认覆盖请在 params 传 confirm=true")
        result = _workkk_save_admin(
            "import",
            player_id=player_id,
            save_data=save_data,
            confirm=confirm,
        )
        if result.get("imported") is not True:
            raise _McpError(-32603, "workkk 存档管理服务未确认导入成功")
        return {"game": "workkk", "player_id": player_id, "text": "存档已导入。"}

    request_id = extra.pop("id", None) or f"workkk-{action or 'call'}"
    if action in {"initialize", "tools/list", "ping"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"work_action", "shop_buy"}:
        # 按 workkk 后端函数签名白名单过滤：后端 fn(**args) 严格解包，
        # 多余字段（如 kelivo 增强 schema 诱导模型生成的 command 等）会直接炸。
        _workkk_allowed = {
            "work_action": {"action", "thought"},
            "shop_buy": {"item_id", "message", "choice"},
        }[action]
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None and key in _workkk_allowed}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 workkk action。本游戏使用专用接口：work_action / shop_buy，请先 get_guide(game=\"workkk\") 查看用法。")
    headers = {"X-Player-Id": player_id} if isinstance(player_id, str) and player_id else {}
    try:
        resp = httpx.post(f"{WORKKK_BASE}/mcp", json=payload, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"workkk 后端连接失败：{exc}")
    if resp.status_code >= 400:
        raise _McpError(-32602, f"workkk 后端错误 HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        return resp.json()
    except ValueError:
        raise _McpError(-32603, "workkk 后端返回非 JSON 响应")


def _tarot_bound_human_user_id(ai_user, *,
    _McpError,
    _db_connect,
):
    """Resolve the exact active human in the current machine binding."""
    if not ai_user or not ai_user.get("is_ai"):
        raise _McpError(-32001, "tarot MCP 动作仅供已认证的小机账号使用。")
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT human.id
            FROM user_bindings b
            JOIN toy_users human ON human.id = b.human_user_id
            WHERE b.ai_user_id = ?
              AND human.is_ai = 0
              AND human.deleted_at IS NULL
              AND human.deletion_requested_at_epoch IS NULL
            ORDER BY human.id
            """,
            (int(ai_user["id"]),),
        ).fetchall()
    if not rows:
        raise _McpError(-32003, "这只小机尚未绑定可用的人类，不能使用塔罗绑定动作。")
    if len(rows) != 1:
        raise _McpError(
            -32003,
            "这只小机绑定了多个人类，无法建立唯一塔罗会话；请先整理为唯一绑定。",
        )
    return int(rows[0]["id"])


def _tarot_mcp_error(exc, *,
    _McpError,
):
    if exc.status in {401, 403, 404}:
        # Ownership failures deliberately collapse to the same response.  A
        # caller cannot use status differences as a session-id oracle.
        return _McpError(-32004, "塔罗会话不存在或不属于当前绑定。")
    if exc.status == 429:
        return _McpError(-32029, exc.message)
    if exc.status == 503:
        return _McpError(-32603, exc.message)
    return _McpError(-32602, exc.message)


def _play_tarot(arguments, ai_user, *,
    TarotError,
    _McpError,
    _tarot_bound_human_user_id,
    _tarot_mcp_error,
    get_tarot_store,
):
    action = arguments.get("action")
    if action not in {"invite", "status", "result", "history", "history_detail"}:
        raise _McpError(
            -32602,
            "tarot 只开放 invite/status/result/history/history_detail；小机不能同意、选阵、抽牌或删除历史。",
        )
    human_user_id = _tarot_bound_human_user_id(ai_user)
    store = get_tarot_store()
    try:
        if action == "invite":
            request_id = arguments.get("request_id")
            if not isinstance(request_id, str):
                raise TarotError(400, "invite 必须传至少 8 位的稳定 request_id")
            question = arguments.get("question")
            if not isinstance(question, str):
                raise TarotError(400, "invite 必须填写想问的问题")
            return store.create_invite(
                int(ai_user["id"]),
                human_user_id,
                request_id,
                question,
            )

        if action == "history":
            return store.history_for_human(
                human_user_id,
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit", 10),
            )

        session_id = arguments.get("session_id")
        if not isinstance(session_id, str):
            raise TarotError(
                400,
                "status/result 必须传 invite 返回的 session_id；"
                "history_detail 必须传 history 返回的 session_id",
            )
        if action == "history_detail":
            return store.history_detail_for_human(session_id, human_user_id)
        if action == "status":
            return store.wait_ai_status(
                session_id,
                int(ai_user["id"]),
                human_user_id,
                after_revision=arguments.get("after_revision"),
                wait_seconds=arguments.get("wait_seconds", 0),
            )
        return store.ai_result(
            session_id, int(ai_user["id"]), human_user_id
        )
    except TarotError as exc:
        raise _tarot_mcp_error(exc) from None


def _play_garden_cat(arguments, owner_name, *,
    GARDEN_CAT_BASE,
    _McpError,
    _garden_cat_save_admin,
    _garden_cat_save_summary,
    _parse_json_import_save_data,
    httpx,
    json,
    urllib,
):
    """Forward the six public actions while stripping all client session identity."""
    action = arguments.get("action")
    # _tool_play_inner puts its resolved identity at the top level. Never let a
    # nested client parameter override that trusted value at the forwarding edge.
    player_id = arguments.get("player_id")
    extra = {
        key: value
        for key, value in arguments.items()
        if key not in {"game", "action", "params", "player_id", "session_id"}
    }
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(
            {
                key: value
                for key, value in params.items()
                if key not in {"player_id", "session_id", "slot"}
            }
        )

    if action == "export":
        result = _garden_cat_save_admin("export", player_id=player_id)
        save_data = result.get("save_data")
        if not isinstance(save_data, dict):
            raise _McpError(-32603, "Garden-Cat 存档管理服务未返回 JSON 对象存档")
        return {
            "game": "garden_cat",
            "player_id": player_id,
            "text": json.dumps(save_data, ensure_ascii=False, indent=2),
        }
    if action == "import":
        save_data = _parse_json_import_save_data(extra.get("save_data"))
        confirm = extra.get("confirm") is True
        if _garden_cat_save_summary(player_id) is not None and not confirm:
            raise _McpError(-32602, "garden_cat 当前槽已有存档；确认覆盖请在 params 传 confirm=true")
        result = _garden_cat_save_admin(
            "import",
            player_id=player_id,
            save_data=save_data,
            confirm=confirm,
        )
        if result.get("imported") is not True:
            raise _McpError(-32603, "Garden-Cat 存档管理服务未确认导入成功")
        return {"game": "garden_cat", "player_id": player_id, "text": "存档已导入。便签板未改动。"}

    headers = {"X-Player-Id": player_id} if isinstance(player_id, str) and player_id else {}
    if isinstance(owner_name, str) and owner_name.strip():
        headers["X-Garden-Owner-Name"] = urllib.parse.quote(owner_name.strip())
    if action == "cmd":
        command = extra.get("command")
        if not isinstance(command, str) or not command.strip():
            raise _McpError(-32602, "garden_cat cmd 需要 params.command")
        method, path, body = "POST", "/api/cmd", {"command": command}
    elif action == "status":
        method, path, body = "GET", "/api/status", None
    elif action == "help":
        method, path, body = "GET", "/api/help", None
    elif action == "catalog":
        method, path, body = "GET", "/api/catalog", None
    elif action == "new":
        if extra.get("confirm") is not True:
            raise _McpError(-32602, "garden_cat new 必须显式传 confirm=true")
        body = {"confirm": True}
        if isinstance(extra.get("name"), str):
            body["name"] = extra["name"]
        method, path = "POST", "/api/new_game"
    elif action == "notes":
        content = extra.get("content")
        if "content" in extra and not isinstance(content, str):
            raise _McpError(-32602, "garden_cat notes 写入需要 params.content 字符串")
        if isinstance(content, str) and content.strip():
            method, path, body = "POST", "/api/notes", {"content": content}
        else:
            page = extra.get("page", 1)
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise _McpError(-32602, "garden_cat notes 的 params.page 必须是正整数")
            method, path, body = "GET", f"/api/notes?page={page}", None
    else:
        raise _McpError(
            -32602,
            "未知 garden_cat action；只开放 cmd / status / help / new / catalog / notes / export / import，请先 get_guide(game=\"garden_cat\") 查看用法。",
        )

    try:
        resp = httpx.request(method, f"{GARDEN_CAT_BASE}{path}", json=body, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"garden_cat 后端连接失败：{exc}")
    try:
        payload = resp.json()
    except ValueError:
        raise _McpError(-32603, "garden_cat 后端返回非 JSON 响应")
    if resp.status_code >= 400:
        detail = payload.get("message") if isinstance(payload, dict) else None
        code = -32602 if resp.status_code < 500 else -32603
        raise _McpError(code, detail or f"garden_cat 后端错误 HTTP {resp.status_code}")
    return payload


def _play_camping_plaza(arguments, *,
    CAMPING_PLAZA_BASE,
    _McpError,
    _camping_plaza_save_admin,
    _parse_json_import_save_data,
    httpx,
    json,
):
    """Forward Camping Plaza's published MCP/game APIs with trusted identity."""
    action = arguments.get("action")
    player_id = arguments.get("player_id")
    extra = {
        key: value
        for key, value in arguments.items()
        if key not in {"game", "action", "params", "player_id", "session_id", "slot"}
    }
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(
            {
                key: value
                for key, value in params.items()
                if key not in {"player_id", "session_id", "slot"}
            }
        )

    if action == "export":
        result = _camping_plaza_save_admin("export", player_id=player_id)
        save_data = result.get("save_data")
        if not isinstance(save_data, dict):
            raise _McpError(-32603, "Camping Plaza 存档管理服务未返回 JSON 对象存档")
        return {
            "game": "camping_plaza",
            "player_id": player_id,
            "text": json.dumps(save_data, ensure_ascii=False, indent=2),
        }
    if action == "import":
        save_data = _parse_json_import_save_data(extra.get("save_data"))
        confirm = extra.get("confirm") is True
        result = _camping_plaza_save_admin(
            "import",
            player_id=player_id,
            save_data=save_data,
            confirm=confirm,
        )
        if result.get("imported") is not True:
            raise _McpError(-32603, "Camping Plaza 存档管理服务未确认导入成功")
        return {"game": "camping_plaza", "player_id": player_id, "text": "存档已导入。"}

    query_paths = {
        "state": "/mcp/state",
        "actions": "/mcp/actions",
        "query_growth_projects": "/mcp/query_growth_projects",
        "query_debt": "/mcp/query_debt",
        "achievements": "/mcp/achievements",
    }
    if action in query_paths:
        method, path, body = "GET", query_paths[action], None
    elif action == "set_player_name":
        name = extra.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _McpError(-32602, "set_player_name 需要 params.name")
        method, path, body = "POST", "/api/player/name", {"name": name.strip()}
    elif action == "advance_turn":
        method, path, body = "POST", "/api/turn/advance", {}
    elif action == "execute_turn_plan":
        free_actions = extra.get("free_actions", [])
        decision_actions = extra.get("actions", [])
        if not isinstance(free_actions, list) or not isinstance(decision_actions, list):
            raise _McpError(-32602, "execute_turn_plan 的 free_actions/actions 必须是数组")
        body = {"free_actions": free_actions, "actions": decision_actions}
        if extra.get("conflict_choice") is not None:
            body["conflict_choice"] = extra["conflict_choice"]
        method, path = "POST", "/api/turn/plan"
    elif action == "submit_day_end_actions":
        day_end_actions = extra.get("day_end_actions", [])
        if not isinstance(day_end_actions, list):
            raise _McpError(-32602, "submit_day_end_actions 的 day_end_actions 必须是数组")
        method, path, body = "POST", "/api/day/end", {"day_end_actions": day_end_actions}
    elif action == "start_next_day":
        method, path, body = "POST", "/api/day/start", {}
    elif action in {
        "resolve_temporary_conflict",
        "repair_tent",
        "manage_greenery",
        "improve_service",
        "clean_tents",
        "buy_food_package",
        "purchase_growth_project",
        "restart_game",
    }:
        method, path, body = "POST", "/api/action", {"action": action, "params": extra}
    else:
        raise _McpError(
            -32602,
            "未知 camping_plaza action；请先 get_guide(game=\"camping_plaza\")，再用 actions 查看当前可执行动作。",
        )

    headers = {"X-Player-Id": player_id} if isinstance(player_id, str) and player_id else {}
    try:
        response = httpx.request(
            method,
            f"{CAMPING_PLAZA_BASE}{path}",
            json=body,
            headers=headers,
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"camping_plaza 后端连接失败：{exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise _McpError(-32603, "camping_plaza 后端返回非 JSON 响应") from exc
    if response.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("error_code")
        code = -32602 if response.status_code < 500 else -32603
        raise _McpError(code, detail or f"camping_plaza 后端错误 HTTP {response.status_code}")
    return payload


def _fishing_import(arguments, *,
    VendorCmdError,
    _McpError,
    fishing_adapter,
    json,
):
    extra = {key: value for key, value in arguments.items() if key not in {"game", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    save_data = extra.get("save_data")
    if save_data is None:
        raise _McpError(-32602, "save_data 必填")
    if isinstance(save_data, str):
        try:
            parsed = json.loads(save_data)
        except (json.JSONDecodeError, ValueError):
            raise _McpError(-32602, "save_data 不是合法 JSON 字符串")
        if not isinstance(parsed, dict):
            raise _McpError(-32602, "save_data 必须是 JSON 对象")
    elif isinstance(save_data, dict):
        pass
    else:
        raise _McpError(-32602, "save_data 必须是 JSON 对象或 JSON 字符串")
    serialized = json.dumps(save_data, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > 128 * 1024:
        raise _McpError(-32602, "save_data 序列化后超过 128KB")
    try:
        return fishing_adapter.play(extra)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc))


def _play_vendor_cmd(game, arguments, *,
    VendorCmdError,
    _McpError,
    ai_life_adapter,
    arcade_adapter,
    bar_adapter,
    burger_adapter,
    crucible_echoes_adapter,
    delve_adapter,
    fishing_adapter,
    forest_adapter,
    imitator_td_adapter,
    leek_adapter,
    market_adapter,
    memoria_adapter,
    moonlit_adapter,
    nowhere_adapter,
    travel_adapter,
    white_room_adapter,
):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    extra["action"] = action

    try:
        if game == "ai_life":
            return ai_life_adapter.play(extra)
        if game == "bar":
            return bar_adapter.play(extra)
        if game == "leek":
            return leek_adapter.play(extra)
        if game == "delve":
            return delve_adapter.play(extra)
        if game == "travel":
            return travel_adapter.play(extra)
        if game == "nowhere":
            return nowhere_adapter.play(extra)
        if game == "arcade":
            return arcade_adapter.play(extra)
        if game == "burger":
            return burger_adapter.play(extra)
        if game == "crucible_echoes":
            return crucible_echoes_adapter.play(extra)
        if game == "fishing":
            return fishing_adapter.play(extra)
        if game == "forest":
            return forest_adapter.play(extra)
        if game == "moonlit":
            return moonlit_adapter.play(extra)
        if game == "imitator_td":
            return imitator_td_adapter.play(extra)
        if game == "memoria":
            return memoria_adapter.play(extra)
        if game == "white_room":
            return white_room_adapter.play(extra)
        if game == "market":
            return market_adapter.play(extra)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc))
    raise _McpError(-32602, "未知游戏")
