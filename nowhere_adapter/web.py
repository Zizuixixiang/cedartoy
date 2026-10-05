"""Authenticated allowlisted facade, never expose upstream web.app directly."""
import base64
from html import escape
from http.cookies import SimpleCookie
import json
from pathlib import Path
import re
import ssl
from urllib.parse import parse_qs, quote, unquote, urlsplit

from vendor_cmd_adapter.base import VendorCmdError
from . import handler, storage, radio

STATIC = Path(__file__).resolve().parents[1] / "vendor/nowhere/nowhere/static"
ASSETS = Path(__file__).with_name("assets")
READS = {"/state", "/history", "/marks", "/sightings", "/postcards", "/messages"}
POSTS = {"/message", "/open_door", "/walk", "/listen", "/look_around", "/ask", "/postcard", "/where_am_i", "/continue", "/mark", "/walk_to", "/wait"}


def allowed(method, path):
    return ((method == "GET" and path in READS)
            or (method == "POST" and (path in POSTS or re.fullmatch(r"/postcard/[0-9]+/reply", path)))
            or (method == "DELETE" and re.fullmatch(r"/postcard/[0-9]+", path)))


def render_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    # Retain the actual upstream UI; narrowly escape upstream HTML interpolations
    # which can contain imported/provider data. Gameplay source remains untouched.
    replacements = {
        '${escapeHtml(streamUrl)}': '${escapeHtml(window.nowhereRadioUrl(streamUrl))}',
        '电台流 ↗</a>`:""}</div>': '电台流 ↗</a>`:f.stream_unavailable===true?`<br><span class="traillink">${station?escapeHtml(station)+" 电台流":"该电台流"}已失效，可让小机再次 listen 寻找附近其他可用电台。</span>`:""}</div>',
        'function safeHttpUrl(value){': 'function safeHttpUrl(value){\n  if(typeof value!=="string"||!/^https?:\\/\\//i.test(value))return "";',
        '${frontImg}': '${escapeHtml(frontImg)}',
        '${c.sent_at?localDateTime(c.sent_at):"旧明信片未记录"}': '${esc(c.sent_at?localDateTime(c.sent_at):"旧明信片未记录")}',
        '${st.elevation!=null?"海拔 "+st.elevation+"m":""} ${st.weather||""}': '${esc(st.elevation!=null?"海拔 "+st.elevation+"m":"")} ${esc(st.weather||"")}',
    }
    for before, after in replacements.items():
        if before not in html:
            raise VendorCmdError("上游网页结构已变化，需重新验收适配")
        html = html.replace(before, after)
    html = html.replace('</head>', '<link rel="stylesheet" href="/nowhere/platform.css">\n<script src="/nowhere/platform.js"></script></head>')
    # Keep the upstream UI, with a radio hint above and an error-only overlay.
    status = '<div id="platform-status" role="status" aria-live="polite" hidden></div>'
    return html.replace('<body>', '<body>' + status).encode()


def render_radio(player, stream):
    stream = radio.authorize(player, stream)
    source = "/nowhere/radio/stream?player=" + quote(player, safe="") + "&url=" + quote(stream, safe="")
    values = {"stream": escape(source, quote=True),
              "journey": "/nowhere/?player=" + quote(player, safe="")}
    html = (ASSETS / "radio.html").read_text(encoding="utf-8")
    return re.sub(r'\{\{(stream|journey)\}\}', lambda m: values[m[1]], html).encode()


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


def secure_context(http):
    # Match browser Secure-cookie semantics, including local development. The
    # reverse proxy already supplies X-Forwarded-Proto; no gateway changes.
    forwarded = http.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    host = urlsplit("//" + http.headers.get("Host", "")).hostname
    return (forwarded == "https" or isinstance(getattr(http, "connection", None), ssl.SSLSocket)
            or host in {"localhost", "127.0.0.1", "::1"})


def access_page(http, message, status=401, recover=False):
    html = (ASSETS / "access.html").read_text()
    html = html.replace("{{message}}", escape(message)).replace("{{recover}}", "true" if recover else "false")
    http.close_connection = True
    return send(http, html.encode(), "text/html; charset=utf-8", status, {"Connection": "close"})


def failure(http, message, status):
    # Authentication/ownership rejects happen before reading a write's body.
    # Do not leave those bytes on an HTTP/1.1 connection for the next request.
    http.close_connection = True
    return send(http, json.dumps({"error": message}, ensure_ascii=False).encode(),
                "application/json; charset=utf-8", status, {"Connection": "close"})


