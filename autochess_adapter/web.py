"""Only Cedar-authenticated private Web operations; no general upstream proxy."""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit, unquote

from cedar_backend.errors import _McpError
from . import economy
from . import enabled, prepare, request, _contains_credential

DIST = Path(__file__).resolve().parents[1] / 'vendor/autochess/dist/cedartoy'
_OPS = {
    'config': set(), 'create': {'requestId', 'pack', 'mode', 'seats'},
    'join': {'invitation'}, 'state': {'roomId'}, 'invite': {'roomId'},
    'start': {'roomId', 'requestId'},
    'action': {'roomId', 'round', 'revision', 'requestId', 'action'},
}


def author_allowed(user_id):
    # Explicit grant only. Even a site admin receives no implicit access.
    return str(user_id) in {x.strip() for x in os.getenv('AUTOCHESS_AUTHOR_USER_IDS', '').split(',') if x.strip().isdigit()}


def api(handler, server, body, path):
    if not isinstance(body, dict):
        raise _McpError(-32602, '需要 JSON 对象')
    token = server._extract_bearer(handler.headers)
    context = handler._duel_human_context(token)  # authenticates and re-reads live bindings
    owner = context['human_player']
    if not server._check_duel_web_request_rate_limit('autochess:' + owner):
        raise _McpError(-32000, '请求过于频繁，请稍后重试')
    if path.endswith('/stats'):
        allowed = author_allowed(owner)
        if body.get('op') == 'access':
            return {'allowed': allowed}
        if not allowed:
            raise _McpError(-32001, '未获本游戏统计查看授权')
    slot = server._normalize_save_slot(body.get('slot', 1))
    machines = {str(x['id']): x for x in context['machines']}
    ai_id = body.get('ai_id')
    if ai_id is not None and str(ai_id) not in machines:
        raise _McpError(-32001, '只能观看当前绑定小机的棋局')
    watched = machines.get(str(ai_id)) if ai_id is not None else None
    identity = prepare({'action': 'rooms'}, player_id=server._account_slot_player_id(watched['id'] if watched else owner, slot),
                       actor_type='ai' if watched else 'human', display_name=watched['name'] if watched else context['human_name'],
                       context=economy.context(server._db_connect, {'id': watched['id'] if watched else owner, 'username': watched['name'] if watched else context['human_name'], 'is_ai': bool(watched)}, slot))
    if path.endswith('/economy'):
        params = economy.params(body)
        if watched and params['op'] not in ('profile', 'catalog', 'wheel'):
            raise _McpError(-32001, '小机钱包仅供查看，请由小机自行操作')
        if _contains_credential(params):
            raise _McpError(-32602, '局外参数不能包含凭据')
        return request(identity, '/platform/economy', params)
    if path.endswith('/bootstrap'):
        result = request(identity, '/platform/web', {'op': 'config', 'params': {}})
        return {**result, 'name': context['human_name'], 'machines': context['machines'], 'author_stats': author_allowed(owner)}
    if path.endswith('/stats'):
        return request(identity, '/platform/stats', {'range': body.get('range', '1h')})
    op = body.get('op')
    if op not in _OPS or (watched and op not in ('config', 'state')):
        raise _McpError(-32001, '此视角不允许该操作')
    params = {k: body[k] for k in _OPS[op] if k in body}
    if _contains_credential(params):
        raise _McpError(-32602, '棋局参数不能包含凭据')
    params['watch'] = bool(watched or body.get('watch') is True)
    if params['watch'] and op not in ('state', 'config'):
        raise _McpError(-32001, '围观只能读取')
    household = []
    if op == 'create':
        selected = body.get('ai_ids', [])
        if not isinstance(selected, list) or len(selected) > 15 or len({str(i) for i in selected}) != len(selected):
            raise _McpError(-32602, '小机选择无效')
        for ident in selected:
            machine = machines.get(str(ident))
            if not machine:
                raise _McpError(-32001, '只能邀请当前绑定的小机')
            household.append({'playerId': server._account_slot_player_id(machine['id'], slot), 'name': machine['name']})
    return request(identity, '/platform/web', {'op': op, 'params': params, 'household': household})


def serve(handler, server):
    def send_json(data, status=200, extra_headers=None):
        headers = {'Connection': 'close'} if handler.command == 'POST' else {}
        try:
            return handler._send_json(data, status=status, extra_headers={**headers, **(extra_headers or {})})
        except (BrokenPipeError, ConnectionResetError):
            return None
    path = urlsplit(handler.path).path
    # Early rejects must not leave an unread body on a reusable HTTP/1.1 connection.
    if handler.command == 'POST':
        handler.close_connection = True
    if not enabled():
        return send_json({'error': 'AutoChess 尚未开放'}, status=503)
    if handler.command == 'GET' and (path in ('/autochess', '/autochess/') or path.startswith('/autochess/static/')):
        relative = 'cedartoy/index.html' if path in ('/autochess', '/autochess/') else unquote(path[len('/autochess/static/'):])
        file = (DIST / relative).resolve()
        if not file.is_relative_to(DIST.resolve()) or not file.is_file() or file.suffix.lower() not in ('.html', '.js', '.css', '.svg', '.png', '.webp', '.jpg', '.woff2', '.glb', '.json', '.ico', '.txt'):
            return send_json({'error': 'not found'}, status=404)
        data = file.read_bytes()
        handler.send_response(200)
        handler.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Cache-Control', 'no-cache')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'self'; base-uri 'none'")
        handler.end_headers()
        handler.wfile.write(data)
        return
    if handler.command != 'POST' or path not in ('/autochess/api/bootstrap', '/autochess/api/room', '/autochess/api/stats', '/autochess/api/economy'):
        return send_json({'error': 'not found'}, status=404)
    # Bearer auth, no Cookie or query fallback. Cross-origin browser traffic is refused.
    origin = handler.headers.get('Origin')
    if origin and urlsplit(origin).netloc != handler.headers.get('Host'):
        return send_json({'error': 'Forbidden origin'}, status=403)
    if not handler.headers.get('Content-Type', '').startswith('application/json'):
        return send_json({'error': 'JSON required'}, status=415)
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 65536:
            handler.close_connection = True
            return send_json({'error': 'Invalid body size'}, status=413)
        body = json.loads(handler.rfile.read(length))
        result = api(handler, server, body, path)
        return send_json(result, extra_headers={'Cache-Control': 'no-store'})
    except _McpError as exc:
        return send_json({'error': exc.message}, status=403 if exc.code in (-32001, -32003) else 400,
                                  extra_headers={'Cache-Control': 'no-store'})
    except (ValueError, TypeError):
        return send_json({'error': 'Invalid request'}, status=400)
