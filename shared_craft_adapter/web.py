"""Same-origin facade. Every request authenticates a current platform account."""
from http.cookies import SimpleCookie
import json
import ssl
from urllib.parse import urlsplit

import httpx

from . import client


def send(http, body, content_type, status=200, headers=None):
    http.send_response(status)
    http.send_header("Content-Type", content_type)
    http.send_header("Content-Length", str(len(body)))
    http.send_header("Cache-Control", "no-store")
    http.send_header("Referrer-Policy", "no-referrer")
    http.send_header("X-Content-Type-Options", "nosniff")
    http.send_header("X-Frame-Options", "SAMEORIGIN")
    for key, value in (headers or {}).items():
        http.send_header(key, value)
    http.end_headers()
    http.wfile.write(body)


def serve(http, platform):
    url = urlsplit(http.path)
    path = url.path.removeprefix("/shared-craft/").lstrip("/")
    try:
        cookies = SimpleCookie()
        cookies.load(http.headers.get("Cookie", ""))
        bearer = platform._extract_bearer(http.headers)
        token = bearer or (cookies["shared_craft_token"].value if "shared_craft_token" in cookies else "")
        user = platform._current_account(token)
        if http.command in {"POST", "DELETE"}:
            origin = http.headers.get("Origin")
            if origin and urlsplit(origin).netloc != http.headers.get("Host"):
                raise PermissionError("请求来源不符")
            if not bearer and not origin:
                raise PermissionError("请求缺少来源")
        if path == "session" and http.command == "POST":
            if not bearer:
                raise PermissionError("需要登录")
            # Verify membership without opening a room or changing any data.
            from .identity import resolve
            resolve(platform.TURTLE_DB_PATH, user["id"])
            secure_transport = (http.headers.get("X-Forwarded-Proto") == "https" or
                                isinstance(getattr(http, "connection", None), ssl.SSLSocket))
            if not secure_transport and urlsplit("//" + http.headers.get("Host", "")).hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise PermissionError("请使用 HTTPS 打开手作空间")
            secure = "; Secure" if secure_transport else ""
            return send(http, b'{"ok":true}', "application/json", headers={
                "Set-Cookie": f"shared_craft_token={token}; HttpOnly; SameSite=Strict; Path=/shared-craft/{secure}"})
        if path in {"", "shared-craft"} and http.command == "GET":
            return send(http, b"", "text/plain", 303, {"Location": "/shared-craft/crafts/"})
        length = int(http.headers.get("Content-Length", "0"))
        if length < 0 or length > 17_000_000:
            raise ValueError("请求过大")
        body = http.rfile.read(length) if http.command == "POST" else None
        response = client.request(user["id"], http.command, path, body=body, query=url.query)
        return send(http, response.content, response.headers.get("content-type", "application/json"),
                    response.status_code)
    except platform._McpError as exc:
        status, message = 401, exc.message
    except PermissionError as exc:
        status, message = 403, str(exc)
    except (ValueError, httpx.HTTPError) as exc:
        status, message = 503, "手作空间暂时不可用" if isinstance(exc, httpx.HTTPError) else str(exc)
    # Close if an authentication rejection left request bytes unread.
    http.close_connection = True
    return send(http, json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode(),
                "application/json; charset=utf-8", status, {"Connection": "close"})