def serve(http, platform):
    url = urlsplit(http.path)
    path = url.path.removeprefix("/nowhere") or "/"
    params = parse_qs(url.query)
    method = http.command
    try:
        # Only these public files; never StaticFiles mount or arbitrary vendor path.
        assets = {
            "/static/world110m.js": (STATIC / "world110m.js", "text/javascript; charset=utf-8"),
            "/static/relief.jpg": (STATIC / "relief.jpg", "image/jpeg"),
            "/platform.js": (ASSETS / "platform.js", "text/javascript; charset=utf-8"),
            "/platform.css": (ASSETS / "platform.css", "text/css; charset=utf-8"),
            "/access.js": (ASSETS / "access.js", "text/javascript; charset=utf-8"),
        }
        if method == "GET" and path in assets:
            file, mime = assets[path]
            return send(http, file.read_bytes(), mime)
        document = method == "GET" and path in {"/", "/radio"}
        if document and not secure_context(http):
            # Never exchange credentials for a cookie the browser will discard,
            # nor carry an old HTTP query token into an HTTPS redirect.
            return access_page(http, "请使用 HTTPS 安全页面查看私人旅程。切换后可能需要重新登录。", 426)
        cookies = SimpleCookie()
        try:
            cookies.load(http.headers.get("Cookie", ""))
        except Exception:
            pass
        bearer = platform._extract_bearer(http.headers)
        query_token = params.get("token", [""])[0] if method == "GET" and path == "/" else ""
        token = bearer or query_token or (unquote(cookies["nowhere_token"].value) if "nowhere_token" in cookies else "")
        try:
            user = platform._current_account(token)
        except ValueError:
            # Platform JWT decoding reports MCP connection advice. A web
            # session needs a normal 401 regardless of invalid/expired format.
            raise platform._McpError(-32001, "网页登录已失效") from None
        requested = params.get("player", [""])[0]
        target = platform._bound_ai_slot_target_for_user(user, requested)
        if not target:
            raise platform._McpError(-32003, "只能访问当前已绑定小机的乌有乡存档槽")
        player = target["player"]
        if document:
            cookie_headers = {}
            if bearer or query_token:
                cookie_headers["Set-Cookie"] = "nowhere_token=" + quote(token, safe="") + "; HttpOnly; SameSite=Strict; Path=/nowhere/; Secure"
            if path == "/radio":
                body = render_radio(player, params.get("url", [""])[0])
                if handler.save_summary(player) is None:
                    raise VendorCmdError("小机还没有乌有乡旅程，请先通过 MCP 开门")
                return send(http, body, "text/html; charset=utf-8", headers={
                    **cookie_headers,
                    "Content-Security-Policy": "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
                })
            if query_token or url.path == "/nowhere":
                headers = {"Location": "/nowhere/?player=" + quote(player, safe=""), **cookie_headers}
                return send(http, b"", "text/plain", 303, headers)
            if handler.save_summary(player) is None:
                raise VendorCmdError("小机还没有乌有乡旅程，请先通过 MCP 开门")
            return send(http, render_page(), "text/html; charset=utf-8", headers=cookie_headers)
        if method == "GET" and path == "/radio/stream":
            if not secure_context(http):
                return failure(http, "需要 HTTPS 安全连接", 426)
            return radio.stream(http, player, params.get("url", [""])[0])
        if method == "GET" and path.startswith("/static/postcards/"):
            filename = path.removeprefix("/static/")
            if not storage.IMAGE_RE.fullmatch(filename):
                return failure(http, "not found", 404)
            with storage.locked(player):
                archive = storage.read(player)
                encoded = (archive or {}).get("images", {}).get(filename)
            if not encoded:
                return failure(http, "not found", 404)
            return send(http, base64.b64decode(encoded, validate=True), "image/png")
        if not allowed(method, path):
            return failure(http, "not found", 404)
        body = {}
        if method in {"POST", "DELETE"}:
            # Cookie requests require a same-origin page. Bearer is explicit auth.
            origin = http.headers.get("Origin")
            if origin and urlsplit(origin).netloc != http.headers.get("Host"):
                raise platform._McpError(-32003, "拒绝跨站写入")
            if http.headers.get("Sec-Fetch-Site") == "cross-site":
                raise platform._McpError(-32003, "拒绝跨站写入")
            length = int(http.headers.get("Content-Length", "0"))
            if not 0 <= length <= 65536:
                raise ValueError("request too large")
            body = json.loads(http.rfile.read(length)) if length else {}
            if not isinstance(body, dict):
                raise ValueError("object required")
            # A supplied selector must agree even though it is never forwarded.
            for key in ("player", "player_id", "slot"):
                if key in body:
                    raise platform._McpError(-32003, "身份与槽位只能由已鉴权的入口选择")
        if path in {"/open_door", "/continue"}:
            action = "open_door" if path == "/open_door" else "continue_journey"
            result = handler.play({**body, "player_id": player, "action": action})
            return http._send_json(result)
        if method == "DELETE" and body.get("confirm") is not True:
            raise VendorCmdError("删除明信片须 confirm=true")
        result = handler.execute(player, {"action": "_web", "path": path, "method": method, "body": body})
        return http._send_json(result["body"], status=result["status"])
    except radio.RadioError as exc:
        if document:
            return access_page(http, str(exc), exc.status)
        return failure(http, str(exc), exc.status)
    except platform._McpError as exc:
        status = 401 if exc.code == -32001 else 403
        message = "登录已失效或尚未登录，请返回首页登录后继续。" if status == 401 else "无法查看这个旅程，请返回首页选择已绑定小机的存档槽位。"
        if method == "GET" and path in {"/", "/radio"}:
            return access_page(http, message, status, recover=True)
        return failure(http, message, status)
    except (VendorCmdError, ValueError, TypeError) as exc:
        if method == "GET" and path in {"/", "/radio"}:
            return access_page(http, str(exc), 400)
        return failure(http, str(exc), 400)
    except OSError:
        if method == "GET" and path in {"/", "/radio"}:
            return access_page(http, "乌有乡暂不可用，请稍后重试。", 503)
        return failure(http, "乌有乡文件或运行环境暂不可用", 503)
