"""Cedar identity -> local private AutoChess satellite. No account credentials cross this boundary."""
import os
import re
from urllib.parse import urlsplit, quote

import httpx
from cedar_backend.errors import _McpError
from .economy import FIELDS as META_FIELDS

_FIELDS = {
    'meta': {'op'} | set().union(*META_FIELDS.values()),
    'rooms': set(),
    'cancel_wait': {'roomId'},
    'create': {'requestId', 'pack', 'mode', 'seats'},
    'join': {'invitation'},
    'start': {'roomId', 'requestId'},
    'read': {'roomId', 'after', 'waitMs'},
    'act': {'roomId', 'round', 'revision', 'requestId', 'kind', 'plan', 'ready', 'times', 'text'},
}
_DETAIL = {'verbose', 'report', 'units', 'unitCache'}
_ALIASES = {'room_id': 'roomId', 'request_id': 'requestId', 'wait_ms': 'waitMs', 'unit_cache': 'unitCache'}
_SECRET_KEYS = {'token', 'password', 'accounttoken', 'authorization', 'cookie', 'watchtoken', 'secret'}


def enabled():
    return os.environ.get('AUTOCHESS_ENABLED') == '1'


def configuration():
    if not enabled():
        raise _McpError(-32003, 'AutoChess 仍在接入验证阶段，尚未开放')
    origin = os.environ.get('AUTOCHESS_ORIGIN', 'http://127.0.0.1:8791')
    try:
        parsed = urlsplit(origin)
        valid = (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                 and parsed.port is not None and 1024 <= parsed.port <= 65535
                 and not parsed.username and not parsed.password and parsed.path in ('', '/')
                 and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    secret = os.environ.get('CHESS_PLATFORM_SECRET', '')
    if not valid or not re.fullmatch('[a-f0-9]{64}', secret):
        raise _McpError(-32603, 'AutoChess 本机地址或服务间密钥配置无效')
    return origin.rstrip('/'), secret


def _contains_credential(value):
    if isinstance(value, dict):
        return any(str(k).lower() in _SECRET_KEYS or _contains_credential(v) for k, v in value.items())
    return isinstance(value, list) and any(_contains_credential(v) for v in value)


def payload(action, arguments, *, allow_wait=False):
    if not isinstance(action, str) or action not in _FIELDS:
        raise _McpError(-32602, 'AutoChess action 仅支持 rooms/create/join/start/read/act/cancel_wait/meta')
    # Select only game fields; platform token, passwords and forged identities never get forwarded.
    params = {}
    for key, value in arguments.items():
        key = _ALIASES.get(key, key)
        if key in _FIELDS[action] | _DETAIL:
            if key in params and params[key] != value:
                raise _McpError(-32602, '同名参数的两种写法冲突')
            params[key] = value
    if _contains_credential(params):
        raise _McpError(-32602, '棋局参数不能包含凭据')
    wait = params.get('waitMs', 0)
    if type(wait) is not int or not 0 <= wait <= 25000:
        raise _McpError(-32602, 'waitMs 必须是 0–25000 的整数')
    if wait and not allow_wait:
        raise _McpError(-32602, '请经根 MCP 异步网关等待；同步入口仅允许 waitMs=0')
    return {'action': action, 'params': params}


def prepare(arguments, *, player_id, actor_type='guest', display_name=None, allow_wait=False, context=None):
    origin, secret = configuration()
    if not isinstance(player_id, str) or not re.fullmatch(r'(?:[1-9][0-9]*(?::[2-5])?|guest:[A-Za-z0-9_-]+)', player_id):
        raise _McpError(-32602, '无效的平台玩家身份')
    if actor_type not in ('human', 'ai', 'guest'):
        raise _McpError(-32602, '无效的玩家类型')
    headers = {'X-Autochess-Platform-Secret': secret, 'X-Player-Id': player_id,
               'X-Player-Provider': 'cedartoy', 'X-Player-Type': actor_type}
    if display_name:
        headers['X-Player-Name'] = quote(str(display_name)[:48], safe='')
    return {'origin': origin, 'headers': headers,
            'body': {**payload(arguments.get('action'), arguments, allow_wait=allow_wait), **({'context': context} if context is not None else {})}}


def decode_response(status, data):
    if 300 <= status < 400:
        raise _McpError(-32603, 'AutoChess 不接受后端重定向')
    if not isinstance(data, dict):
        raise _McpError(-32603, 'AutoChess 返回格式无效')
    if status >= 400:
        raise _McpError(-32602 if status == 400 else -32603, str(data.get('error', 'AutoChess 请求失败')))
    return data


def request(prepared, endpoint='/platform/autochess', body=None):
    if endpoint not in ('/platform/autochess', '/platform/web', '/platform/stats', '/platform/economy'):
        raise _McpError(-32603, '无效的内部路由')
    if body is not None and 'context' in prepared['body']:
        body = {**body, 'context': prepared['body']['context']}
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
            response = client.post(prepared['origin'] + endpoint, json=prepared['body'] if body is None else body,
                                   headers=prepared['headers'])
        return decode_response(response.status_code, response.json())
    except (httpx.HTTPError, ValueError):
        raise _McpError(-32603, 'AutoChess 本地服务暂不可用；重试保持相同 requestId') from None


def play(arguments, **identity):
    return request(prepare(arguments, **identity))
