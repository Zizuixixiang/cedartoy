"""Short trusted prepare/finalize calls. No HTTP worker performs a long wait."""
import json
import secrets
import threading
import time
from cedar_backend.errors import _McpError
from . import decode_response

_TICKETS = {}
_LOCK = threading.Lock()
_TTL = 90


def take(ticket):
    with _LOCK:
        item = _TICKETS.pop(str(ticket), None)
    if item is None or time.monotonic() - item[0] >= _TTL:
        raise _McpError(-32603, '等待请求凭据已失效')
    return item[1:]


def prepare_rpc(server, payload, path, bearer, ua, ip):
    request_id = payload.get('id')
    try:
        if 'id' not in payload:
            return {'kind': 'response', 'status_code': 202, 'body': None}
        if server._blocked_mcp_client_message(ua):
            raise _McpError(-32000, '此客户端暂不支持')
        actual_path, path_token = server._mcp_path_and_token(path)
        if actual_path not in server._ROOT_MCP_PATHS and not path_token:
            raise _McpError(-32602, '无效 MCP 路径')
        if not server._check_request_rate_limit(f'{server._request_rate_limit_identity(path_token, ip)}:autochess', max_count=server.DUEL_REQUEST_RATE_LIMIT_MAX):
            raise _McpError(-32000, '请求过于频繁')
        args = (payload.get('params') or {}).get('arguments') or {}
        if payload.get('method') != 'tools/call' or (payload.get('params') or {}).get('name') != 'play' or args.get('game') != 'autochess':
            raise _McpError(-32602, '仅支持 autochess play')
        prepared = server._tool_play(args, path_token=path_token or bearer, defer_duel=True)
        if not isinstance(prepared, server._DeferredDuelCall):
            return {'kind': 'response', 'body': server._mcp_tool_text_result(request_id, prepared)}
        now = time.monotonic()
        with _LOCK:
            for key in list(_TICKETS):
                if now - _TICKETS[key][0] >= _TTL:
                    del _TICKETS[key]
            if len(_TICKETS) >= 128:
                raise _McpError(-32603, '等待容量已满，请稍后重试')
            ticket = secrets.token_urlsafe(32)
            _TICKETS[ticket] = (now, request_id, prepared)
        return {'kind': 'ready', 'ticket': ticket, 'backend_payload': prepared.backend_payload}
    except _McpError as exc:
        return {'kind': 'response', 'body': server._mcp_tool_text_result(request_id, exc.message, is_error=True)}


def finalize(server, ticket, completion):
    request_id, prepared = take(ticket)
    try:
        if not isinstance(completion, dict) or completion.get('error'):
            raise _McpError(-32603, '本地棋局服务暂不可用；操作重试请保持 requestId')
        response = decode_response(completion.get('status', 503), completion.get('body'))
        if response.get('status') != 'wait_cancelled':
            response = server._finalize_play_response(response, game=prepared.game, action=prepared.action,
                account_user=prepared.account_user, account_player_id=prepared.account_player_id,
                guest_player_id=prepared.guest_player_id, slot=prepared.slot, anti_context=prepared.anti_context,
                announce_player_id=prepared.announce_player_id, activity_params=prepared.activity_params)
        text = server._apply_play_slot_hint(json.dumps(response, ensure_ascii=False), prepared.slot_hint)
        body = server._mcp_tool_text_result(request_id, text)
    except _McpError as exc:
        body = server._mcp_tool_text_result(request_id, exc.message, is_error=True)
    return {'body': body}


def serve(handler, server):
    if not handler._duel_gateway_internal_authorized():
        handler._drain_body()
        return handler._send_json({'error': 'not found'}, status=404)
    try:
        # Internal caller only; allow bounded verbose replies at finalize, small prepares.
        length = int(handler.headers.get('Content-Length', '0'))
        limit = 2 * 1024 * 1024 if handler.path.endswith('/finalize') else 65536
        if not 0 < length <= limit:
            handler.close_connection = True
            return handler._send_json({'error': 'invalid body size'}, status=413)
        body = handler._read_json_body()
        op = handler.path.rsplit('/', 1)[-1]
        if op == 'prepare':
            result = prepare_rpc(server, body, handler.headers.get('X-CedarToy-Original-Path', '/mcp'),
                server._extract_bearer(handler.headers), handler.headers.get('User-Agent', ''), handler.headers.get('X-CedarToy-Client-IP', 'unknown'))
        elif op == 'finalize':
            result = finalize(server, body.get('ticket'), body.get('completion'))
        elif op == 'abandon':
            with _LOCK:
                _TICKETS.pop(str(body.get('ticket')), None)
            result = {'ok': True}
        else:
            return handler._send_json({'error': 'not found'}, status=404)
        handler._send_json(result)
    except (_McpError, ValueError, TypeError) as exc:
        handler._send_json({'error': exc.message if isinstance(exc, _McpError) else 'invalid request'}, status=400)
