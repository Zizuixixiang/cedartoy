"""Garden/Camping HTTP extraction contracts; only in-memory upstream fixtures."""

import hashlib
from contextlib import ExitStack
from email.message import Message
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs

install_missing_optional_game_stubs()
import server


TARGET = {"player": "42:3", "owner_name": "阿橘", "slot": 3}
USER = {"id": 1, "username": " 小满 ", "is_ai": False}


class SatelliteProxyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Fail closed if any fixture accidentally reaches persistent state or I/O.
        for name in ("_db_connect", "_sessions_db_connect", "_current_account"):
            self.stack.enter_context(patch.object(server, name, side_effect=AssertionError(name)))
        self.stack.enter_context(patch.object(server, "_game_maintenance", return_value=None))
        self.activity = self.stack.enter_context(patch.object(server, "_record_web_game_activity"))
        self.connection_factory = self.stack.enter_context(patch.object(server.http.client, "HTTPConnection"))

    def handler(self, headers=(), body=b"", command="GET"):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = Message()
        for key, value in headers:
            handler.headers[key] = value
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.command = command
        handler.client_address = ("127.0.0.9", 1234)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_json = Mock()
        handler._garden_cat_bound_target = Mock(return_value=TARGET)
        return handler

    def forward(self, game, path="/", query="", raw=b"{}", status=200,
                headers=(), body=b"", target=TARGET, command="GET", fail_at=None):
        handler = self.handler(headers, body, command)
        handler._gc_prefix_override = "/fixture-garden"
        conn = self.connection_factory.return_value
        conn.reset_mock()
        conn.request.side_effect = None
        conn.getresponse.side_effect = None
        response = conn.getresponse.return_value
        response.read.side_effect = None
        response.status, response.reason = status, "fixture reason"
        response.read.return_value = raw
        response.getheaders.return_value = [
            ("Content-Type", "text/plain"), ("Content-Length", "999"),
            ("Connection", "close"), ("Transfer-Encoding", "chunked"),
            ("Set-Cookie", "upstream=1"), ("X-Fixture", "kept"),
        ]
        if fail_at:
            {"request": conn.request, "response": conn.getresponse,
             "read": response.read}[fail_at].side_effect = OSError("fixture failure")
        kwargs = {"set_cookie": "platform=1", "human_name": " 小满 "} if game == "garden_cat" else {"set_cookies": ["platform=1", "player=42"]}
        getattr(handler, "_proxy_to_" + game)(
            command, path, query, target=target, activity_user=USER, **kwargs)
        return handler, conn

    def test_exact_allowed_paths_and_server_policy_patch_points(self):
        expected = {
            "garden_cat": {
                "GET": {"/", "/api/catalog", "/web/status", "/web/notes", "/static/x.js"},
                "POST": {"/web/notes", "/web/register", "/web/cmd", "/web/new_game", "/web/move_with_cat", "/web/bouquets/read"},
            },
            "camping_plaza": {
                "GET": {"/", "/api/health", "/api/state", "/api/actions", "/api/growth", "/api/achievements", "/styles/a.css", "/scripts/overview.js", "/assets/a.png"},
                "POST": {"/api/session", "/api/player/name", "/api/turn/advance", "/api/turn/plan", "/api/day/end", "/api/day/start", "/api/action", "/api/nature-observation/intro/seen"},
            },
        }
        paths = {"/web/catalog", "/web/water", "/web/pet_cat", "/mcp/state", "/internal/saves/delete", "/static", "/styles", "//", "/api/state/"}
        for rules in expected.values():
            paths.update(rules["GET"] | rules["POST"])
        for game, rules in expected.items():
            allowed = getattr(server, "_" + game + "_proxy_allowed")
            for method in ("GET", "POST", "HEAD", "DELETE", "PUT", "get"):
                for path in paths:
                    with self.subTest(game=game, method=method, path=path):
                        self.assertEqual(allowed(method, path), path in rules.get(method, set()))
        with patch.object(server, "GARDEN_CAT_PROXY_GET_PATHS", {"/fixture"}):
            self.assertTrue(server._garden_cat_proxy_allowed("GET", "/fixture"))
            self.assertFalse(server._garden_cat_proxy_allowed("GET", "/"))
        with patch.object(server, "GARDEN_CAT_PROXY_POST_PATHS", {"/fixture"}):
            self.assertTrue(server._garden_cat_proxy_allowed("POST", "/fixture"))
            self.assertFalse(server._garden_cat_proxy_allowed("POST", "/web/notes"))

    def test_cookie_parsing_preserves_first_match_and_unquote_semantics(self):
        for raw, expected in (
            ("", None), ("other=1", None), ("NAME=wrong", None),
            ("name=a%3Ab+%2B%3D; name=second", "a:b++="),
            (" name= ; name=second", ""), ("name", ""),
            ("name=%ZZ", "%ZZ"), ("name=a=b", "a=b"),
        ):
            for game in ("garden_cat", "camping_plaza"):
                name = game + "_token"
                handler = self.handler([("Cookie", raw.replace("name", name))])
                actual = handler._garden_cat_cookie_token() if game == "garden_cat" else handler._camping_plaza_cookie(name)
                self.assertEqual(actual, expected)

    def test_auth_errors_static_bypass_and_maintenance_order(self):
        for game, root, static in (("garden_cat", "/garden-cat", "/static/x.js"), ("camping_plaza", "/camping-plaza", "/styles/x.css")):
            for path, user, target, status, payload in (
                ("/internal/saves/delete", USER, TARGET, 404, {"error": "not found"}),
                ("/", server._McpError(-32001, "bad"), TARGET, 401, {"error": "未登录，请先在首页登录", "code": 401}),
                ("/", {"is_ai": True}, TARGET, 401, {"error": "未登录，请先在首页登录", "code": 401}),
                ("/", USER, None, 403, {"error": "你没有绑定这只小机或槽位无效", "code": 403}),
                (static, USER, None, None, None),
            ):
                with self.subTest(game=game, path=path, status=status):
                    handler = self.handler()
                    handler.path = root + path + "?token=fixture&player=42%3A3"
                    handler._garden_cat_bound_target.return_value = target
                    proxy = Mock()
                    setattr(handler, "_proxy_to_" + game, proxy)
                    with patch.object(server, "_current_account", return_value=user, side_effect=user if isinstance(user, Exception) else None) as account:
                        getattr(handler, "_handle_" + game + "_proxy")("GET")
                    if status is not None:
                        handler._send_json.assert_called_once_with(payload, status=status)
                        proxy.assert_not_called()
                    else:
                        account.assert_not_called()
                        handler._garden_cat_bound_target.assert_not_called()
                        self.assertIsNone(proxy.call_args.kwargs["target"])
                        self.assertIsNone(proxy.call_args.kwargs["activity_user"])
        handler = self.handler()
        handler.path = "/camping-plaza/internal/not-allowed"
        with patch.object(server, "_game_maintenance", return_value={"message": "fixture maintenance"}):
            handler._handle_camping_plaza_proxy("GET")
        handler._send_json.assert_called_once_with({"error": "fixture maintenance", "maintenance": True}, status=503)

    def test_token_precedence_cookie_attributes_player_fallback_and_path_mapping(self):
        for game in ("garden_cat", "camping_plaza"):
            prefix = "/" + game.replace("_", "-")
            for query, cookie, expected_token in (
                ("token=query&player=42%3A3", "cookie", "query"),
                ("player=42%3A3", "cookie", "cookie"),
                ("player=42%3A3", "", "bearer"),
                ("token=&player=42%3A3", "cookie", "cookie"),
            ):
                handler = self.handler([("Cookie", game + "_token=" + cookie), ("Authorization", "Bearer bearer")])
                handler.path = prefix + "/?" + query
                proxy = Mock()
                setattr(handler, "_proxy_to_" + game, proxy)
                with patch.object(server, "_current_account", return_value=USER) as account, patch.object(server, "HUMAN_TOKEN_SECONDS", 123):
                    getattr(handler, "_handle_" + game + "_proxy")("GET")
                account.assert_called_once_with(expected_token)
                handler._garden_cat_bound_target.assert_called_once_with(USER, "42:3")
                self.assertEqual(proxy.call_args.args, ("GET", "/web/" if game == "garden_cat" else "/", query))
                cookies = proxy.call_args.kwargs.get("set_cookies") or [proxy.call_args.kwargs.get("set_cookie")]
                if expected_token == "query":
                    self.assertEqual(cookies[0], f"{game}_token=query; Path={prefix}; HttpOnly; SameSite=Lax; Max-Age=123")
                    if game == "camping_plaza":
                        self.assertEqual(cookies[1], "camping_plaza_player=42%3A3; Path=/camping-plaza; HttpOnly; SameSite=Lax; Max-Age=123")
                else:
                    self.assertEqual(cookies, [None])
            handler = self.handler([("Cookie", game + "_player=42%3A3")])
            handler.path = prefix + "/"
            setattr(handler, "_proxy_to_" + game, Mock())
            with patch.object(server, "_current_account", return_value=USER):
                getattr(handler, "_handle_" + game + "_proxy")("GET")
            handler._garden_cat_bound_target.assert_called_once_with(USER, "42:3" if game == "camping_plaza" else "")

    def test_forwarding_filters_untrusted_identity_and_preserves_transport(self):
        headers = [("hOsT", "bad"), ("aUtHoRiZaTiOn", "bad"), ("cOoKiE", "bad"),
                   ("x-player-id", "bad"), ("X-Player-Id", "also bad"),
                   ("X-GARDEN-OWNER-NAME", "bad"), ("x-garden-new-identity", "bad"),
                   ("X-Forwarded-For", "bad"), ("X-Forwarded-Prefix", "bad"),
                   ("Connection", "close"), ("X-Extra-Hop", "bad"),
                   ("Content-Length", "3"), ("Accept", "text/plain")]
        for game in ("garden_cat", "camping_plaza"):
            for target in (TARGET, None):
                with self.subTest(game=game, target=target), patch.object(server, "HOP_BY_HOP_HEADERS", server.HOP_BY_HOP_HEADERS | {"x-extra-hop"}):
                    handler, conn = self.forward(game, "/fixture", "token=secret&player=bad&a=1&b=&a=2&%74oken=again&flag&v=a%2Bb", headers=headers, body=b"abcTAIL", target=target, command="POST")
                conn.request.assert_called_once()
                call = conn.request.call_args
                self.assertEqual(call.args, ("POST", "/fixture?a=1&a=2&b=&flag=&v=a%2Bb"))
                self.assertEqual(call.kwargs["body"], b"abc")
                self.assertEqual(handler.rfile.read(), b"TAIL")
                forwarded = call.kwargs["headers"]
                expected = {"Content-Length": "3", "Accept": "text/plain", "Host": game.replace("_", "-") + ".local", "X-Forwarded-For": "127.0.0.9", "X-Forwarded-Prefix": "/fixture-garden" if game == "garden_cat" else "/camping-plaza"}
                if target:
                    expected["X-Player-Id"] = "42:3"
                    if game == "garden_cat":
                        expected.update({"X-Garden-Player": "42:3", "X-Garden-Owner-Name": "%E9%98%BF%E6%A9%98", "X-Garden-Slot": "3", "X-Garden-Human-Name": "%E5%B0%8F%E6%BB%A1"})
                self.assertEqual(forwarded, expected)
                self.connection_factory.assert_called_with(getattr(server, game.upper() + "_HOST"), getattr(server, game.upper() + "_PORT"), timeout=60)
                conn.close.assert_called_once()

    def test_upstream_failures_close_connection_and_keep_502_payload(self):
        for game, label in (("garden_cat", "Garden-Cat"), ("camping_plaza", "Camping Plaza")):
            for stage in ("request", "response", "read"):
                handler, conn = self.forward(game, fail_at=stage)
                handler._send_json.assert_called_once_with({"error": label + " 代理失败", "detail": "fixture failure"}, status=502)
                conn.close.assert_called_once()
                handler.send_response.assert_not_called()
        self.activity.assert_not_called()

    def test_response_status_headers_cookies_head_and_activity_bytes(self):
        for game in ("garden_cat", "camping_plaza"):
            for status in (200, 302, 401, 403, 404, 502):
                for command in ("GET", "HEAD"):
                    handler, conn = self.forward(game, raw=b"opaque\xff", status=status, command=command)
                    handler.send_response.assert_called_once_with(status, "fixture reason")
                    sent = [call.args for call in handler.send_header.call_args_list]
                    expected = [("Content-Type", "text/plain"), ("Set-Cookie", "upstream=1"), ("X-Fixture", "kept"), ("Set-Cookie", "platform=1")]
                    if game == "camping_plaza":
                        expected.append(("Set-Cookie", "player=42"))
                    self.assertEqual(sent, expected + [("Content-Length", "7")])
                    self.assertEqual(handler.wfile.getvalue(), b"" if command == "HEAD" else b"opaque\xff")
                    self.activity.assert_called_with(game, command, "/", status, b"opaque\xff", USER, None)
                    handler.end_headers.assert_called_once()

    def test_camping_rewrite_bytes_status_and_exact_resource_scope(self):
        html = b'<head><link href="styles/a.css"></head></head><script src="scripts/overview.js"></script><img src="assets/a.png">\xff'
        js = b'''fetch('/api/state'); fetch("/api/actions"); 'assets/a.png'; "assets/b.png"; `/api/keep`; /api/bare'''
        expected_js = b'''fetch('/camping-plaza/api/state'); fetch("/camping-plaza/api/actions"); '/camping-plaza/assets/a.png'; "/camping-plaza/assets/b.png"; `/api/keep`; /api/bare'''
        for status in (200, 302, 399, 400, 404, 502):
            handler, _ = self.forward("camping_plaza", raw=html, status=status)
            body = handler.wfile.getvalue()
            self.activity.assert_called_with("camping_plaza", "GET", "/", status, html, USER, None)
            self.assertEqual(handler.send_header.call_args.args, ("Content-Length", str(len(body))))
            if status < 400:
                start = body.index(b'\n<style')
                end = body.index(b'</style>\n') + len(b'</style>\n')
                css = body[start:end]
                # SHA-256 of the literal bytes in baseline 46e079a.
                self.assertEqual(hashlib.sha256(css).hexdigest(), "099e7b472db9aac1359f8b8868b092b3f46c60d221d62f0cddb8434562e3268e")
                self.assertEqual(body[:start] + body[end:], html.replace(b'="styles/', b'="/camping-plaza/styles/').replace(b'="scripts/', b'="/camping-plaza/scripts/').replace(b'="assets/', b'="/camping-plaza/assets/'))
                self.assertEqual(body.count(b'cedartoy-camping-mobile'), 1)
            else:
                self.assertEqual(body, html)
            handler, _ = self.forward("camping_plaza", "/scripts/overview.js", raw=js, status=status)
            self.assertEqual(handler.wfile.getvalue(), expected_js if status < 400 else js)
        for path in ("/scripts/other.js", "/styles/main.css", "/assets/a.png", "/api/state"):
            handler, _ = self.forward("camping_plaza", path, raw=html + js)
            self.assertEqual(handler.wfile.getvalue(), html + js)
        handler, _ = self.forward("garden_cat", "/web/", raw=html + js)
        self.assertEqual(handler.wfile.getvalue(), html + js)


if __name__ == "__main__":
    unittest.main()
