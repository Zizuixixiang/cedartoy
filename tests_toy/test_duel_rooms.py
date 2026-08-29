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

    def test_future_six_player_shape_is_forwarded_without_identity_escape(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True}

        with patch.object(server.httpx, "post", return_value=FakeResponse()) as post:
            server._play_duel(
                {
                    "action": "new",
                    "player_id": "forged-npc",
                    "opponent_id": "forged-human",
                    "game_type": "future_game",
                    "target_player_count": 6,
                    "fill_with_npcs": True,
                },
                trusted_player_id="42:3",
                trusted_opponent_id="trusted-human",
                force_opponent=True,
            )
        self.assertEqual(post.call_args.kwargs["json"], {
            "action": "new",
            "player_id": "42:3",
            "opponent_id": "trusted-human",
            "game_type": "future_game",
            "target_player_count": 6,
            "fill_with_npcs": True,
        })

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

    def test_all_chips_operations_reach_backend_through_authenticated_play(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True, "status": "ok"}

        cases = (
            ("status", {"op": "status"}),
            ("check_in", {"op": "check_in"}),
            ("bankruptcy", {"op": "bankruptcy"}),
            ("ledger", {"op": "ledger", "limit": 7}),
            ("achievements", {"op": "achievements"}),
            ("loans_list", {"op": "loans", "loan_action": "list", "limit": 12}),
            (
                "loans_create",
                {
                    "op": "loans",
                    "loan_action": "create",
                    "principal": 20,
                    "daily_rate_micro_percent": 125_000,
                    "due_date": "2026-09-05",
                    "interest_cap_enabled": True,
                    "idempotency_key": "loan:create:0001",
                },
            ),
            (
                "loans_accept",
                {
                    "op": "loans",
                    "loan_action": "accept",
                    "loan_id": "ln_0123456789abcdef",
                    "loan_revision": 2,
                    "idempotency_key": "loan:accept:0001",
                },
            ),
            (
                "loans_reject",
                {
                    "op": "loans",
                    "loan_action": "reject",
                    "loan_id": "ln_0123456789abcdef",
                    "loan_revision": 2,
                    "idempotency_key": "loan:reject:0001",
                },
            ),
            (
                "loans_counter",
                {
                    "op": "loans",
                    "loan_action": "counter",
                    "loan_id": "ln_0123456789abcdef",
                    "loan_revision": 2,
                    "principal": 25,
                    "daily_rate_micro_percent": 50_000,
                    "due_date": "2026-09-06",
                    "interest_cap_enabled": False,
                    "idempotency_key": "loan:counter:0001",
                },
            ),
            (
                "loans_withdraw",
                {
                    "op": "loans",
                    "loan_action": "withdraw",
                    "loan_id": "ln_0123456789abcdef",
                    "loan_revision": 2,
                    "idempotency_key": "loan:withdraw:0001",
                },
            ),
            (
                "loans_repay",
                {
                    "op": "loans",
                    "loan_action": "repay",
                    "loan_id": "ln_0123456789abcdef",
                    "amount": 9,
                    "idempotency_key": "loan:repay:0001",
                },
            ),
            ("exchange_catalog", {"op": "exchange", "exchange_action": "catalog"}),
            (
                "exchange_list",
                {"op": "exchange", "exchange_action": "list", "limit": 17},
            ),
            (
                "exchange_create",
                {
                    "op": "exchange",
                    "exchange_action": "create",
                    "item_key": "custom",
                    "request_note": "完成约定后收取筹码",
                    "custom_title": "自定义约定",
                    "chip_amount": 20,
                    "idempotency_key": "exchange:create:0001",
                },
            ),
            (
                "exchange_confirm",
                {
                    "op": "exchange",
                    "exchange_action": "confirm",
                    "request_id": "ex_0123456789abcdef",
                    "idempotency_key": "exchange:confirm:0001",
                },
            ),
            (
                "exchange_reject",
                {
                    "op": "exchange",
                    "exchange_action": "reject",
                    "request_id": "ex_0123456789abcdef",
                    "idempotency_key": "exchange:reject:0001",
                },
            ),
            (
                "exchange_withdraw",
                {
                    "op": "exchange",
                    "exchange_action": "withdraw",
                    "request_id": "ex_0123456789abcdef",
                    "idempotency_key": "exchange:withdraw:0001",
                },
            ),
        )
        account = {"id": 42, "username": "machine", "is_ai": 1}
        with (
            patch.object(server, "_current_account", return_value=account),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(
                server, "_duel_bound_human_player_id", return_value="trusted-human"
            ),
            patch.object(server.httpx, "post", return_value=FakeResponse()) as post,
            patch.object(server, "_stamp_save_owner"),
            patch.object(server, "_anti_addiction_record_success", return_value=""),
            patch.object(server, "_play_announcements", return_value=""),
        ):
            for label, params in cases:
                with self.subTest(operation=label):
                    post.reset_mock()
                    result = json.loads(server._tool_play_inner(
                        {
                            "game": "duel",
                            "action": "chips",
                            "player_id": "reported-top-level-ai",
                            "opponent_id": "reported-top-level-human",
                            "params": {
                                **params,
                                "player_id": "reported-params-ai",
                                "opponent_id": "reported-params-human",
                                "viewer": "victim-ai",
                                "unknown_chips_field": "must-not-pass",
                            },
                        },
                        path_token="trusted-token",
                    ))

                    self.assertTrue(result["ok"])
                    self.assertEqual(
                        post.call_args.kwargs["json"],
                        {
                            "action": "chips",
                            "player_id": "42",
                            "opponent_id": "trusted-human",
                            **params,
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
            "NPC 最多补四个创建时空座",
            "不写全局钱包",
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
            "async gateway 会在程序内吞掉 Duel 后端每 30 秒的 still_waiting",
            "默认服务端最多连续等待 10 分钟",
            "只有绕过官方网关直连 8772",
            "current_actor_id/current_actor_seat",
            "allowed_player_counts",
            "NPC 最多补四个",
            "NPC provider",
            'action="chips"',
            "ledger 默认 5 条、最大 10",
            "正常开局已含双方余额，不必额外 chips/status",
        ):
            self.assertIn(expected, guide)

    def test_guide_documents_complete_machine_chip_center(self):
        guide = json.loads(server._tool_get_guide({"game": "duel"}))["guide"]
        self.assertNotIn("成就、互动、借款尚无接口", guide)
        for expected in (
            'params={"op":"status"}',
            'params={"op":"check_in"}',
            'params={"op":"bankruptcy"}',
            'params={"op":"achievements"}',
            'params={"op":"loans","loan_action":"list","limit":20}',
            "小机向绑定人类借款",
            "小机是借款人；只有借款方能发起",
            '"loan_action":"create"',
            '"loan_action":"accept"',
            '"loan_action":"counter"',
            "用户文案叫“改条件”",
            '"loan_action":"reject"',
            '"loan_action":"withdraw"',
            '"loan_action":"repay"',
            '"exchange_action":"catalog"',
            '"exchange_action":"list"',
            '"exchange_action":"create"',
            '"exchange_action":"confirm"',
            "发起方承诺完成约定并收取 chip_amount 筹码",
            "审批方 confirm 后才支付筹码",
            "confirm / reject / withdraw 均必填 request_id",
            "8-128 位 idempotency_key",
        ):
            self.assertIn(expected, guide)

    def test_proxy_allows_only_current_duel_web_routes(self):
        allowed = {
            "GET": (
                "/", "/chips", "/api/whoami", "/api/chips",
                "/static/styles.css", "/static/app.js",
                "/static/chips.css", "/static/chips.js",
                "/static/assets/exchange-shop/items/good_life.png",
                "/api/chips/machines/42%3A3", "/api/rooms/ABCDEFGH",
                "/api/chips/exchanges", "/api/chips/exchanges/catalog",
                "/api/npc-avatars/example.webp",
            ),
            "POST": (
                "/api/rooms", "/api/chips/check-in", "/api/chips/bankruptcy",
                "/api/chips/exchanges", "/api/chips/loans",
                "/api/chips/exchanges/ex_0123456789abcdef/confirm",
                "/api/chips/exchanges/ex_0123456789abcdef/reject",
                "/api/chips/exchanges/ex_0123456789abcdef/withdraw",
                "/api/chips/loans/ln_0123456789abcdef/accept",
                "/api/chips/loans/ln_0123456789abcdef/reject",
                "/api/chips/loans/ln_0123456789abcdef/counter",
                "/api/chips/loans/ln_0123456789abcdef/withdraw",
                "/api/chips/loans/ln_0123456789abcdef/repay",
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
            ("GET", "/api/chips/loans"),
            ("GET", "/api/rooms/abcdefghi"),
            ("GET", "/api/npc-avatars/../secret.png"),
            ("GET", "/api/npc-avatars/example.svg"),
            ("GET", "/static/assets/exchange-shop/items/../good_life.png"),
            ("GET", "/static/assets/exchange-shop/items/%2e%2e/good_life.png"),
            ("GET", "/static/assets/exchange-shop/items/good_life.svg"),
            ("GET", "/static/assets/exchange-shop/item/good_life.png"),
            ("GET", "/static/assets/exchange-shop/items/nested/good_life.png"),
            ("POST", "/api/chips/machines/42%3A3/check-in"),
            ("POST", "/api/chips/exchanges/ex_0123456789abcdef/accept"),
            ("POST", "/api/chips/exchanges/ex_0123456789abcde/confirm"),
            ("POST", "/api/chips/exchanges/ex_0123456789abcdef/confirm/extra"),
            ("POST", "/api/chips/loans/ln_0123456789abcdef/confirm"),
            ("POST", "/api/chips/loan/ln_0123456789abcdef/repay"),
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

        handler = object.__new__(server.CedarToyHandler)
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

        paths = (
            "/api/chips/check-in", "/api/chips/bankruptcy",
            "/api/chips/exchanges", "/api/chips/loans",
            "/api/chips/exchanges/ex_0123456789abcdef/confirm",
            "/api/chips/exchanges/ex_0123456789abcdef/reject",
            "/api/chips/exchanges/ex_0123456789abcdef/withdraw",
            "/api/chips/loans/ln_0123456789abcdef/accept",
            "/api/chips/loans/ln_0123456789abcdef/reject",
            "/api/chips/loans/ln_0123456789abcdef/counter",
            "/api/chips/loans/ln_0123456789abcdef/withdraw",
            "/api/chips/loans/ln_0123456789abcdef/repay",
        )
        for path in paths:
            with self.subTest(path=path):
                captured.clear()
                raw_body = b'{"player_id":"reported-human"}'
                handler.headers = {
                    "Content-Length": str(len(raw_body)),
                    "Content-Type": "application/json",
                }
                handler.rfile = BytesIO(raw_body)
                handler.wfile = BytesIO()
                with patch.object(
                    server.http.client, "HTTPConnection", FakeConnection
                ):
                    handler._proxy_to_duel("POST", path, "", target=target)

                self.assertEqual(json.loads(captured["body"]), {})
                self.assertEqual(
                    captured["headers"]["X-Duel-Human-Player"],
                    "trusted-human",
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
