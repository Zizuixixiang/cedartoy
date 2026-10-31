"""Proxy quota isolation; no live HTTP, accounts, or game saves are accessed."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import Mock, patch

import server


class DuelWebRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.web_buckets = {}
        self.mcp_buckets = {}
        self.now = 1000.0
        for patcher in (
            patch.object(server, "_DUEL_WEB_REQUEST_RATE_LIMIT", self.web_buckets),
            patch.object(server, "_REQUEST_RATE_LIMIT", self.mcp_buckets),
            patch.object(server.time, "time", side_effect=lambda: self.now),
            patch.object(server, "_path_token_user_id", side_effect=self.user_id),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def user_id(token):
        return {"human-a": 7, "human-a-renewed": 7, "human-b": 8}.get(token)

    def request(self, path, method="GET", *, token="human-a", via="cookie",
                ip="192.0.2.1", authenticated=True):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/duel" + path
        handler.command = method
        handler.headers = {}
        if token:
            if via == "query":
                handler.path += ("&" if "?" in path else "?") + "token=" + token
            elif via == "bearer":
                handler.headers["Authorization"] = "Bearer " + token
            else:
                handler.headers["Cookie"] = "duel_token=" + token
        handler.client_address = (ip, 12345)
        handler._client_ip = Mock(return_value=ip)
        handler.rfile, handler.wfile = BytesIO(), BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._duel_human_context = Mock(return_value={
            "human_player": "7", "human_name": "Test", "machines": [],
        })
        if not authenticated:
            handler._duel_human_context.side_effect = server._McpError(-32001, "未登录")
        handler._proxy_to_duel = Mock()
        handler._handle_duel_proxy(method)
        return handler

    def assert_proxied(self, handler):
        handler._proxy_to_duel.assert_called_once()
        handler.send_response.assert_not_called()

    def assert_limited(self, handler):
        handler._proxy_to_duel.assert_not_called()
        handler.send_response.assert_called_once_with(429)
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Retry-After"], "60")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(handler.wfile.getvalue()), {
            "error": server.REQUEST_RATE_LIMIT_MESSAGE, "code": 429,
        })

    def exhaust(self, path, method="GET", **kwargs):
        for _ in range(120):
            self.assert_proxied(self.request(path, method, **kwargs))
        limited = self.request(path, method, **kwargs)
        self.assert_limited(limited)
        return limited

    def fill_read_total(self):
        for room in ("ROOM0001", "ROOM0002", "ROOM0003", "ROOM0004", "ROOM0005"):
            self.exhaust("/api/rooms/" + room)

    def test_full_read_and_write_api_buckets_do_not_block_entry_chips_or_static(self):
        self.fill_read_total()
        self.assert_limited(self.request("/api/whoami"))
        self.assert_limited(self.request("/api/rooms/ABCDEFGH"))
        self.exhaust("/api/rooms/ABCDEFGH/move", "POST")
        for path in ("/", "/chips", "/static/app.js", "/static/styles.css",
                     "/static/games/chess.js", "/static/assets/exchange-shop/items/item.png",
                     "/api/npc-avatars/example.webp"):
            with self.subTest(path=path):
                handler = self.request(path, via="query")
                self.assert_proxied(handler)
                if path in ("/", "/chips"):
                    self.assertTrue(handler._proxy_to_duel.call_args.kwargs["rewrite_html"])
                    self.assertIn("duel_token=human-a", handler._proxy_to_duel.call_args.kwargs["set_cookies"][0])

    def test_page_and_static_loads_leave_full_api_allowances(self):
        self.exhaust("/")
        for _ in range(60):
            self.assert_proxied(self.request("/static/app.js"))
            self.assert_proxied(self.request("/api/npc-avatars/example.webp"))
        self.assert_limited(self.request("/static/styles.css"))
        self.assert_limited(self.request("/api/npc-avatars/another.png"))
        self.fill_read_total()
        self.exhaust("/api/rooms/ABCDEFGH/move", "POST")

    def test_ten_rooms_sync_every_three_seconds_with_general_reads_and_writes(self):
        for tick in range(20):
            self.now = 1000 + tick * 3
            for room in range(10):
                self.assert_proxied(self.request(f"/api/rooms/ROOM{room:04d}?revision={tick}"))
            self.assert_proxied(self.request("/api/notifications/unread"))
            self.assert_proxied(self.request("/api/rooms/ROOM0000/move", "POST"))
        self.assertEqual(len(self.web_buckets[("user:7", "read_total")]), 220)

    def test_room_local_rejection_does_not_charge_total_or_other_room(self):
        self.exhaust("/api/rooms/ABCDEFGH?wait=true")
        for _ in range(150):
            self.assert_limited(self.request("/api/rooms/ABCDEFGH?perspective=123"))
        self.assertEqual(len(self.web_buckets[("user:7", "read_total")]), 120)
        self.exhaust("/api/rooms/12345678")
        self.exhaust("/api/whoami")
        self.assertEqual(len(self.web_buckets[("user:7", "read_total")]), 360)

    def test_same_room_windows_queries_and_identity_transports_share_quota(self):
        for i in range(120):
            self.assert_proxied(self.request(
                f"/api/rooms/ABCDEFGH?revision={i}&perspective={i}&wait=true",
                via=("query", "cookie", "bearer")[i % 3],
                token=("human-a", "human-a-renewed")[i % 2], ip=f"192.0.2.{i + 1}"))
        self.assert_limited(self.request("/api/rooms/ABCDEFGH?wait=false&perspective=other"))
        self.assertEqual(len(self.web_buckets), 2)
        self.assert_proxied(self.request("/api/rooms/ABCDEFGH", token="human-b"))

    def test_general_reads_share_their_own_limit_and_do_not_block_rooms(self):
        self.exhaust("/api/whoami")
        for path in ("/api/chips", "/api/notifications/unread", "/api/spectating/rooms"):
            self.assert_limited(self.request(path))
        self.exhaust("/api/rooms/ABCDEFGH")

    def test_total_read_cap_includes_general_reads_and_is_account_scoped(self):
        self.exhaust("/api/whoami")
        for i in range(4):
            self.exhaust(f"/api/rooms/ROOM{i:04d}")
        before = {key: list(value) for key, value in self.web_buckets.items()}
        for i in range(100):
            self.assert_limited(self.request(f"/api/rooms/TEST{i:04d}"))
        self.assertEqual(self.web_buckets, before)
        self.assert_proxied(self.request("/api/rooms/TEST0000", token="human-b"))
        self.assert_proxied(self.request("/api/whoami", token="human-b"))
        self.exhaust("/api/rooms/ABCDEFGH/move", "POST")

    def test_room_bucket_growth_is_bounded_and_idle_buckets_are_pruned(self):
        for i in range(600):
            self.assert_proxied(self.request(f"/api/rooms/ROOM{i:04d}"))
        self.assertEqual(len(self.web_buckets), 601)
        for i in range(600, 620):
            self.assert_limited(self.request(f"/api/rooms/ROOM{i:04d}"))
        self.assertEqual(len(self.web_buckets), 601)
        self.now += 60
        self.assert_proxied(self.request("/api/rooms/NEXT0001"))
        self.assertEqual(set(self.web_buckets), {
            ("user:7", "read_total"), ("user:7", "room", "NEXT0001"),
        })

    def test_concurrent_admission_cannot_overrun_local_or_total_caps(self):
        def check(path):
            return server._check_duel_web_request_rate_limit("user:7", "GET", path)
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = list(pool.map(check, ["/api/rooms/ABCDEFGH"] * 160))
            self.assertEqual(sum(accepted), 120)
            accepted = list(pool.map(check, [f"/api/rooms/ROOM{i:04d}" for i in range(600)]))
            self.assertEqual(sum(accepted), 480)
        self.assertEqual(len(self.web_buckets[("user:7", "read_total")]), 600)

    def test_anonymous_reads_share_ip_cap_without_per_room_buckets_or_auth_bypass(self):
        for i in range(120):
            handler = self.request(f"/api/rooms/ROOM{i:04d}", token=None, authenticated=False)
            handler.send_response.assert_called_once_with(401)
            handler._proxy_to_duel.assert_not_called()
        self.assert_limited(self.request("/api/rooms/ABCDEFGH", token="invalid", authenticated=False))
        self.assertEqual(len(self.web_buckets), 2)
        other = self.request("/api/rooms/ABCDEFGH", token=None, ip="192.0.2.2", authenticated=False)
        other.send_response.assert_called_once_with(401)
        self.assert_proxied(self.request("/api/rooms/ABCDEFGH"))

    def test_write_quota_covers_all_mutations_without_blocking_reads(self):
        self.exhaust("/api/rooms/ABCDEFGH/move", "POST")
        for path in ("/api/rooms", "/api/rooms/12345678/messages",
                     "/api/chips/check-in", "/api/notifications/read"):
            self.assert_limited(self.request(path, "POST"))
        self.assert_proxied(self.request("/api/rooms/ABCDEFGH"))
        self.assert_proxied(self.request("/api/whoami"))
        self.assert_proxied(self.request("/api/rooms", "POST", token="human-b"))

    def test_user_bucket_survives_token_transport_renewal_and_ip_change(self):
        self.exhaust("/api/whoami")
        for via in ("query", "cookie", "bearer"):
            self.assert_limited(self.request("/api/chips", via=via,
                                            token="human-a-renewed", ip="192.0.2.2"))
            self.assert_proxied(self.request("/api/chips", via=via, token="human-b"))

    def test_anonymous_bucket_uses_ip_and_static_still_needs_no_login(self):
        self.exhaust("/static/app.js", token=None, authenticated=False)
        self.assert_limited(self.request("/static/styles.css", token="invalid"))
        other = self.request("/static/app.js", token=None, ip="192.0.2.2", authenticated=False)
        self.assert_proxied(other)
        other._duel_human_context.assert_not_called()
        self.assert_proxied(self.request("/", token=None, authenticated=False))

    def test_retry_after_recovers_at_window_boundary_and_rejections_do_not_extend_it(self):
        limited = self.exhaust("/api/whoami")
        headers = dict(call.args for call in limited.send_header.call_args_list)
        self.now += 59.99
        self.assert_limited(self.request("/api/whoami"))
        self.now = 1000 + int(headers["Retry-After"])
        self.assert_proxied(self.request("/api/whoami"))
        self.assertEqual(list(self.web_buckets.values()), [[1060.0], [1060.0]])

    def test_total_read_limit_recovers_after_retry_after(self):
        self.fill_read_total()
        self.now += 59.99
        self.assert_limited(self.request("/api/rooms/NEXT0001"))
        self.now = 1060
        self.assert_proxied(self.request("/api/rooms/NEXT0001"))
        self.assertEqual(len(self.web_buckets), 2)

    def test_root_mcp_and_duel_web_quotas_are_independent(self):
        self.fill_read_total()
        for _ in range(server.REQUEST_RATE_LIMIT_MAX):
            self.assertTrue(server._check_request_rate_limit("user:7"))
        self.assertFalse(server._check_request_rate_limit("user:7"))
        for _ in range(server.DUEL_REQUEST_RATE_LIMIT_MAX):
            self.assertTrue(server._check_request_rate_limit("duel:user:7", server.DUEL_REQUEST_RATE_LIMIT_MAX))
        self.assertFalse(server._check_request_rate_limit("duel:user:7", server.DUEL_REQUEST_RATE_LIMIT_MAX))
        self.assert_proxied(self.request("/"))
        self.assert_proxied(self.request("/api/rooms", "POST"))

    def test_login_token_transports_and_query_cookie_setting_are_unchanged(self):
        for via in ("query", "cookie", "bearer"):
            handler = self.request("/", via=via)
            self.assert_proxied(handler)
            handler._duel_human_context.assert_called_once_with("human-a")
            cookies = handler._proxy_to_duel.call_args.kwargs["set_cookies"]
            self.assertEqual(bool(cookies), via == "query")

    def test_auth_failure_still_allows_only_entry_and_static(self):
        self.assert_proxied(self.request("/", token=None, authenticated=False))
        self.assert_proxied(self.request("/static/app.js", token=None, authenticated=False))
        for path, method in (("/chips", "GET"), ("/api/whoami", "GET"),
                             ("/api/npc-avatars/example.webp", "GET"),
                             ("/api/rooms", "POST")):
            handler = self.request(path, method, token=None, authenticated=False)
            handler._proxy_to_duel.assert_not_called()
            handler.send_response.assert_called_once_with(401)

    def test_disallowed_paths_and_methods_do_not_consume_any_quota(self):
        for path, method in (("/api/admin", "GET"), ("/static/app.js", "POST"),
                             ("/api/rooms/abcdefgh", "GET"), ("/api/rooms/%41BCDEFGH", "GET"),
                             ("/api/rooms/ABCDEFGH/", "GET"),
                             ("/api/rooms/ABCDEFGH", "DELETE"), ("/", "HEAD")):
            handler = self.request(path, method)
            handler._proxy_to_duel.assert_not_called()
            handler.send_response.assert_called_once_with(404)
        self.assertEqual(self.web_buckets, {})

    def test_ticket_exchange_still_precedes_quota_and_is_not_forwarded(self):
        self.fill_read_total()
        self.exhaust("/")
        before = {key: list(value) for key, value in self.web_buckets.items()}
        with patch.object(server, "_consume_operit_web_ticket", return_value={"id": 7}) as consume, \
                patch.object(server, "_create_account_jwt", return_value="human-a"):
            handler = self.request("/?web_ticket=test-ticket", token=None)
        consume.assert_called_once_with("test-ticket")
        handler.send_response.assert_called_once_with(303)
        handler._proxy_to_duel.assert_not_called()
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Location"], "/duel/")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("duel_token=human-a", headers["Set-Cookie"])
        self.assertEqual(self.web_buckets, before)


if __name__ == "__main__":
    unittest.main()
