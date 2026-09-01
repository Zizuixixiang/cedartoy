import base64
import json
import sqlite3
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class DuelRoomsPlatformTests(unittest.TestCase):
    def test_play_schema_documents_duel_move_siblings_for_all_clients(self):
        for tools in (server._PLATFORM_TOOLS, server._KELIVO_PLATFORM_TOOLS):
            with self.subTest(tools=tools):
                play = next(tool for tool in tools if tool["name"] == "play")
                properties = play["inputSchema"]["properties"]["params"][
                    "properties"
                ]
                self.assertTrue({
                    "room_id", "move", "revision", "wait", "full_state",
                    "message",
                } <= properties.keys())
                self.assertIn("与 move 同级", properties["message"]["description"])
                self.assertIn("只放游戏动作字段", properties["move"]["description"])
                self.assertIn("不要提到 duel 外层", properties["move"]["description"])
                self.assertIn(
                    "join/move/state/resign/leave",
                    properties["message"]["description"],
                )
                self.assertIn("最近成功响应", properties["revision"]["description"])
                self.assertIn("409", properties["revision"]["description"])
                self.assertIn("仅 duel state 使用", properties["full_state"]["description"])

    def test_duel_move_message_is_lifted_without_loosening_game_actions(self):
        strict_game_moves = (
            (
                "aeroplane_chess roll",
                {"action": "roll", "message": "起飞"},
                {"action": "roll"},
            ),
            (
                "aeroplane_chess move",
                {
                    "action": "move",
                    "plane_id": "red-0",
                    "plane_index": 0,
                    "message": "走你",
                },
                {"action": "move", "plane_id": "red-0", "plane_index": 0},
            ),
            (
                "connect4",
                {"col": 3, "message": "这一列"},
                {"col": 3},
            ),
        )
        for label, move, expected_move in strict_game_moves:
            with self.subTest(game=label):
                original = dict(move)
                payload = server._prepare_duel_payload({
                    "action": "move",
                    "player_id": "ai-1",
                    "room_id": "ABCDEFGH",
                    "move": move,
                    "revision": 4,
                })
                self.assertEqual(payload["move"], expected_move)
                self.assertEqual(payload["message"], original["message"])
                self.assertEqual(move, original)

        top_level_wins = server._prepare_duel_payload({
            "action": "move",
            "player_id": "ai-1",
            "room_id": "ABCDEFGH",
            "move": {"action": "roll", "message": "内部消息"},
            "message": "同级消息",
            "revision": 4,
        })
        self.assertEqual(top_level_wins["move"], {"action": "roll"})
        self.assertEqual(top_level_wins["message"], "同级消息")

        top_level_only = server._prepare_duel_payload({
            "action": "move",
            "player_id": "ai-1",
            "room_id": "ABCDEFGH",
            "move": {"action": "roll"},
            "message": "原本就在同级",
            "revision": 4,
        })
        self.assertEqual(top_level_only["move"], {"action": "roll"})
        self.assertEqual(top_level_only["message"], "原本就在同级")

        unknown_stays_in_move = server._prepare_duel_payload({
            "action": "move",
            "player_id": "ai-1",
            "room_id": "ABCDEFGH",
            "move": {
                "action": "roll",
                "message": "只提升这个",
                "unexpected": "must-still-fail-game-validation",
            },
            "revision": 4,
        })
        self.assertEqual(unknown_stays_in_move["move"], {
            "action": "roll",
            "unexpected": "must-still-fail-game-validation",
        })
        self.assertIn("与 move 同级", server.DUEL_GUIDE)

    def test_duel_prepare_errors_are_targeted_and_self_repairing(self):
        cases = (
            (
                {"action": "roll", "room_id": "ABCDEFGH"},
                "outer_action",
                "params.move.action",
            ),
            (
                {
                    "action": "move",
                    "room_id": "ABCDEFGH",
                    "revision": 4,
                    "move": {
                        "action": "roll",
                        "room_id": "ABCDEFGH",
                        "revision": 4,
                        "wait": True,
                    },
                },
                "misplaced_common_fields",
                "与 move 同级",
            ),
            (
                {
                    "action": "move",
                    "room_id": "ABCDEFGH",
                    "move": {"action": "roll"},
                },
                "missing_revision",
                "最近成功响应",
            ),
            (
                {
                    "action": "move",
                    "room_id": "ABCDEFGH",
                    "move": {"action": "roll"},
                    "revision": 4,
                    "full_state": True,
                },
                "full_state_action",
                "只用于 state",
            ),
            (
                {"action": "new", "game_type": "connect4", "wait": True},
                "wait_usage",
                "仅 state/move",
            ),
        )
        for arguments, error_type, hint_fragment in cases:
            with self.subTest(error_type=error_type):
                with self.assertRaises(server._McpError) as raised:
                    server._prepare_duel_payload(arguments)
                details = raised.exception.details
                self.assertEqual(details["error_type"], error_type)
                self.assertIn(hint_fragment, details["retry_hint"])
                self.assertIn("retry_example", details)
                self.assertLess(len(server._duel_mcp_error_text(raised.exception)), 500)

        with self.assertRaises(server._McpError) as nested_fields:
            server._prepare_duel_payload({
                "action": "move",
                "room_id": "ABCDEFGH",
                "revision": 4,
                "move": {"action": "roll", "full_state": True},
            })
        self.assertEqual(
            nested_fields.exception.details["field_errors"],
            ["move.full_state: 应与 move 同级"],
        )

    def test_sync_duel_backend_errors_preserve_fields_and_classify_retries(self):
        class FakeResponse:
            def __init__(self, status_code, data):
                self.status_code = status_code
                self._data = data

            def json(self):
                return self._data

        def mapped_error(status_code, data, payload):
            with (
                patch.object(
                    server.httpx,
                    "post",
                    return_value=FakeResponse(status_code, data),
                ),
                self.assertRaises(server._McpError) as raised,
            ):
                server._request_duel_backend(payload)
            return raised.exception

        base = {
            "action": "move",
            "room_id": "ABCDEFGH",
            "move": {"action": "move"},
            "revision": 4,
        }
        missing = mapped_error(
            422,
            {
                "message": "请求参数不符合接口格式，请检查字段、类型与必填项。",
                "details": [
                    {"field": "move.plane_id", "message": "Field required"},
                    {"field": "move.plane_index", "message": "Field required"},
                ],
            },
            base,
        )
        self.assertEqual(missing.details["error_type"], "missing_move_fields")
        self.assertEqual(missing.details["field_errors"], [
            "move.plane_id: Field required",
            "move.plane_index: Field required",
        ])
        self.assertIn("完整动作", missing.details["retry_hint"])

        alternative = mapped_error(
            400,
            {"message": "无效落子：move 必须提供 plane_id 或 plane_index"},
            base,
        )
        self.assertEqual(
            alternative.details["field_errors"],
            ["move.plane_id/plane_index: 至少一个必填"],
        )

        coordinates = mapped_error(
            400,
            {
                "message": (
                    "无效落子：走法只接受 from_row、from_col、to_row、to_col 四个字段"
                )
            },
            {**base, "move": {"from_row": 5}},
        )
        self.assertEqual(coordinates.details["error_type"], "missing_move_fields")
        self.assertEqual(coordinates.details["field_errors"], [
            "move.from_col: 缺失",
            "move.to_col: 缺失",
            "move.to_row: 缺失",
        ])

        extra_payload = {**base, "move": {"action": "roll", "trace": "x"}}
        extra = mapped_error(
            400,
            {"message": "无效落子：roll 只接受 action 字段"},
            extra_payload,
        )
        self.assertEqual(extra.details["error_type"], "extra_move_fields")
        self.assertEqual(extra.details["field_errors"], ["move.trace: 不接受"])
        self.assertIn("只接受 action", extra.message)

        authoritative = mapped_error(
            400,
            {"message": "无效落子：action_id 不在当前权威 legal_actions 中"},
            {**base, "move": {"action": "act", "action_id": "made-up"}},
        )
        self.assertEqual(
            authoritative.details["error_type"], "not_authoritative"
        )
        self.assertIn("不要推理未发布动作", authoritative.details["retry_hint"])

        stale = mapped_error(
            409,
            {"message": "局面 revision 已变化（期望 4，当前 5）"},
            base,
        )
        self.assertEqual(stale.details["error_type"], "stale_revision")
        self.assertIn("别重放旧 move", stale.details["retry_hint"])

        other_turn = mapped_error(
            409,
            {"message": "还没轮到你落子"},
            base,
        )
        self.assertEqual(other_turn.details["error_type"], "not_your_turn")
        self.assertEqual(
            other_turn.details["retry_example"]["params"]["wait"], True
        )

        loan_conflict = mapped_error(
            409,
            {"message": "欠条 revision 已变化，当前为 3"},
            {"action": "chips", "op": "loans", "loan_action": "accept"},
        )
        self.assertEqual(loan_conflict.details, {})

    def test_sync_mcp_adds_duel_retry_metadata_only_on_errors(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "tools/call",
            "params": {
                "name": "play",
                "arguments": {
                    "game": "duel",
                    "action": "roll",
                    "params": {"room_id": "ABCDEFGH"},
                },
            },
        }
        error = server._duel_mcp_error(
            'duel 外层 action="roll" 无效',
            error_type="outer_action",
            retry_hint='外层 action 改为 "move"；游戏动作放 params.move.action。',
            retry_example=server._duel_move_retry_example("roll"),
        )
        with patch.object(server, "_tool_play", side_effect=error):
            failed = server._handle_root_mcp(payload)
        failed_text = failed["result"]["content"][0]["text"]
        self.assertTrue(failed["result"]["isError"])
        self.assertIn('"error_type":"outer_action"', failed_text)
        self.assertIn('"retry_example"', failed_text)

        with patch.object(server, "_tool_play", return_value='{"ok":true}'):
            succeeded = server._handle_root_mcp(payload)
        success_text = succeeded["result"]["content"][0]["text"]
        self.assertFalse(succeeded["result"]["isError"])
        self.assertNotIn("retry_hint", success_text)
        self.assertNotIn("retry_example", success_text)

        non_duel = {
            **payload,
            "id": 52,
            "params": {
                "name": "play",
                "arguments": {"game": "workkk", "action": "state"},
            },
        }
        with (
            patch.object(server, "_authenticated_ai_player_id", return_value=None),
            patch.object(server, "_duel_unread_request_reminder", return_value=""),
            patch.object(server, "_tool_play", side_effect=error),
        ):
            unchanged = server._handle_root_mcp(non_duel)
        unchanged_text = unchanged["result"]["content"][0]["text"]
        self.assertEqual(
            unchanged_text,
            '【cedartoy】duel 外层 action="roll" 无效',
        )
        self.assertNotIn("retry_hint", unchanged_text)

    def test_pending_and_expired_wait_responses_expose_request_bound_next_call(self):
        pending = server._annotate_duel_wait_followup(
            {
                "ok": True,
                "status": "pending",
                "room_id": "ABCDEFGH",
            },
            action="new",
        )
        self.assertEqual(pending["wait_scope"], "current_request_only")
        self.assertEqual(pending["next_call"], {
            "game": "duel",
            "action": "state",
            "params": {"room_id": "ABCDEFGH", "wait": True},
        })
        self.assertIn("不能主动唤醒", pending["wait_hint"])

        expired = server._annotate_duel_wait_followup(
            {
                "ok": True,
                "status": "still_waiting",
                "room_id": "ABCDEFGH",
                "revision": 9,
            },
            action="state",
        )
        self.assertEqual(expired["next_call"], pending["next_call"])
        self.assertIn("挂等已到上限", expired["wait_hint"])

        finished = {"ok": True, "status": "finished", "room_id": "ABCDEFGH"}
        self.assertIs(
            server._annotate_duel_wait_followup(finished, action="state"),
            finished,
        )

    def test_unread_duel_reminder_is_pull_time_and_does_not_acknowledge(self):
        with TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/duel.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE notifications (
                        subject_type TEXT NOT NULL,
                        subject_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        read_at TEXT
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO notifications VALUES ('ai', '42', ?, ?)",
                    (("game", None), ("game", None), ("loan", None)),
                )

            with patch.object(server, "DUEL_DB_PATH", db_path):
                reminder = server._duel_unread_request_reminder("42")
                self.assertIn("2 条未读对局变化", reminder)
                self.assertIn("非主动推送", reminder)
                with sqlite3.connect(db_path) as conn:
                    unread = conn.execute(
                        "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
                    ).fetchone()[0]
                self.assertEqual(unread, 3)

    def test_next_authenticated_non_duel_tool_call_appends_duel_reminder(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 91,
            "method": "tools/call",
            "params": {"name": "list_games", "arguments": {}},
        }
        with (
            patch.object(server, "_tool_list_games", return_value="games"),
            patch.object(server, "_authenticated_ai_player_id", return_value="42"),
            patch.object(
                server,
                "_duel_unread_request_reminder",
                return_value="pull-time duel reminder",
            ),
        ):
            result = server._handle_root_mcp(payload, path_token="trusted")
            error_result = server._handle_root_mcp(
                {
                    **payload,
                    "id": 92,
                    "params": {"name": "unknown", "arguments": {}},
                },
                path_token="trusted",
            )

        self.assertEqual(
            [item["text"] for item in result["result"]["content"]],
            ["games", "pull-time duel reminder"],
        )
        self.assertTrue(error_result["result"]["isError"])
        self.assertEqual(
            error_result["result"]["content"][-1]["text"],
            "pull-time duel reminder",
        )

    def test_human_context_uses_account_avatars_and_shared_fallbacks(self):
        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def execute(*_args, **_kwargs):
                return FakeConnection()

            @staticmethod
            def fetchall():
                return [
                    {
                        "id": 42,
                        "username": "小紫",
                        "is_ai": 1,
                        "avatar_type": "emoji",
                        "avatar_value": "🌌",
                    },
                    {
                        "id": 43,
                        "username": "小蓝",
                        "is_ai": 1,
                        "avatar_type": None,
                        "avatar_value": None,
                    },
                ]

        handler = object.__new__(server.CedarToyHandler)
        with (
            patch.object(
                server,
                "_current_account",
                return_value={
                    "id": 7,
                    "username": "南山",
                    "is_ai": 0,
                    "avatar_type": None,
                    "avatar_value": None,
                },
            ),
            patch.object(server, "_db_connect", return_value=FakeConnection()),
        ):
            context = handler._duel_human_context("trusted-token")

        self.assertEqual(
            context["human_avatar"],
            {"type": "emoji", "value": "🙂", "is_default": True},
        )
        self.assertEqual(
            [machine["avatar"] for machine in context["machines"]],
            [
                {"type": "emoji", "value": "🌌", "is_default": False},
                {"type": "emoji", "value": "🤖", "is_default": True},
            ],
        )

    def test_all_room_actions_use_minimum_backend_field_matrix(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True}

        cases = (
            (
                "catalog",
                {"room_id": "must-drop"},
                {},
                "catalog 查游戏能力",
            ),
            (
                "rooms",
                {"include_terminal": True, "limit": 7, "offset": 2,
                 "room_id": "must-drop"},
                {"include_terminal": True, "limit": 7, "offset": 2},
                "rooms 查房",
            ),
            (
                "new",
                {
                    "game_type": "gomoku",
                    "mode": "ai_first",
                    "stake": 12,
                    "target_player_count": 2,
                    "fill_with_npcs": False,
                    "room_id": "must-drop",
                },
                {
                    "opponent_id": "trusted-human",
                    "game_type": "gomoku",
                    "mode": "ai_first",
                    "stake": 12,
                    "target_player_count": 2,
                    "fill_with_npcs": False,
                },
                "new 开房",
            ),
            (
                "rematch",
                {"room_id": "OLDROOM1", "game_type": "must-drop",
                 "stake": 99},
                {"room_id": "OLDROOM1"},
                "rematch 再来一局",
            ),
            (
                "join",
                {"room_id": "ABCDEFGH", "message": "我来了",
                 "revision": 3},
                {"opponent_id": "trusted-human", "room_id": "ABCDEFGH",
                 "message": "我来了"},
                "join 加 waiting 房",
            ),
            (
                "accept",
                {"room_id": "ABCDEFGH", "message": "must-drop"},
                {"room_id": "ABCDEFGH"},
                "accept/reject 处理邀请",
            ),
            (
                "reject",
                {"room_id": "ABCDEFGH", "revision": 3},
                {"room_id": "ABCDEFGH"},
                "accept/reject 处理邀请",
            ),
            (
                "move",
                {
                    "room_id": "ABCDEFGH",
                    "move": {"action": "bid", "quantity": 3, "face": 4},
                    "revision": 8,
                    "wait": True,
                    "message": "三个四",
                    "offset": 9,
                },
                {
                    "room_id": "ABCDEFGH",
                    "move": {"action": "bid", "quantity": 3, "face": 4},
                    "revision": 8,
                    "wait": True,
                    "message": "三个四",
                },
                "move 行动",
            ),
            (
                "state",
                {
                    "room_id": "ABCDEFGH",
                    "wait": True,
                    "message": "还在吗",
                    "move": {"row": 0, "col": 0},
                },
                {
                    "room_id": "ABCDEFGH",
                    "wait": True,
                    "message": "还在吗",
                },
                "state 同步",
            ),
            (
                "resign",
                {"room_id": "ABCDEFGH", "message": "认输"},
                {"room_id": "ABCDEFGH", "message": "认输"},
                "resign 认输",
            ),
            (
                "leave",
                {"room_id": "ABCDEFGH", "message": "先走了",
                 "revision": 5},
                {"room_id": "ABCDEFGH", "message": "先走了"},
                "leave 离席",
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
            for action, params, expected_fields, guide_marker in cases:
                with self.subTest(action=action):
                    post.reset_mock()
                    result = json.loads(server._tool_play_inner(
                        {
                            "game": "duel",
                            "action": action,
                            "player_id": "reported-top-level-machine",
                            "opponent_id": "reported-top-level-human",
                            "params": {
                                "player_id": "reported-params-machine",
                                "opponent_id": "reported-params-human",
                                "participant_ids": [
                                    "victim", "reported-params-machine",
                                ],
                                "viewer": "victim",
                                "unknown_duel_field": "must-drop",
                                **params,
                            },
                        },
                        path_token="trusted-token",
                    ))
                    self.assertTrue(result["ok"])
                    self.assertEqual(
                        post.call_args.kwargs["json"],
                        {
                            "action": action,
                            "player_id": "42",
                            **expected_fields,
                        },
                    )
                    self.assertIn(guide_marker, server.DUEL_GUIDE)

    def test_sync_forwarder_preserves_backend_private_terminal_and_unread_fields(self):
        backend_response = {
            "ok": True,
            "status": "finished",
            "room_id": "ABCDEFGH",
            "revision": 12,
            "private_state": {"dice": [2, 5]},
            "events": [{"sequence": 3, "type": "move"}],
            "winner_player_id": "42:3",
            "game_result": {"reason": "last_active"},
            "settlement": {"deltas": {"42:3": 2}},
            "notices": [{"event": "loan_countered"}],
            "unread": {"total": 1, "categories": {"loan": 1}},
            "unread_hint": "借款（未读1）→chips/loans",
        }

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return backend_response

        with patch.object(server.httpx, "post", return_value=FakeResponse()):
            result = server._play_duel(
                {
                    "action": "state",
                    "player_id": "reported-machine",
                    "room_id": "ABCDEFGH",
                },
                trusted_player_id="42:3",
            )

        self.assertIs(result, backend_response)
        self.assertEqual(result["private_state"], {"dice": [2, 5]})
        self.assertEqual(result["unread_hint"], "借款（未读1）→chips/loans")

    def test_reported_opponent_is_dropped_without_a_trusted_binding(self):
        payload = server._prepare_duel_payload(
            {
                "action": "new",
                "player_id": "guest:machine",
                "opponent_id": "victim-human",
                "participant_ids": ["victim-human", "guest:machine"],
                "viewer": "victim-human",
                "game_type": "tictactoe",
            },
            trusted_player_id="guest:machine",
        )

        self.assertEqual(payload, {
            "action": "new",
            "player_id": "guest:machine",
            "game_type": "tictactoe",
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
            ("default_status", {}),
            ("status", {"op": "status"}),
            ("check_in", {"op": "check_in"}),
            ("bankruptcy", {"op": "bankruptcy"}),
            ("ledger", {"op": "ledger", "limit": 7}),
            ("achievements", {"op": "achievements"}),
            ("loans_default_list", {"op": "loans", "limit": 11}),
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
            ("exchange_default_list", {"op": "exchange", "limit": 16}),
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
        guide_markers = {
            "default_status": "op=status|check_in|bankruptcy|ledger|achievements|loans|exchange",
            "status": "op=status|check_in|bankruptcy|ledger|achievements|loans|exchange",
            "check_in": "check_in",
            "bankruptcy": "bankruptcy",
            "ledger": "ledger",
            "achievements": "achievements",
            "loans_default_list": "loan_action=list|create|accept|reject|counter|withdraw|repay",
            "loans_list": "loan_action=list|create|accept|reject|counter|withdraw|repay",
            "loans_create": "create(principal,daily_rate_micro_percent,due_date,interest_cap_enabled?,idempotency_key)",
            "loans_accept": "accept/reject/withdraw(loan_id,loan_revision,idempotency_key)",
            "loans_reject": "accept/reject/withdraw(loan_id,loan_revision,idempotency_key)",
            "loans_counter": "counter(loan_id,loan_revision,principal,daily_rate_micro_percent,due_date,interest_cap_enabled,idempotency_key)",
            "loans_withdraw": "accept/reject/withdraw(loan_id,loan_revision,idempotency_key)",
            "loans_repay": "repay(loan_id,amount,idempotency_key)",
            "exchange_default_list": "exchange_action=catalog|list|create|confirm|reject|withdraw",
            "exchange_catalog": "exchange_action=catalog|list|create|confirm|reject|withdraw",
            "exchange_list": "exchange_action=catalog|list|create|confirm|reject|withdraw",
            "exchange_create": "create(item_key,request_note,chip_amount,custom_title?,idempotency_key)",
            "exchange_confirm": "confirm/reject/withdraw(request_id,idempotency_key)",
            "exchange_reject": "confirm/reject/withdraw(request_id,idempotency_key)",
            "exchange_withdraw": "confirm/reject/withdraw(request_id,idempotency_key)",
        }
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
                    known_field_noise = {
                        "limit": 99,
                        "loan_action": "repay",
                        "loan_id": "ln_must_drop",
                        "loan_revision": 99,
                        "principal": 999,
                        "daily_rate_micro_percent": 999,
                        "due_date": "2026-09-30",
                        "interest_cap_enabled": False,
                        "amount": 999,
                        "exchange_action": "confirm",
                        "request_id": "ex_must_drop",
                        "item_key": "must_drop",
                        "request_note": "must drop",
                        "custom_title": "must drop",
                        "chip_amount": 99,
                        "idempotency_key": "noise:key:0001",
                    }
                    if label == "loans_default_list":
                        known_field_noise.pop("loan_action")
                    if label == "exchange_default_list":
                        known_field_noise.pop("exchange_action")
                    result = json.loads(server._tool_play_inner(
                        {
                            "game": "duel",
                            "action": "chips",
                            "player_id": "reported-top-level-ai",
                            "opponent_id": "reported-top-level-human",
                            "params": {
                                **known_field_noise,
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
                    self.assertIn(guide_markers[label], server.DUEL_GUIDE)

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

    def test_guide_is_compact_and_discovers_every_room_capability(self):
        guide = server.DUEL_GUIDE
        self.assertLessEqual(len(guide), 4000)
        self.assertEqual(guide.count('play(game="duel"'), 1)
        self.assertNotIn("[存档槽]", guide)
        for expected in (
            "rooms 查房",
            "new 开房",
            "accept/reject 处理邀请",
            "join 加 waiting 房",
            "rematch 再来一局",
            "move 行动",
            "state 同步",
            "resign 认输",
            "leave 离席",
            "2人=tictactoe/gomoku/othello/connect4/jungle/xiangqi/checkers/banqi/chess/junqi/go",
            "3人=doudizhu",
            "4人=guandan/mahjong",
            "dots_boxes=2/3/4",
            "aeroplane_chess/gandengyan=2/3/4",
            "chinese_checkers=2/3/4/6",
            "liars_dice/yahtzee/uno/blackjack/train_cards/zhajinhua/texas_holdem=2..6",
            "NPC：除 tictactoe/gomoku/othello/connect4/jungle/xiangqi 外均可",
            "target_player_count/fill_with_npcs",
            "yahtzee 固定娱乐局；其余按 catalog 支持 stake",
            "liars_dice 私骰；uno/gandengyan/blackjack/doudizhu/guandan/zhajinhua/texas_holdem/mahjong 私手",
            "开房能力以 catalog",
            "supports_npcs/supports_stakes",
            "bootstrap 后也按上述方式继续挂等",
            "挂等不是后台订阅或推送",
            "不能主动唤醒 ChatGPT/MCP 客户端",
            "next_call",
            "full_state=true",
            "之后 move/state 默认只返回",
            "不会消费增量事件",
            "按 rules_text/move_format 行动",
            '外层 duel action 固定为 "move"',
            "游戏动作对象放 params.move",
            "内部 action 提到外层",
            "room_id/revision/wait/full_state/message 均在 params 内与 move 同级",
            "message 仅 join/move/state/resign/leave 支持",
            "列表型 legal_actions/legal_moves 的选中对象直接作为 move",
            "紧凑/参数化规格按 legal_action_spec 或 submit 构造",
            "allowed_player_counts",
            "private_state 只含己方私密信息",
            "revision 优先用最近成功响应值",
            "仅缺失、409 或疑似过期时 state",
            "不要每步先 state",
            "winner/result/settlement",
            "player_id/opponent_id/viewer/participant_ids",
        ):
            self.assertIn(expected, guide)

        self.assertNotIn("写操作带最新 revision", guide)

        delivered = json.loads(server._tool_get_guide({"game": "duel"}))["guide"]
        self.assertEqual(delivered, guide)
        self.assertNotIn("[存档槽]", delivered)

    def test_guide_discovers_chip_center_roles_fields_and_unread_entries(self):
        guide = server.DUEL_GUIDE
        for expected in (
            'action="chips"',
            "op=status|check_in|bankruptcy|ledger|achievements|loans|exchange",
            "loan_action=list|create|accept|reject|counter|withdraw|repay",
            "create(principal,daily_rate_micro_percent,due_date,interest_cap_enabled?,idempotency_key)",
            "accept/reject/withdraw(loan_id,loan_revision,idempotency_key)",
            "counter(loan_id,loan_revision,principal,daily_rate_micro_percent,due_date,interest_cap_enabled,idempotency_key)",
            "repay(loan_id,amount,idempotency_key)",
            "小机向绑定人类借款",
            "counter=改条件",
            "list.allowed_actions",
            "exchange_action=catalog|list|create|confirm|reject|withdraw",
            "create(item_key,request_note,chip_amount,custom_title?,idempotency_key)",
            "confirm/reject/withdraw(request_id,idempotency_key)",
            "发起方履约收筹码，审批方 confirm 后付筹码",
            "idempotency_key 必须 8..128 位",
            "unread/unread_hint",
            "对局→rooms",
            "借款→chips/loans",
            "兑换→chips/exchange",
            "成就→chips/achievements",
        ):
            self.assertIn(expected, guide)

    def test_proxy_allows_only_current_duel_web_routes(self):
        allowed = {
            "GET": (
                "/", "/chips", "/api/whoami", "/api/chips",
                "/api/notifications/unread",
                "/static/styles.css", "/static/app.js",
                "/static/game_ui_registry.js",
                "/static/games/checkers.js", "/static/games/checkers.css",
                "/static/games/banqi.js", "/static/games/chess.js",
                "/static/games/yahtzee.js",
                "/static/chips.css", "/static/chips.js",
                "/static/assets/exchange-shop/items/good_life.png",
                "/api/chips/machines/42%3A3", "/api/rooms/ABCDEFGH",
                "/api/chips/exchanges", "/api/chips/exchanges/catalog",
                "/api/npc-avatars/example.webp",
            ),
            "POST": (
                "/api/rooms", "/api/chips/check-in", "/api/chips/bankruptcy",
                "/api/notifications/read",
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
            ("GET", "/api/notifications/read"),
            ("GET", "/static/games/Checkers.js"),
            ("GET", "/static/games/checkers.svg"),
            ("GET", "/static/games/../app.js"),
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
            ("POST", "/api/notifications/unread"),
            ("POST", "/api/notifications/read/all"),
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

    def test_strict_body_actions_use_trusted_header_without_forbidden_body_identity(self):
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
            "human_avatar": {
                "type": "emoji", "value": "🐼", "is_default": False,
            },
            "machines": [
                {
                    "id": "42:3",
                    "name": "小紫",
                    "avatar": {
                        "type": "emoji", "value": "🌌", "is_default": False,
                    },
                },
            ],
        }

        paths = (
            "/api/chips/check-in", "/api/chips/bankruptcy",
            "/api/notifications/read",
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
                payload = {"player_id": "reported-human"}
                expected_payload = {}
                if path == "/api/notifications/read":
                    payload.update(category="achievement", reference_id="achievement-1")
                    expected_payload = {
                        "category": "achievement",
                        "reference_id": "achievement-1",
                    }
                raw_body = json.dumps(payload).encode("utf-8")
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

                self.assertEqual(json.loads(captured["body"]), expected_payload)
                self.assertEqual(
                    captured["headers"]["X-Duel-Human-Player"],
                    "trusted-human",
                )
                human_avatar = json.loads(base64.urlsafe_b64decode(
                    captured["headers"]["X-Duel-Human-Avatar"] + "=="
                ))
                machines = json.loads(base64.urlsafe_b64decode(
                    captured["headers"]["X-Duel-Bound-Ais"] + "=="
                ))
                self.assertEqual(human_avatar, target["human_avatar"])
                self.assertEqual(machines, target["machines"])

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
