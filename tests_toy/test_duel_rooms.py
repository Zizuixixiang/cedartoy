import json
import unittest
from io import BytesIO
from unittest.mock import patch

import server


class DuelRoomsPlatformTests(unittest.TestCase):
    def test_forwarder_allows_stake_and_invitation_actions_with_trusted_identity(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True}

        cases = (
            (
                {
                    "action": "new",
                    "player_id": "reported-ai",
                    "opponent_id": "reported-human",
                    "participant_ids": ["victim-ai", "reported-ai"],
                    "viewer": "victim-ai",
                    "game_type": "gomoku",
                    "mode": "ai_first",
                    "stake": 12,
                },
                {
                    "action": "new",
                    "player_id": "42:3",
                    "opponent_id": "trusted-human",
                    "game_type": "gomoku",
                    "mode": "ai_first",
                    "stake": 12,
                },
            ),
            (
                {
                    "action": "accept",
                    "player_id": "reported-ai",
                    "opponent_id": "reported-human",
                    "room_id": "ABCDEFGH",
                },
                {
                    "action": "accept",
                    "player_id": "42:3",
                    "room_id": "ABCDEFGH",
                },
            ),
            (
                {
                    "action": "join",
                    "player_id": "reported-ai",
                    "opponent_id": "reported-human",
                    "room_id": "ABCDEFGH",
                },
                {
                    "action": "join",
                    "player_id": "42:3",
                    "opponent_id": "trusted-human",
                    "room_id": "ABCDEFGH",
                },
            ),
            (
                {
                    "action": "reject",
                    "player_id": "reported-ai",
                    "opponent_id": "reported-human",
                    "room_id": "ABCDEFGH",
                },
                {
                    "action": "reject",
                    "player_id": "42:3",
                    "room_id": "ABCDEFGH",
                },
            ),
            (
                {
                    "action": "leave",
                    "player_id": "reported-ai",
                    "opponent_id": "reported-human",
                    "room_id": "ABCDEFGH",
                    "message": "先走了",
                },
                {
                    "action": "leave",
                    "player_id": "42:3",
                    "room_id": "ABCDEFGH",
                    "message": "先走了",
                },
            ),
        )
        for arguments, expected in cases:
            with self.subTest(action=arguments["action"]):
                with patch.object(
                    server.httpx, "post", return_value=FakeResponse()
                ) as post:
                    server._play_duel(
                        arguments,
                        trusted_opponent_id="trusted-human",
                        force_opponent=True,
                        trusted_player_id="42:3",
                    )
                self.assertEqual(post.call_args.kwargs["json"], expected)

    def test_forwarder_overrides_reported_identity_and_drops_unrelated_fields(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True, "rooms": []}

        arguments = {
            "game": "duel",
            "action": "rooms",
            "player_id": "victim-ai",
            "opponent_id": "victim-human",
            "room_id": "ABCDEFGH",
            "include_terminal": True,
            "limit": 7,
            "offset": 2,
        }
        with patch.object(server.httpx, "post", return_value=FakeResponse()) as post:
            result = server._play_duel(arguments, trusted_player_id="42:3")

        self.assertTrue(result["ok"])
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "action": "rooms",
                "player_id": "42:3",
                "include_terminal": True,
                "limit": 7,
                "offset": 2,
            },
        )

    def test_chips_forwarder_forces_canonical_ai_and_bound_human(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True, "status": "ok"}

        with patch.object(server.httpx, "post", return_value=FakeResponse()) as post:
            server._play_duel(
                {
                    "action": "chips",
                    "op": "ledger",
                    "limit": 7,
                    "player_id": "reported-ai",
                    "opponent_id": "reported-human",
                },
                trusted_player_id="42:3",
                trusted_opponent_id="trusted-human",
                force_opponent=True,
            )

        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "action": "chips",
                "op": "ledger",
                "limit": 7,
                "player_id": "42:3",
                "opponent_id": "trusted-human",
            },
        )

    def test_authenticated_ai_params_player_id_cannot_select_another_ai(self):
        captured = {}

        def fake_play(arguments, **kwargs):
            captured["arguments"] = arguments
            captured["kwargs"] = kwargs
            return {"ok": True, "rooms": []}

        account = {"id": 42, "username": "machine", "is_ai": 1}
        with (
            patch.object(server, "_current_account", return_value=account),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_play_duel", side_effect=fake_play),
            patch.object(server, "_stamp_save_owner"),
            patch.object(server, "_anti_addiction_record_success", return_value=""),
            patch.object(server, "_play_announcements", return_value=""),
        ):
            result = json.loads(server._tool_play_inner(
                {
                    "game": "duel",
                    "action": "rooms",
                    "player_id": "victim-top-level",
                    "params": {
                        "player_id": "victim-in-params",
                        "include_terminal": True,
                        "limit": 5,
                    },
                },
                path_token="trusted-token",
            ))

        self.assertTrue(result["ok"])
        self.assertEqual(captured["arguments"]["player_id"], "42")
        self.assertEqual(captured["kwargs"]["trusted_player_id"], "42")
        self.assertTrue(captured["kwargs"]["force_opponent"])

    def test_rooms_rejects_unauthenticated_reported_player_id(self):
        with patch.object(server, "_reject_claimed_guest"):
            with self.assertRaises(server._McpError) as raised:
                server._tool_play_inner(
                    {
                        "game": "duel",
                        "action": "rooms",
                        "params": {"player_id": "victim-ai"},
                    }
                )
        self.assertEqual(raised.exception.code, -32001)
        self.assertIn("已认证的 AI", raised.exception.message)

    def test_chips_rejects_human_account_before_forwarding(self):
        account = {"id": 7, "username": "human", "is_ai": 0}
        with (
            patch.object(server, "_current_account", return_value=account),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_play_duel") as play_duel,
        ):
            with self.assertRaises(server._McpError) as raised:
                server._tool_play_inner(
                    {"game": "duel", "action": "chips", "params": {"op": "check_in"}},
                    path_token="trusted-human-token",
                )
        self.assertEqual(raised.exception.code, -32001)
        play_duel.assert_not_called()

    def test_guide_documents_private_rooms_workflow(self):
        guide = json.loads(server._tool_get_guide({"game": "duel"}))["guide"]
        self.assertIn('action="rooms"', guide)
        self.assertIn("不是全局房间目录", guide)
        self.assertIn("pending / waiting / playing", guide)
        self.assertIn("include_terminal=true", guide)
        self.assertIn("limit 默认 50、最大 100", guide)
        self.assertIn("accept/reject", guide)
        self.assertIn("正常无需再 join", guide)

    def test_guide_documents_stake_confirmation_and_negative_chips(self):
        guide = json.loads(server._tool_get_guide({"game": "duel"}))["guide"]
        for expected in (
            "stake 必须是 >=0 的整数",
            "stake=0 直接开局",
            "stake>0 创建 pending 邀请",
            'action="accept"',
            'action="reject"',
            "筹码余额允许为负数",
            "双方必须重新确认",
        ):
            self.assertIn(expected, guide)

    def test_guide_documents_bootstrap_delta_state_and_ai_chips_flow(self):
        guide = json.loads(server._tool_get_guide({"game": "duel"}))["guide"]
        for expected in (
            "rooms -> accept/reject（如需）-> bootstrap -> move(wait=true) 增量循环",
            "仅在上下文丢失、复盘或怀疑局面不同步时调用",
            "不要每轮调用",
            "正常 move 不返回完整 room/棋盘/规则",
            "对方落子仍是原始坐标 payload",
            "ordered participants / current_actor",
            "按当前 canonical AI 投影",
            "private_state",
            "不能用 viewer / player_id 参数",
            "非当前参与者可传 wait=true",
            "current_actor_id/current_actor_seat",
            'action="chips"',
            "ledger 默认 5 条、最大 10",
            "正常开局已含双方余额，不必额外 chips/status",
        ):
            self.assertIn(expected, guide)

    def test_proxy_allows_only_current_duel_web_routes(self):
        allowed = {
            "GET": (
                "/", "/chips", "/api/whoami", "/api/chips",
                "/static/styles.css", "/static/app.js",
                "/static/chips.css", "/static/chips.js",
                "/api/chips/machines/42%3A3", "/api/rooms/ABCDEFGH",
            ),
            "POST": (
                "/api/rooms", "/api/chips/check-in", "/api/chips/bankruptcy",
                "/api/rooms/ABCDEFGH/invitation",
                "/api/rooms/ABCDEFGH/join", "/api/rooms/ABCDEFGH/move",
                "/api/rooms/ABCDEFGH/resign", "/api/rooms/ABCDEFGH/leave",
                "/api/rooms/ABCDEFGH/messages",
                "/api/rooms/ABCDEFGH/retention", "/api/rooms/ABCDEFGH/delete",
            ),
        }
        for method, paths in allowed.items():
            for path in paths:
                with self.subTest(method=method, path=path):
                    self.assertTrue(server._duel_proxy_allowed(method, path))

        denied = (
            ("GET", "/health"),
            ("GET", "/api/chips/machines/"),
            ("GET", "/api/chips/machines/ai/extra"),
            ("GET", "/api/rooms/abcdefghi"),
            ("POST", "/api/chips/machines/42%3A3/check-in"),
            ("POST", "/api/rooms/ABCDEFGH/arbitrary"),
            ("DELETE", "/api/rooms/ABCDEFGH"),
        )
        for method, path in denied:
            with self.subTest(method=method, path=path):
                self.assertFalse(server._duel_proxy_allowed(method, path))

    def test_chip_actions_use_trusted_header_without_forbidden_body_identity(self):
        captured = {}

        class FakeResponse:
            status = 200
            reason = "OK"

            @staticmethod
            def read():
                return b'{"ok":true}'

            @staticmethod
            def getheaders():
                return [("Content-Type", "application/json")]

            @staticmethod
            def getheader(name, default=None):
                return "application/json" if name == "Content-Type" else default

        class FakeConnection:
            def __init__(self, *_args, **_kwargs):
                pass

            def request(self, method, path, body=None, headers=None):
                captured.update(method=method, path=path, body=body, headers=headers)

            @staticmethod
            def getresponse():
                return FakeResponse()

            @staticmethod
            def close():
                pass

        raw_body = b'{"player_id":"reported-human"}'
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {
            "Content-Length": str(len(raw_body)),
            "Content-Type": "application/json",
        }
        handler.rfile = BytesIO(raw_body)
        handler.wfile = BytesIO()
        handler.client_address = ("127.0.0.1", 12345)
        handler.command = "POST"
        handler.send_response = lambda *_args, **_kwargs: None
        handler.send_header = lambda *_args, **_kwargs: None
        handler.end_headers = lambda: None
        target = {
            "human_player": "trusted-human",
            "human_name": "南山",
            "machines": [{"id": "42:3", "name": "小紫"}],
        }

        with patch.object(server.http.client, "HTTPConnection", FakeConnection):
            handler._proxy_to_duel(
                "POST", "/api/chips/check-in", "", target=target
            )

        self.assertEqual(json.loads(captured["body"]), {})
        self.assertEqual(
            captured["headers"]["X-Duel-Human-Player"], "trusted-human"
        )

    def test_chips_page_proxy_rewrites_static_asset_prefix(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/duel/chips?token=trusted"
        handler.headers = {}
        handler._duel_human_context = lambda _token: {
            "human_player": "human-1",
            "human_name": "南山",
            "machines": [],
        }
        captured = {}
        handler._proxy_to_duel = (
            lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs)
        )

        with (
            patch.object(server, "_path_token_user_id", return_value=1),
            patch.object(server, "_check_duel_web_request_rate_limit", return_value=True),
        ):
            handler._handle_duel_proxy("GET")

        self.assertEqual(captured["args"][:2], ("GET", "/chips"))
        self.assertTrue(captured["kwargs"]["rewrite_html"])


if __name__ == "__main__":
    unittest.main()
