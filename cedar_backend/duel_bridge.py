"""Duel backend requests and gateway ticket coordination.

Dependencies are explicit arguments supplied by server wrappers at call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
The retry example helper uses positional-only dependencies to preserve arbitrary
sibling payload keys. Ticket state and the deferred-call type stay in server.
"""


def _duel_mcp_error_text(exc, *,
    json,
):
    """Render only Duel retry metadata into the existing MCP text shape."""
    text = f"【cedartoy】{exc.message}"
    if not isinstance(exc.details, dict) or "error_type" not in exc.details:
        return text
    visible = {
        key: exc.details[key]
        for key in (
            "error_type", "field_errors", "retry_hint", "retry_example",
        )
        if key in exc.details and exc.details[key] not in (None, [], {})
    }
    return text + "\n" + json.dumps(
        visible, ensure_ascii=False, separators=(",", ":")
    )


def _duel_bound_human_player_id(ai_user, *,
    _McpError,
    _db_connect,
):
    """Resolve the sole active human bound to an AI for paired Duel actions."""
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT human.id
            FROM user_bindings b
            JOIN toy_users human ON human.id = b.human_user_id
            WHERE b.ai_user_id = ?
              AND human.is_ai = 0
              AND human.deleted_at IS NULL
            ORDER BY human.id
            """,
            (int(ai_user["id"]),),
        ).fetchall()
    if len(rows) > 1:
        raise _McpError(
            -32602,
            "这只 AI 仍绑定了多个人类，无法确定 duel 对手；请先整理为唯一绑定。",
        )
    return str(rows[0]["id"]) if rows else None


def _duel_state_retry_example(*, wait, full_state):
    params = {"room_id": "..."}
    if wait:
        params["wait"] = True
    if full_state:
        params["full_state"] = True
    return {"action": "state", "params": params}


def _duel_move_retry_example(inner_action, **siblings):
    params = {
        "room_id": "...",
        "move": {"action": str(inner_action or "roll")},
        "revision": 12,
    }
    params.update(siblings)
    return {"action": "move", "params": params}


def _duel_mcp_error(message, *, error_type, retry_hint, retry_example, field_errors, code,
    _McpError,
):
    details = {"error_type": error_type, "retry_hint": retry_hint}
    if field_errors:
        details["field_errors"] = list(field_errors)
    if retry_example:
        details["retry_example"] = retry_example
    return _McpError(code, message, details)


def _duel_compact_field_errors(data, *,
    json,
):
    if not isinstance(data, dict):
        return []
    raw = data.get("details")
    if raw is None:
        raw = data.get("detail")
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    compact = []
    for item in items:
        if isinstance(item, dict):
            field = item.get("field")
            if not field and isinstance(item.get("loc"), (list, tuple)):
                field = ".".join(
                    str(part) for part in item["loc"] if part != "body"
                )
            message = item.get("message") or item.get("msg") or item.get("type")
            if field and message:
                value = f"{field}: {message}"
            elif field:
                value = str(field)
            elif message:
                value = str(message)
            else:
                value = json.dumps(
                    item, ensure_ascii=False, separators=(",", ":")
                )
        else:
            value = str(item)
        if value and value not in compact:
            compact.append(value)
    return compact


def _duel_backend_message(status_code, data, field_errors):
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        detail = data.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    if field_errors:
        return "duel 请求字段无效"
    return f"duel 后端错误 HTTP {status_code}"


def _duel_move_field_diagnostics(message, payload, field_errors, *,
    _DUEL_KNOWN_MOVE_FIELDS,
    re,
):
    move = payload.get("move") if isinstance(payload, dict) else None
    if not isinstance(move, dict):
        return field_errors, [], []
    mentioned = {
        field for field in _DUEL_KNOWN_MOVE_FIELDS
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])",
            message,
        )
    }
    if "牌型提示" in message:
        mentioned.update({"pattern_type", "pattern_label"})
    required_alternative = "必须提供" in message and bool(mentioned)
    if not mentioned or not (
        required_alternative
        or any(
            marker in message for marker in (
                "只接受", "只能提交", "包含未知字段", "未发布的字段",
            )
        )
    ):
        return field_errors, [], []
    actual = set(move)
    if required_alternative:
        alternatives = sorted(mentioned - actual)
        enriched = list(field_errors)
        if alternatives:
            value = f"move.{'/'.join(alternatives)}: 至少一个必填"
            if value not in enriched:
                enriched.append(value)
        return enriched, alternatives, []
    optional = {
        field for field in mentioned
        if re.search(rf"可选[^\u3002；]*{re.escape(field)}", message)
    }
    missing = sorted(mentioned - actual - optional)
    extra = sorted(actual - mentioned)
    enriched = list(field_errors)
    for field in missing:
        value = f"move.{field}: 缺失"
        if value not in enriched:
            enriched.append(value)
    for field in extra:
        value = f"move.{field}: 不接受"
        if value not in enriched:
            enriched.append(value)
    return enriched, missing, extra


def _duel_backend_mcp_error(status_code, data, payload, *,
    _McpError,
    _duel_backend_message,
    _duel_compact_field_errors,
    _duel_mcp_error,
    _duel_move_field_diagnostics,
    _duel_move_retry_example,
    _duel_state_retry_example,
):
    field_errors = _duel_compact_field_errors(data)
    message = _duel_backend_message(status_code, data, field_errors)
    field_errors, missing_move_fields, extra_move_fields = (
        _duel_move_field_diagnostics(message, payload, field_errors)
    )
    combined = " ".join([message, *field_errors]).lower()
    action = payload.get("action") if isinstance(payload, dict) else None
    is_move = action == "move"
    room_id = payload.get("room_id") if isinstance(payload, dict) else None
    state_example = _duel_state_retry_example(full_state=True)
    if room_id:
        state_example["params"]["room_id"] = room_id

    if is_move and status_code == 409 and (
        "revision" in combined
        or "版本" in combined
        or "已变化" in combined
        or "过期" in combined
    ):
        return _duel_mcp_error(
            message,
            error_type="stale_revision",
            field_errors=field_errors,
            retry_hint="先 state 取最新局面和 revision，重新决策；别重放旧 move。",
            retry_example=state_example,
        )

    if is_move and any(marker in combined for marker in (
        "还没轮到你", "当前不是", "行动权属于", "当前行动者",
        "not your turn",
    )):
        wait_example = _duel_state_retry_example(wait=True)
        if room_id:
            wait_example["params"]["room_id"] = room_id
        return _duel_mcp_error(
            message,
            error_type="not_your_turn",
            field_errors=field_errors,
            retry_hint="用 state(wait=true) 等待；别重试同一 move。",
            retry_example=wait_example,
        )

    if "full_state" in combined:
        return _duel_mcp_error(
            message,
            error_type="full_state_action",
            field_errors=field_errors,
            retry_hint="full_state 只用于 state，放 params.full_state。",
            retry_example=state_example,
        )

    if "wait" in combined:
        wait_example = _duel_state_retry_example(wait=True)
        if room_id:
            wait_example["params"]["room_id"] = room_id
        return _duel_mcp_error(
            message,
            error_type="wait_usage",
            field_errors=field_errors,
            retry_hint="wait 放 params，与 move 同级；仅 state/move 使用 true/false。",
            retry_example=wait_example,
        )

    revision_missing = any(
        "revision" in item.lower()
        and any(marker in item.lower() for marker in ("required", "必填", "缺失"))
        for item in field_errors
    ) or any(marker in combined for marker in (
        "必须携带 revision", "revision 必填", "缺少 revision",
    ))
    if is_move and revision_missing:
        inner_action = (
            payload.get("move", {}).get("action", "roll")
            if isinstance(payload.get("move"), dict) else "roll"
        )
        return _duel_mcp_error(
            message,
            error_type="missing_revision",
            field_errors=field_errors,
            retry_hint="优先用最近成功响应的 revision；没有时才 state，别每步先 state。",
            retry_example=_duel_move_retry_example(inner_action),
        )

    if is_move and (
        extra_move_fields
        or any(marker in combined for marker in (
            "未知字段", "未发布的字段", "不得提交", "不接受此字段",
            "extra inputs are not permitted",
        ))
    ):
        return _duel_mcp_error(
            message,
            error_type="extra_move_fields",
            field_errors=field_errors,
            retry_hint="move 不要附加服务端未发布字段；按最新 legal_actions/legal_moves 原样选。",
            retry_example=state_example,
        )

    required_move_error = any(
        item.lower().startswith("move")
        and any(
            marker in item.lower() for marker in ("required", "必填", "缺失")
        )
        for item in field_errors
    )
    if is_move and (
        missing_move_fields
        or required_move_error
        or any(
            marker in combined for marker in ("缺少字段", "需要 move 对象")
        )
    ):
        return _duel_mcp_error(
            message,
            error_type="missing_move_fields",
            field_errors=field_errors,
            retry_hint="从最新 legal_actions/legal_moves 选完整动作，不要自行删字段。",
            retry_example=state_example,
        )

    if is_move and any(marker in combined for marker in (
        "legal_actions", "legal_moves", "authoritative", "权威动作",
        "权威合法行动", "该动作不在", "该行动不在", "该走法不合法",
        "服务端当前未发布", "服务端本次发布", "当前必须先",
        "当前没有待执行", "无效落子",
    )):
        return _duel_mcp_error(
            message,
            error_type="not_authoritative",
            field_errors=field_errors,
            retry_hint="按最新 legal_actions/legal_moves 重新选；不要推理未发布动作。",
            retry_example=state_example,
        )

    if is_move and status_code in {400, 409, 422}:
        return _duel_mcp_error(
            message,
            error_type="invalid_request",
            field_errors=field_errors,
            retry_hint="按错误信息修正后重试。",
        )
    code = -32602 if status_code < 500 else -32603
    return _McpError(code, message)


def _prepare_duel_payload(arguments, trusted_opponent_id, force_opponent, trusted_player_id, trusted_display_name, *,
    _DUEL_MOVE_SIBLING_FIELDS,
    _McpError,
    _duel_mcp_error,
    _duel_move_retry_example,
    _duel_state_retry_example,
    re,
):
    # Models sometimes attach the optional table message to the game action.
    # Keep game validators authoritative: lift only this known MCP field and
    # leave every other move key untouched so malformed actions still fail.
    if arguments.get("action") == "move":
        move = arguments.get("move")
        if isinstance(move, dict) and "message" in move:
            normalized_arguments = dict(arguments)
            normalized_move = dict(move)
            nested_message = normalized_move.pop("message")
            normalized_arguments["move"] = normalized_move
            # An explicit sibling value wins on conflict; never send both.
            if "message" not in normalized_arguments:
                normalized_arguments["message"] = nested_message
            arguments = normalized_arguments
    action = arguments.get("action")
    action_fields = {
        "catalog": set(),
        "rooms": {"include_terminal", "limit", "offset"},
        "new": {
            "game_type", "mode", "stake",
            "target_player_count", "fill_with_npcs",
        },
        "rematch": {"room_id"},
        "invite": {"game_type", "target_player_count", "stake", "timeout_takeover", "timeout_takeover_seconds"},
        "start": {"room_id", "fill_with_npcs"},
        "chat": {"room_id", "message"},
        "reclaim": {"room_id"},
        "join": {"room_id", "message", "invite_code"},
        "accept": {"room_id"},
        "reject": {"room_id"},
        "move": {"room_id", "move", "revision", "wait", "message"},
        "state": {"room_id", "wait", "full_state", "message", "move"},
        "cancel_wait": {"room_id"},
        "resign": {"room_id", "message"},
        "leave": {"room_id", "message"},
    }
    if action not in {*action_fields, "chips"}:
        inner_action = action if isinstance(action, str) and action else "roll"
        raise _duel_mcp_error(
            f'duel 外层 action="{inner_action}" 无效',
            error_type="outer_action",
            retry_hint='外层 action 改为 "move"；游戏动作放 params.move.action。',
            retry_example=_duel_move_retry_example(inner_action),
        )
    if action == "cancel_wait" and not re.fullmatch(r"[A-Za-z0-9]{8}", str(arguments.get("room_id") or "").strip()):
        raise _McpError(-32602, "cancel_wait 需要有效的 8 位 room_id")
    if "full_state" in arguments and action != "state":
        raise _duel_mcp_error(
            "full_state 不适用于当前 duel action",
            error_type="full_state_action",
            field_errors=["full_state: 仅 state 支持"],
            retry_hint="full_state 只用于 state，放 params.full_state。",
            retry_example=_duel_state_retry_example(full_state=True),
        )
    if "wait" in arguments and action not in {"move", "state"}:
        raise _duel_mcp_error(
            "wait 不适用于当前 duel action",
            error_type="wait_usage",
            field_errors=["wait: 仅 state/move 支持"],
            retry_hint="wait 放 params，与 move 同级；仅 state/move 使用。",
            retry_example=_duel_state_retry_example(wait=True),
        )
    if action == "move":
        move = arguments.get("move")
        if move is None:
            raise _duel_mcp_error(
                "move 动作缺少 move 对象",
                error_type="missing_move_fields",
                field_errors=["move: 必填对象"],
                retry_hint="从最新 legal_actions/legal_moves 选完整动作，不要自行删字段。",
                retry_example=_duel_state_retry_example(full_state=True),
            )
        if isinstance(move, dict):
            misplaced = [
                field for field in _DUEL_MOVE_SIBLING_FIELDS
                if field != "message" and field in move
            ]
            if misplaced:
                field_errors = [
                    f"move.{field}: 应与 move 同级"
                    for field in misplaced
                ]
                if "full_state" in misplaced:
                    hint = "full_state 只用于 state 的 params.full_state；其他通用字段与 move 同级。"
                    example = _duel_state_retry_example(full_state=True)
                else:
                    hint = "room_id/revision/wait/message 放 params，与 move 同级；move 只留游戏动作。"
                    example = _duel_move_retry_example(
                        move.get("action", "roll"),
                        **({"wait": True} if "wait" in misplaced else {}),
                    )
                raise _duel_mcp_error(
                    "move 含有层级错误的通用字段",
                    error_type="misplaced_common_fields",
                    field_errors=field_errors,
                    retry_hint=hint,
                    retry_example=example,
                )
        if arguments.get("revision") is None:
            inner_action = (
                move.get("action", "roll") if isinstance(move, dict) else "roll"
            )
            raise _duel_mcp_error(
                "move 缺少 revision",
                error_type="missing_revision",
                field_errors=["revision: 必填"],
                retry_hint="优先用最近成功响应的 revision；没有时才 state，别每步先 state。",
                retry_example=_duel_move_retry_example(inner_action),
            )
    if action == "chips":
        # Split McpPlayBody by chips operation so fields valid for one operation
        # cannot hitchhike on another one. Identities are injected below.
        op = arguments.get("op") or "status"
        chips_fields = {
            "status": set(),
            "check_in": set(),
            "bankruptcy": set(),
            "ledger": {"limit"},
            "achievements": set(),
            "loans": {"loan_action"},
            "exchange": {"exchange_action"},
        }
        allowed_fields = {"action", "player_id", "op"} | chips_fields.get(
            op, set()
        )
        if op == "loans":
            loan_action = arguments.get("loan_action") or "list"
            loan_fields = {
                "list": {"limit"},
                "create": {
                    "principal", "daily_rate_micro_percent", "due_date",
                    "interest_cap_enabled", "idempotency_key",
                },
                "accept": {"loan_id", "loan_revision", "idempotency_key"},
                "reject": {"loan_id", "loan_revision", "idempotency_key"},
                "counter": {
                    "loan_id", "loan_revision", "principal",
                    "daily_rate_micro_percent", "due_date",
                    "interest_cap_enabled", "idempotency_key",
                },
                "withdraw": {"loan_id", "loan_revision", "idempotency_key"},
                "repay": {"loan_id", "amount", "idempotency_key"},
            }
            allowed_fields |= loan_fields.get(loan_action, set())
        elif op == "exchange":
            exchange_action = arguments.get("exchange_action") or "list"
            exchange_fields = {
                "catalog": set(),
                "list": {"limit"},
                "create": {
                    "item_key", "request_note", "custom_title", "chip_amount",
                    "idempotency_key",
                },
                "confirm": {"request_id", "idempotency_key"},
                "reject": {"request_id", "idempotency_key"},
                "withdraw": {"request_id", "idempotency_key"},
            }
            allowed_fields |= exchange_fields.get(exchange_action, set())
    else:
        allowed_fields = {"action", "player_id"} | action_fields[action]
    payload = {
        key: value
        for key, value in arguments.items()
        if key in allowed_fields and value is not None
    }
    if trusted_display_name is not None and (action == "invite" or (action == "join" and arguments.get("invite_code"))):
        payload["display_name"] = trusted_display_name
    if trusted_player_id is not None:
        # 聚合层认证得到的 canonical AI 身份始终覆盖顶层或 params 自报值。
        payload["player_id"] = trusted_player_id
    if force_opponent:
        # 账号请求绝不接受模型自报 opponent_id；只认平台绑定表。
        payload.pop("opponent_id", None)
        if action in {"new", "join", "chips"} and trusted_opponent_id is not None and not payload.get("invite_code"):
            payload["opponent_id"] = trusted_opponent_id
    return payload


def _annotate_duel_wait_followup(response, *, action):
    """Make request-bound wait continuation explicit without pretending to push."""
    if not isinstance(response, dict):
        return response
    room_id = response.get("room_id")
    if room_id is None and isinstance(response.get("room"), dict):
        room_id = response["room"].get("room_id")
    if room_id in {None, ""}:
        return response

    status = response.get("status")
    pending = (
        action in {"new", "rematch", "join", "accept"}
        and status in {"pending", "waiting"}
    )
    wait_ended = status == "still_waiting"
    wait_downgraded = response.get("wait_downgraded") is True
    opponent_turn = (
        action in {"move", "state"}
        and status == "playing"
        and response.get("your_turn") is False
    )
    if not (pending or wait_ended or wait_downgraded or opponent_turn):
        return response

    annotated = dict(response)
    annotated["wait_scope"] = "current_request_only"
    annotated["next_call"] = {
        "game": "duel",
        "action": "state",
        "params": {"room_id": str(room_id), "wait": True},
    }
    if pending:
        annotated["wait_hint"] = (
            "房间尚未轮到你；请不要结束当前回复，立即执行 next_call 开始请求内挂等。"
            "本次请求结束后服务端不能主动唤醒宿主。"
        )
    elif wait_ended:
        annotated["wait_hint"] = (
            "本次请求内挂等已到上限。当前回复若仍可继续，请立即再次执行 next_call；"
            "否则只能等宿主下一次请求时检查未读状态。"
        )
    elif wait_downgraded:
        annotated["wait_hint"] = (
            "等待容量暂满，动作结果已保留；请执行 next_call 重试挂等。"
        )
    else:
        annotated["wait_hint"] = (
            "当前轮到对方；请执行 next_call，在同一工具请求内等待对方行动。"
        )
    return annotated


def _request_duel_backend(payload, *,
    DUEL_BASE,
    _McpError,
    _duel_backend_mcp_error,
    httpx,
):
    try:
        resp = httpx.post(f"{DUEL_BASE}/mcp/play", json=payload, timeout=55)
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"duel 后端连接失败：{exc}") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise _McpError(-32603, "duel 后端返回非 JSON 响应") from exc
    if resp.status_code >= 400:
        raise _duel_backend_mcp_error(resp.status_code, data, payload)
    return data


def _play_duel(arguments, trusted_opponent_id, force_opponent, trusted_player_id, trusted_display_name, *,
    _annotate_duel_wait_followup,
    _prepare_duel_payload,
    _request_duel_backend,
    duel_wait_control,
    re,
):
    payload = _prepare_duel_payload(
        arguments,
        trusted_opponent_id=trusted_opponent_id,
        force_opponent=force_opponent,
        trusted_player_id=trusted_player_id,
        trusted_display_name=trusted_display_name,
    )
    duel_wait_control.begin(payload)
    try:
        response = _request_duel_backend(payload)
    except Exception:
        cancelled = duel_wait_control.finish(payload)
        if cancelled is not None:
            return cancelled
        raise
    except BaseException:
        duel_wait_control.finish(payload)
        raise
    # MCP has no browser origin to resolve a relative human invitation link.
    for item in (response, response.get("room")):
        if isinstance(item, dict) and re.fullmatch(r"/duel/\?invite=[A-F0-9]{12}", str(item.get("invite_link", ""))):
            item["invite_link"] = "https://toy.cedarstar.org" + item["invite_link"]
    return duel_wait_control.finish(payload, _annotate_duel_wait_followup(
        response, action=payload.get("action")
    ))


def _mcp_tool_text_result(request_id, text, *, is_error,
    _json_rpc_result,
):
    return _json_rpc_result(
        request_id,
        {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    )


def _prune_duel_gateway_tickets(now, *,
    DUEL_GATEWAY_TICKET_TTL_SECONDS,
    _DUEL_GATEWAY_TICKETS,
    duel_wait_control,
):
    expired = [
        ticket
        for ticket, item in _DUEL_GATEWAY_TICKETS.items()
        if now - item[0] >= DUEL_GATEWAY_TICKET_TTL_SECONDS
    ]
    for ticket in expired:
        item = _DUEL_GATEWAY_TICKETS.pop(ticket, None)
        duel_wait_control.finish(item[2].backend_payload)


def _store_duel_gateway_ticket(request_id, prepared, *,
    DUEL_GATEWAY_MAX_TICKETS,
    _DUEL_GATEWAY_TICKETS,
    _DUEL_GATEWAY_TICKETS_LOCK,
    _McpError,
    _prune_duel_gateway_tickets,
    duel_wait_control,
    secrets,
    time,
):
    now = time.monotonic()
    with _DUEL_GATEWAY_TICKETS_LOCK:
        _prune_duel_gateway_tickets(now)
        if len(_DUEL_GATEWAY_TICKETS) >= DUEL_GATEWAY_MAX_TICKETS:
            raise _McpError(-32603, "duel async gateway 暂时繁忙，请稍后重试")
        ticket = secrets.token_urlsafe(32)
        duel_wait_control.begin(prepared.backend_payload)
        _DUEL_GATEWAY_TICKETS[ticket] = (now, request_id, prepared)
    return ticket


def _consume_duel_gateway_ticket(ticket, *,
    DUEL_GATEWAY_TICKET_TTL_SECONDS,
    _DUEL_GATEWAY_TICKETS,
    _DUEL_GATEWAY_TICKETS_LOCK,
    _McpError,
    _prune_duel_gateway_tickets,
    duel_wait_control,
    time,
):
    now = time.monotonic()
    with _DUEL_GATEWAY_TICKETS_LOCK:
        item = _DUEL_GATEWAY_TICKETS.pop(str(ticket or ""), None)
        _prune_duel_gateway_tickets(now)
    if item is None or now - item[0] >= DUEL_GATEWAY_TICKET_TTL_SECONDS:
        if item is not None:
            duel_wait_control.finish(item[2].backend_payload)
        raise _McpError(-32603, "duel async gateway 请求凭据已失效")
    return item[1], item[2]


def _discard_duel_gateway_ticket(ticket, *,
    _DUEL_GATEWAY_TICKETS,
    _DUEL_GATEWAY_TICKETS_LOCK,
    _prune_duel_gateway_tickets,
    duel_wait_control,
    time,
):
    now = time.monotonic()
    with _DUEL_GATEWAY_TICKETS_LOCK:
        removed = _DUEL_GATEWAY_TICKETS.pop(str(ticket or ""), None)
        _prune_duel_gateway_tickets(now)
    if removed is not None:
        duel_wait_control.finish(removed[2].backend_payload)
    return removed is not None


def _duel_response_from_gateway_completion(completion, backend_payload, *,
    _McpError,
    _duel_backend_mcp_error,
):
    if not isinstance(completion, dict):
        raise _McpError(-32603, "duel async gateway 返回格式无效")
    kind = completion.get("kind")
    if kind == "transport_error":
        detail = str(completion.get("message") or "unknown transport error")
        raise _McpError(-32603, f"duel 后端连接失败：{detail}")
    if kind == "invalid_json":
        raise _McpError(-32603, "duel 后端返回非 JSON 响应")
    if kind != "response":
        raise _McpError(-32603, "duel async gateway 返回格式无效")
    try:
        status_code = int(completion.get("status_code"))
    except (TypeError, ValueError):
        raise _McpError(-32603, "duel async gateway 返回状态无效") from None
    data = completion.get("data")
    if status_code >= 400:
        raise _duel_backend_mcp_error(
            status_code, data, backend_payload or {}
        )
    return data


def _finalize_deferred_duel_call(prepared, response, *,
    _annotate_duel_wait_followup,
    _apply_play_slot_hint,
    _finalize_play_response,
    json,
):
    response = _annotate_duel_wait_followup(
        response, action=prepared.action
    )
    response = _finalize_play_response(
        response,
        game=prepared.game,
        action=prepared.action,
        account_user=prepared.account_user,
        account_player_id=prepared.account_player_id,
        guest_player_id=prepared.guest_player_id,
        slot=prepared.slot,
        anti_context=prepared.anti_context,
        announce_player_id=prepared.announce_player_id,
        activity_params=prepared.activity_params,
    )
    text = json.dumps(response, ensure_ascii=False)
    return _apply_play_slot_hint(text, prepared.slot_hint)


def _prepare_duel_gateway_rpc(payload, *, user_agent, auth_token,
    _DeferredDuelCall,
    _McpError,
    _blocked_mcp_client_message,
    _duel_mcp_error_text,
    _json_rpc_error,
    _mcp_tool_text_result,
    _store_duel_gateway_ticket,
    _tool_play,
    logger,
):
    request_id = payload.get("id")
    blocked_message = _blocked_mcp_client_message(user_agent)
    if blocked_message:
        logger.info("Blocked evolia client, UA: %s", user_agent)
        return {
            "kind": "response",
            "status_code": 200,
            "body": _json_rpc_error(request_id, -32000, blocked_message),
        }
    try:
        params = payload.get("params") or {}
        prepared = _tool_play(
            params.get("arguments") or {},
            path_token=auth_token,
            defer_duel=True,
        )
        if not isinstance(prepared, _DeferredDuelCall):
            return {
                "kind": "response",
                "status_code": 200,
                "body": _mcp_tool_text_result(request_id, prepared),
            }
        ticket = _store_duel_gateway_ticket(request_id, prepared)
        return {
            "kind": "ready",
            "ticket": ticket,
            "backend_payload": prepared.backend_payload,
        }
    except _McpError as exc:
        return {
            "kind": "response",
            "status_code": 200,
            "body": _mcp_tool_text_result(
                request_id,
                _duel_mcp_error_text(exc),
                is_error=True,
            ),
        }
    except Exception as exc:
        return {
            "kind": "response",
            "status_code": 200,
            "body": _mcp_tool_text_result(
                request_id,
                f"【cedartoy服务错误】{exc}",
                is_error=True,
            ),
        }


def _prepare_duel_gateway_request(payload, *, original_path, user_agent, bearer_token, client_ip,
    DUEL_REQUEST_RATE_LIMIT_MAX,
    RATE_LIMIT_ERROR_CODE,
    REQUEST_RATE_LIMIT_MESSAGE,
    _ROOT_MCP_PATHS,
    _check_request_rate_limit,
    _json_rpc_error,
    _mcp_path_and_token,
    _prepare_duel_gateway_rpc,
    _request_rate_limit_identity,
):
    path, path_token = _mcp_path_and_token(original_path)
    if path not in _ROOT_MCP_PATHS and not path_token:
        return {
            "kind": "response",
            "status_code": 404,
            "body": {"error": "not found"},
        }
    if "id" not in payload:
        return {"kind": "response", "status_code": 202, "body": None}
    rate_identity = f"{_request_rate_limit_identity(path_token, client_ip)}:duel"
    if not _check_request_rate_limit(
        rate_identity, max_count=DUEL_REQUEST_RATE_LIMIT_MAX
    ):
        return {
            "kind": "response",
            "status_code": 429,
            "body": _json_rpc_error(
                payload.get("id"),
                RATE_LIMIT_ERROR_CODE,
                REQUEST_RATE_LIMIT_MESSAGE,
            ),
        }
    return _prepare_duel_gateway_rpc(
        payload,
        user_agent=user_agent,
        auth_token=path_token or bearer_token,
    )


def _finalize_duel_gateway_rpc(ticket, completion, *,
    _McpError,
    _consume_duel_gateway_ticket,
    _duel_mcp_error_text,
    _duel_response_from_gateway_completion,
    _finalize_deferred_duel_call,
    _mcp_tool_text_result,
    duel_wait_control,
    json,
):
    try:
        request_id, prepared = _consume_duel_gateway_ticket(ticket)
    except _McpError as exc:
        return {
            "ok": False,
            "status_code": 410,
            "message": exc.message,
        }
    try:
        response = duel_wait_control.finish(prepared.backend_payload, {}, release=False)
        if response.get("status") != "wait_cancelled":
            response = _duel_response_from_gateway_completion(
                completion, prepared.backend_payload
            )
        # Fence already-cancelled responses before gameplay finalization can
        # consume announcements or record activity; check again after formatting.
        response = duel_wait_control.finish(prepared.backend_payload, response, release=False)
        text = _finalize_deferred_duel_call(prepared, response)
        guarded = duel_wait_control.finish(prepared.backend_payload, response)
        if guarded is not response:
            text = json.dumps(guarded, ensure_ascii=False)
        body = _mcp_tool_text_result(request_id, text)
    except _McpError as exc:
        cancelled = duel_wait_control.finish(prepared.backend_payload, release=False)
        body = _mcp_tool_text_result(
            request_id,
            json.dumps(cancelled, ensure_ascii=False) if cancelled else _duel_mcp_error_text(exc),
            is_error=cancelled is None,
        )
    except Exception as exc:
        cancelled = duel_wait_control.finish(prepared.backend_payload, release=False)
        body = _mcp_tool_text_result(
            request_id,
            json.dumps(cancelled, ensure_ascii=False) if cancelled else f"【cedartoy服务错误】{exc}",
            is_error=cancelled is None,
        )
    finally:
        duel_wait_control.finish(prepared.backend_payload)
    return {"ok": True, "status_code": 200, "body": body}
