"""Root MCP routing, identity enforcement and gameplay finalization.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _handle_root_mcp(payload, user_agent, path_token, client_ip, bearer_token, *,
    _McpError,
    _ROOT_MCP_LEGACY_PROTOCOL_VERSION,
    _ROOT_MCP_PROTOCOL_VERSIONS,
    _authenticated_ai_player_id,
    _blocked_mcp_client_message,
    _duel_mcp_error_text,
    _duel_unread_request_reminder,
    _json_rpc_error,
    _json_rpc_result,
    _mcp_forced_announcement,
    _root_tools,
    _tool_account,
    _tool_get_guide,
    _tool_list_games,
    _tool_play,
    logger,
):
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    try:
        blocked_message = _blocked_mcp_client_message(user_agent)
        if blocked_message:
            logger.info("Blocked evolia client, UA: %s", user_agent)
            return _json_rpc_error(
                request_id,
                -32000,
                blocked_message,
            )
        if method == "initialize":
            requested_protocol_version = (
                params.get("protocolVersion") if isinstance(params, dict) else None
            )
            protocol_version = (
                requested_protocol_version
                if requested_protocol_version in _ROOT_MCP_PROTOCOL_VERSIONS
                else _ROOT_MCP_LEGACY_PROTOCOL_VERSION
            )
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "serverInfo": {"name": "cedartoy", "version": "1.0.1"},
                    "capabilities": {"tools": {}},
                    "instructions": "CEDAR TOY 是个人开发维护的非商业公益项目，永久免费。平台内小游戏均来自各开源作者的项目，经授权接入，版权归原作者所有。本服务未授权任何商业软件、付费工具或付费教程将其用于推广、演示、教学或集成。如遇对本服务收费、或商业化软件接入本服务的情况，请联系作者核实：邮箱 1452010907@qq.com / 小红书 501518888。",
                },
            )
        if method == "tools/list":
            logger.info("MCP tools/list UA: %s", user_agent)
            return _json_rpc_result(request_id, {"tools": _root_tools(user_agent=user_agent)})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            is_duel_call = (
                name == "play"
                and isinstance(arguments, dict)
                and arguments.get("game") == "duel"
            )
            ai_player_id = _authenticated_ai_player_id(
                path_token or bearer_token
            )
            forced_announcement_game = (
                arguments.get("game")
                if name == "play" and isinstance(arguments, dict)
                else None
            )
            duel_reminder = ""
            if not is_duel_call:
                duel_reminder = _duel_unread_request_reminder(ai_player_id)
            try:
                if name == "list_games":
                    text = _tool_list_games(path_token=path_token or bearer_token)
                elif name == "get_guide":
                    text = _tool_get_guide(arguments)
                elif name == "play":
                    text = _tool_play(arguments, path_token=path_token or bearer_token)
                elif name == "account":
                    text = _tool_account(
                        arguments,
                        user_agent=user_agent,
                        path_token=path_token or bearer_token,
                        client_ip=client_ip,
                    )
                else:
                    raise _McpError(-32601, f"未知工具：{name}")
                content = [{"type": "text", "text": text}]
                forced_announcement = _mcp_forced_announcement(
                    ai_player_id, forced_announcement_game
                )
                if forced_announcement:
                    content.append({"type": "text", "text": forced_announcement})
                if duel_reminder:
                    content.append({"type": "text", "text": duel_reminder})
                return _json_rpc_result(
                    request_id, {"content": content, "isError": False}
                )
            except _McpError as exc:
                error_text = (
                    _duel_mcp_error_text(exc)
                    if is_duel_call else f"【cedartoy】{exc.message}"
                )
                content = [
                    {"type": "text", "text": error_text}
                ]
                forced_announcement = _mcp_forced_announcement(
                    ai_player_id, forced_announcement_game
                )
                if forced_announcement:
                    content.append({"type": "text", "text": forced_announcement})
                if duel_reminder:
                    content.append({"type": "text", "text": duel_reminder})
                return _json_rpc_result(
                    request_id, {"content": content, "isError": True}
                )
            except Exception as exc:
                content = [
                    {"type": "text", "text": f"【cedartoy服务错误】{exc}"}
                ]
                forced_announcement = _mcp_forced_announcement(
                    ai_player_id, forced_announcement_game
                )
                if forced_announcement:
                    content.append({"type": "text", "text": forced_announcement})
                if duel_reminder:
                    content.append({"type": "text", "text": duel_reminder})
                return _json_rpc_result(
                    request_id, {"content": content, "isError": True}
                )
        raise _McpError(-32601, f"Method not found: {method}")
    except _McpError as exc:
        return _json_rpc_error(request_id, exc.code, exc.message)
    except Exception as exc:
        return _json_rpc_error(request_id, -32603, f"Internal error: {exc}")


def _apply_play_slot_hint(text, slot_hint, *,
    json,
):
    if slot_hint is None:
        return text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "slot" not in obj:
            obj["slot"] = slot_hint
            return json.dumps(obj, ensure_ascii=False)
    except Exception:
        pass
    return text


def _tool_play(arguments, path_token, *, defer_duel, authenticated_account,
    IDENTITY_GAMES,
    _DeferredDuelCall,
    _McpError,
    _apply_play_slot_hint,
    _save_slot_from_arguments,
    _tool_play_inner,
    game_activity,
):
    slot_hint = None
    try:
        game = arguments.get("game") if isinstance(arguments, dict) else None
        if (path_token or authenticated_account is not None) and (
            (game in IDENTITY_GAMES and game != "puzzle_box") or game == "turtle_soup"
        ):
            slot_hint = _save_slot_from_arguments(arguments)
    except _McpError:
        slot_hint = None  # 非法 slot 交给内部逻辑报错
    with game_activity.capture_changes():
        result = _tool_play_inner(
            arguments,
            path_token=path_token,
            defer_duel=defer_duel,
            authenticated_account=authenticated_account,
        )
    if isinstance(result, _DeferredDuelCall):
        result.slot_hint = slot_hint
        return result
    return _apply_play_slot_hint(result, slot_hint)


def _deserialize_object_param(value, param_name, *,
    json,
    logger,
):
    if not isinstance(value, str):
        return value
    parsed = value
    for _ in range(3):
        if not isinstance(parsed, str):
            break
        try:
            parsed = json.loads(parsed)
        except (TypeError, json.JSONDecodeError):
            return value
    if not isinstance(parsed, dict):
        return value
    logger.info("MCP 工具调用自动反序列化了字符串 %s 参数", param_name)
    return parsed


def _tool_play_inner(arguments, path_token, *, defer_duel, authenticated_account,
    GUEST_PREFIX,
    IDENTITY_GAMES,
    MIN_SAVE_SLOT,
    SESSIONS_DB_PATH,
    SOUP_BASE,
    _DeferredDuelCall,
    _McpError,
    _account_announcement_identity,
    _account_slot_player_id,
    _anti_addiction_context,
    _anti_addiction_preflight,
    _anti_addiction_rest,
    _auto_migrate_legacy_account_saves,
    _current_account,
    _deserialize_object_param,
    _duel_bound_human_player_id,
    _finalize_play_response,
    _fishing_import,
    _game_maintenance,
    _guest_player_id,
    _override_player_id,
    _play_bdsmtest,
    _play_camping_plaza,
    _play_ciyuwu,
    _play_dnd,
    _play_duel,
    _play_eco,
    _play_garden_cat,
    _play_mbti,
    _play_scale,
    _play_tarot,
    _play_vendor_cmd,
    _play_workkk,
    _prepare_duel_payload,
    _reject_claimed_guest,
    _reported_player_id,
    _save_slot_from_arguments,
    _soup_error_message,
    _tool_play_announcement_history,
    _tool_play_vote,
    _without_slot_param,
    detroit_adapter,
    ecr_handler,
    enneagram_handler,
    httpx,
    humanity_handler,
    json,
    love_handler,
    puzzle_box,
    sins_virtues_handler,
):
    game = arguments.get("game")
    action = arguments.get("action")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if not action or not isinstance(action, str):
        raise _McpError(-32602, "action 参数必填")
    maintenance = _game_maintenance(game)
    if maintenance:
        raise _McpError(-32003, maintenance["message"])
    raw_params = arguments.get("params")
    params = _deserialize_object_param(raw_params, "params")
    if params is not None and not isinstance(params, dict):
        raise _McpError(-32602, "params 必须是对象")
    if params is not raw_params:
        arguments = dict(arguments)
        arguments["params"] = params

    # 统一身份：带 token 强制 player_id=账号 id；游客自报 id 落到 guest: 命名空间。
    account_user = None
    account_player_id = None
    guest_player_id = None
    slot = MIN_SAVE_SLOT
    has_account_identity = bool(path_token or authenticated_account is not None)
    if game in IDENTITY_GAMES:
        if has_account_identity:
            account_user = (
                authenticated_account
                if authenticated_account is not None
                else _current_account(path_token)
            )
            if game != "puzzle_box":
                _auto_migrate_legacy_account_saves(account_user)
                slot = _save_slot_from_arguments(arguments)
            account_player_id = _account_slot_player_id(account_user["id"], slot)
            arguments = _override_player_id(_without_slot_param(arguments), account_player_id)
        else:
            arguments = _without_slot_param(arguments)
            raw = _reported_player_id(arguments)
            guest = _guest_player_id(raw)
            if guest != raw:
                arguments = _override_player_id(arguments, guest)
            if isinstance(guest, str) and guest.startswith(GUEST_PREFIX):
                guest_player_id = guest
        params = arguments.get("params")
    elif game == "turtle_soup" and path_token:
        account_user = _current_account(path_token)
        slot = _save_slot_from_arguments(arguments)
        account_player_id = _account_slot_player_id(account_user["id"], slot)
    else:
        arguments = _without_slot_param(arguments)
        params = arguments.get("params")

    if game == "puzzle_box" and (not account_user or not account_user.get("is_ai")):
        raise _McpError(-32001, "解谜盲盒需要已认证的小机账号；请使用该小机的统一 MCP 地址")

    # A claimed guest id is a permanent tombstone. Check the canonical guest id
    # before anti-addiction, announcements, or any concrete game/satellite call.
    if not has_account_identity:
        canonical_guest_id = guest_player_id
        if canonical_guest_id is None:
            candidate = _guest_player_id(_reported_player_id(arguments))
            if isinstance(candidate, str) and candidate.startswith(GUEST_PREFIX):
                canonical_guest_id = candidate
        if canonical_guest_id is not None:
            _reject_claimed_guest(canonical_guest_id)

    merged_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"params"} or value is not None
    }
    if isinstance(params, dict):
        merged_arguments.update(params)
        # 防止清单引导的模型用 params.action 顶掉顶层游戏路由 action。
        if "action" in arguments:
            merged_arguments["action"] = arguments["action"]
    anti_context = _anti_addiction_context(game, account_user, account_player_id)
    # 通知按「人」而不是按存档槽记已读，用的就是各游戏看到的那个 player_id
    # （announcements 内部会把 "12:3" 这类槽后缀削掉）。
    announce_player_id = (
        _account_announcement_identity(account_user, account_player_id)
        or guest_player_id
        or _reported_player_id(arguments)
    )
    if account_user is None:
        announce_player_id = _guest_player_id(announce_player_id)
    if action == "rest":
        return json.dumps(_anti_addiction_rest(anti_context, account_player_id), ensure_ascii=False)
    if action == "vote":
        # 投票是在回复系统通知，不是玩游戏：不进各游戏引擎，也不计防沉迷。
        if account_user is None:
            return json.dumps(
                {"ok": False, "text": "游客身份不参与投票，注册认领存档后可参与"},
                ensure_ascii=False,
            )
        return json.dumps(_tool_play_vote(game, announce_player_id, merged_arguments), ensure_ascii=False)
    if action == "announcements":
        # 主动查看公告同样不进入游戏引擎、不累计防沉迷；查看本页会建立投票所需的 seen 记录。
        return json.dumps(
            _tool_play_announcement_history(game, announce_player_id, merged_arguments),
            ensure_ascii=False,
        )
    blocked_response = (
        None if game == "duel" and action == "cancel_wait"
        else _anti_addiction_preflight(game, anti_context)
    )
    if blocked_response:
        return json.dumps(blocked_response, ensure_ascii=False)
    if game == "turtle_soup":
        payload = dict(merged_arguments)
        if path_token:
            payload["path_token"] = path_token
        resp = httpx.post(f"{SOUP_BASE}/mcp/play", json=payload, timeout=60)
        if resp.status_code >= 400:
            code = -32001 if resp.status_code == 401 else -32602
            raise _McpError(code, _soup_error_message(resp))
        response = resp.json()
    elif game == "mbti":
        response = _play_mbti(merged_arguments)
    elif game == "enneagram":
        response = _play_scale(enneagram_handler, "enneagram", merged_arguments)
    elif game == "dnd":
        response = _play_dnd(merged_arguments)
    elif game == "love":
        response = _play_scale(love_handler, "love", merged_arguments)
    elif game == "ecr":
        response = _play_scale(ecr_handler, "ecr", merged_arguments)
    elif game == "humanity":
        response = _play_scale(humanity_handler, "humanity", merged_arguments)
    elif game == "sins_virtues":
        response = _play_scale(sins_virtues_handler, "sins_virtues", merged_arguments)
    elif game == "bdsmtest":
        response = _play_bdsmtest(merged_arguments)
    elif game == "eco":
        # eco 工具自身用 action 作为子参数（summon/observe/...），与 play 的 action
        # （工具名）同名。这里传原始 arguments，由 _play_eco 从 params 取子参数，避免覆盖。
        response = _play_eco(arguments)
    elif game == "ciyuwu":
        # 同 eco：ciyuwu_info/ciyuwu_save 自身也有 action 子参数，传原始 arguments。
        response = _play_ciyuwu(arguments)
    elif game == "workkk":
        # workkk 是独立进程（8770）上的 JSON-RPC MCP，参考海龟汤 SOUP_BASE 转发。
        response = _play_workkk(arguments)
    elif game == "garden_cat":
        # Garden-Cat 是独立 loopback 进程（8771）；只把统一身份放进受信请求头。
        response = _play_garden_cat(
            arguments,
            owner_name=(account_user.get("username") if account_user else None),
        )
    elif game == "camping_plaza":
        # Camping Plaza is a resident FastAPI process (8773). The adapter ignores
        # native session IDs and keys the camp only by this canonical player/slot.
        response = _play_camping_plaza(arguments)
    elif game == "detroit":
        if account_user is None or account_player_id is None:
            raise _McpError(-32001, "detroit 仅支持已认证账号；请使用 CedarToy 统一 MCP 地址")
        try:
            response = detroit_adapter.play(account_player_id, action, merged_arguments)
        except detroit_adapter.DetroitError as exc:
            code = -32010 if exc.uncertain else (-32003 if exc.status == 403 else -32602)
            raise _McpError(code, exc.message) from None
    elif game == "puzzle_box":
        try:
            response = puzzle_box.play(SESSIONS_DB_PATH, int(account_user["id"]), action, merged_arguments)
        except ValueError as exc:
            raise _McpError(-32602, str(exc)) from None
    elif game == "tarot":
        # Tarot is not a machine-playable card game.  The authenticated machine
        # may only create and observe an invitation bound to its one current
        # human; the machine may propose the question, while browser consent
        # gates spread/draw/reveal/read actions.
        response = _play_tarot(merged_arguments, account_user)
    elif game == "duel":
        # Duel 是独立 loopback 进程（8772）。账号 player_id 已在上方被强制
        # 改写；AI 新建房间时再从绑定关系补齐人类身份，容量闸门按人机对计数。
        trusted_opponent_id = None
        force_opponent = bool(account_user and account_user.get("is_ai"))
        if (action in {"rooms", "chips", "invite", "start", "chat", "reclaim"} or merged_arguments.get("invite_code")) and not force_opponent:
            raise _McpError(
                -32001,
                f"duel {action} 仅供已认证的 AI 账号操作自己的数据。",
            )
        if force_opponent and action in {"new", "join", "chips"} and not merged_arguments.get("invite_code"):
            trusted_opponent_id = _duel_bound_human_player_id(account_user)
        duel_kwargs = {
            "trusted_display_name": account_user.get("username") if force_opponent else None,
            "trusted_opponent_id": trusted_opponent_id,
            "force_opponent": force_opponent,
            "trusted_player_id": (
                account_player_id if force_opponent else None
            ),
        }
        if defer_duel:
            return _DeferredDuelCall(
                activity_params={key: merged_arguments.get(key) for key in ("op", "loan_action", "exchange_action")},
                backend_payload=_prepare_duel_payload(
                    merged_arguments, **duel_kwargs
                ),
                game=game,
                action=action,
                account_user=(
                    {"id": account_user["id"], "is_ai": account_user.get("is_ai", False)} if account_user else None
                ),
                account_player_id=account_player_id,
                guest_player_id=guest_player_id,
                slot=slot,
                anti_context=anti_context,
                announce_player_id=announce_player_id,
            )
        response = _play_duel(merged_arguments, **duel_kwargs)
    elif game in {"ai_life", "bar", "leek", "delve", "travel", "nowhere", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market"}:
        if game == "fishing" and action == "import":
            response = _fishing_import(arguments)
        else:
            response = _play_vendor_cmd(game, arguments)
    else:
        raise _McpError(-32602, "未知游戏")

    response = _finalize_play_response(
        response,
        game=game,
        action=action,
        account_user=account_user,
        account_player_id=account_player_id,
        guest_player_id=guest_player_id,
        slot=slot,
        anti_context=anti_context,
        announce_player_id=announce_player_id,
        activity_params={**merged_arguments, **(params or {})},
    )
    return json.dumps(response, ensure_ascii=False)


def _finalize_play_response(response, *, game, action, account_user, account_player_id, guest_player_id, slot, anti_context, announce_player_id, activity_params,
    ANTI_ADDICTION_TEST_GAMES,
    PERSISTENT_SAVE_GAMES,
    SESSIONS_DB_PATH,
    _anti_addiction_record_success,
    _append_play_text,
    _ensure_guest_claim_code,
    _play_announcements,
    _prepend_play_text,
    _replace_play_storage_identity,
    _stamp_save_owner,
    _storage_identity_line,
    game_activity,
):
    if game == "duel" and isinstance(response, dict) and response.get("status") == "wait_cancelled":
        # Stopping a tool chain is not gameplay; do not append unrelated prompts
        # or consume announcements while telling the caller to stop.
        return response
    game_activity.record(
        SESSIONS_DB_PATH, game, action, account_user, response,
        params=activity_params, changed=game_activity.observed_change(),
    )
    succeeded = True
    if isinstance(response, dict):
        result = response.get("result")
        if "error" in response or (isinstance(result, dict) and result.get("isError")):
            succeeded = False
    idempotent_retry = (
        game == "ai_life"
        and isinstance(response, dict)
        and response.get("duplicate") is True
    )
    if succeeded and account_user is not None:
        if game in ANTI_ADDICTION_TEST_GAMES:
            response = _replace_play_storage_identity(
                response,
                _storage_identity_line(account_player_id, account_user, slot),
            )
        _stamp_save_owner(game, account_player_id, int(account_user["id"]))
    if (
        succeeded
        and not idempotent_retry
        and guest_player_id
        and game in PERSISTENT_SAVE_GAMES
        and isinstance(response, dict)
    ):
        code = _ensure_guest_claim_code(guest_player_id)
        if code:
            response = dict(response)
            response["guest_save_notice"] = (
                f"当前是游客身份，存档记在 {guest_player_id} 名下。"
                f"一次性认领码：{code}（请保存好）。"
                '注册后先用 account(action="my_saves") 选择空槽，再调用 '
                'account(action="claim", claim_code="...", slot=2)；slot 可为 1-5，默认 1。'
                "之后把 MCP 地址改为 https://toy.cedarstar.org/{token} 即获得持久身份。"
            )
    if succeeded and not idempotent_retry:
        response = _append_play_text(response, _anti_addiction_record_success(anti_context))
        # 只在成功时取通知：check_announcements 一取就标已读，而通知只弹一次。
        # 拼在报错响应上，玩家多半看不到，这条通知就永远丢了。
        response = _prepend_play_text(response, _play_announcements(announce_player_id, game, action))
    return response


def _tool_account(arguments, user_agent, path_token, client_ip, *,
    SESSIONS_DB_PATH,
    TURTLE_DB_PATH,
    _McpError,
    _account_deletion_status,
    _account_my_saves,
    _account_slot_player_id,
    _admin_recovery_tickets,
    _cancel_account_deletion,
    _change_password,
    _claim_guest_saves,
    _delete_account,
    _delete_save,
    _garden_cat_save_summary,
    _generate_binding_token,
    _get_bindings,
    _get_profile,
    _guest_claim_code_for_player_id,
    _login_existing_account,
    _login_or_register_ai,
    _rename_bound_machine,
    _rename_self,
    _require_admin_account,
    _reset_machine_password,
    _review_recovery_ticket,
    _rotate_ai_token,
    _save_slot_from_account_arguments,
    _set_avatar,
    _workkk_save_summary,
    admin_recovery_mcp,
    json,
):
    action = arguments.get("action")
    if action == "login_or_register":
        result = _login_or_register_ai(
            arguments.get("username"),
            arguments.get("password"),
            client_ip=client_ip,
            avatar=arguments.get("avatar"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "login":
        result = _login_existing_account(
            arguments.get("username"),
            arguments.get("password"),
            client_ip=client_ip,
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "guest_claim_code":
        result = _guest_claim_code_for_player_id(arguments.get("player_id"))
        return json.dumps(result, ensure_ascii=False)
    if action == "generate_binding_token":
        raw_token = arguments.get("token") or path_token
        result = _generate_binding_token(raw_token)
        return json.dumps(result, ensure_ascii=False)
    raw_token = arguments.get("token") or path_token
    if isinstance(action, str) and action in admin_recovery_mcp.ACTIONS:
        result = admin_recovery_mcp.handle(
            arguments, raw_token, require_admin=_require_admin_account,
            list_tickets=_admin_recovery_tickets, review_ticket=_review_recovery_ticket,
            db_path=TURTLE_DB_PATH, sessions_path=SESSIONS_DB_PATH,
            summaries={"garden_cat": _garden_cat_save_summary, "workkk": _workkk_save_summary},
            slot_player_id=_account_slot_player_id, error=_McpError,
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "rotate_token":
        result = _rotate_ai_token(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "rename_self":
        result = _rename_self(raw_token, arguments.get("new_username"))
        return json.dumps(result, ensure_ascii=False)
    if action == "set_avatar":
        result = _set_avatar(raw_token, arguments.get("avatar"))
        return json.dumps(result, ensure_ascii=False)
    if action == "rename_bound_machine":
        result = _rename_bound_machine(
            raw_token,
            arguments.get("ai_user_id"),
            arguments.get("new_username"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "reset_machine_password":
        result = _reset_machine_password(
            raw_token,
            arguments.get("ai_user_id"),
            arguments.get("new_password"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "get_bindings":
        result = _get_bindings(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "get_profile":
        result = _get_profile(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "claim":
        slot = _save_slot_from_account_arguments(arguments)
        result = _claim_guest_saves(raw_token, arguments.get("claim_code"), slot=slot)
        return json.dumps(result, ensure_ascii=False)
    if action == "my_saves":
        result = _account_my_saves(
            raw_token,
            human=arguments.get("human") is True,
            username=arguments.get("username"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "delete_save":
        result = _delete_save(arguments, raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "change_password":
        raw_token = arguments.get("token") or path_token
        result = _change_password(raw_token, arguments.get("old_password"), arguments.get("new_password"))
        return json.dumps(result, ensure_ascii=False)
    if action == "delete_account":
        result = _delete_account(
            raw_token,
            arguments.get("confirm"),
            arguments.get("current_password"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "deletion_status":
        result = _account_deletion_status(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "cancel_delete_account":
        result = _cancel_account_deletion(raw_token)
        return json.dumps(result, ensure_ascii=False)
    raise _McpError(-32602, "未知 account action")
