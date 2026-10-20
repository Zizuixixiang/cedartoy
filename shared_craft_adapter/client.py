"""Python 3.10 compatible gateway client; no engine imports in CedarToy."""
from dataclasses import dataclass
import os
from urllib.parse import urlsplit

import httpx


@dataclass
class ContentResult:
    content: list


def request(user_id, method, path, *, body=None, query=""):
    secret = os.environ.get("SHARED_CRAFT_SECRET", "")
    origin = os.environ.get("SHARED_CRAFT_ORIGIN", "http://127.0.0.1:8793").rstrip("/")
    url = urlsplit(origin)
    if len(secret) < 32 or url.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("手作空间尚未配置独立服务")
    response = httpx.request(
        method, origin + "/" + path + ("?" + query if query else ""),
        headers={"X-Craft-Secret": secret, "X-Craft-User": str(user_id)},
        content=body, timeout=30, trust_env=False, follow_redirects=False,
    )
    return response


def play(user, arguments):
    import json
    params = arguments.get("params") or {}
    for _ in range(3):
        if not isinstance(params, str):
            break
        params = json.loads(params)
    if not isinstance(params, dict):
        raise ValueError("params 必须为对象")
    # Platform identity is never accepted in engine arguments. Untrusted IDs
    # cannot choose a room, actor, token or save slot.
    params = {k: v for k, v in params.items()
              if k not in {"player_id", "actor", "actor_id", "room", "room_id", "slot", "user_id"}}
    response = request(user["id"], "POST", "play", body=json.dumps(
        {"action": arguments.get("action"), "params": params}).encode())
    data = response.json()
    if response.status_code >= 400:
        raise ValueError(data.get("error", "手作空间暂时不可用"))
    # Original web gallery action returns JSON, original MCP returns content.
    return ContentResult(data["content"] if "content" in data else
                         [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}])
