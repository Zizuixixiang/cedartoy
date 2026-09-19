from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import server
from vendor_cmd_adapter import ai_life, base
from vendor_cmd_adapter.base import VendorCmdError


ROOT = Path(__file__).resolve().parents[1]


def make_handler(headers=None):
    current = object.__new__(server.CedarToyHandler)
    current.headers = headers or {}
    current.wfile = io.BytesIO()
    current.response_statuses = []
    current.response_headers = []
    current.send_response = lambda status, *_args: current.response_statuses.append(status)
    current.send_header = lambda key, value: current.response_headers.append((key, value))
    current.end_headers = lambda: None
    return current


def choose_action(decision):
    kind = decision["kind"]
    legal = decision.get("legal_actions") or []
    if kind.startswith("childhood_pick"):
        return legal[0]
    if kind == "pre_roll_decision":
        return {}
    if kind == "post_roll_decision":
        return {"choice": "proceed_to_purchase"}
    if kind == "purchase_ready":
        if legal:
            return legal[0]
        targets = decision.get("purchase_targets") or []
        empty = next(
            (
                item for item in targets
                if item.get("ordinary_card_ids") == []
                and item.get("fate_card_id") is None
            ),
            None,
        )
        target = empty or targets[0]
        return {
            "ordinary_card_ids": list(target["ordinary_card_ids"]),
            "fate_card_id": target.get("fate_card_id"),
        }
    if kind in {
        "fate_immediate_decision",
        "debuff_protection_decision",
        "debuff_shorten_decision",
        "maintenance_decision",
        "mh04_decision",
        "market_protection_decision",
        "placement_decision",
    }:
        return legal[0]
    if kind == "final_flex_designation":
        return decision["action_format"]
    raise AssertionError(f"no test action for decision kind {kind!r}")


class AiLifeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ai-life-adapter-")
        self.save_root = Path(self.temp_dir.name) / "vendor_saves"
        self.patches = [
            patch.object(base, "SAVE_ROOT", self.save_root),
            patch.object(ai_life, "SAVE_ROOT", self.save_root),
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
        ]
        for current in self.patches:
            current.start()
        with ai_life._SNAPSHOT_CACHE_LOCK:
            ai_life._SNAPSHOT_CACHE.clear()

    def tearDown(self):
        for current in reversed(self.patches):
            current.stop()
        with ai_life._SNAPSHOT_CACHE_LOCK:
            ai_life._SNAPSHOT_CACHE.clear()
        self.temp_dir.cleanup()

    def play(self, player_id, action, **params):
        return ai_life.play({"player_id": player_id, "action": action, **params})

    def save_path(self, player_id):
        return self.save_root / "ai_life" / player_id / ai_life.SAVE_NAME

    def test_real_runtime_reaches_game_over_and_keeps_official_score(self):
        player = "guest:ailifefull"
        result = self.play(player, "start_game", seed=20260919)
        decision = result["decision"]
        seen_kinds = set()
        for _ in range(180):
            seen_kinds.add(decision["kind"])
            if decision["kind"] == "game_over":
                break
            action = choose_action(decision)
            result = self.play(
                player,
                "submit_action",
                decision_id=decision["decision_id"],
                game_action=action,
            )
            self.assertTrue(result["ok"], result)
            decision = result["decision"]
        else:
            self.fail("real GameSession did not reach game_over within 180 accepted actions")

        self.assertEqual(decision["kind"], "game_over")
        self.assertTrue(decision["scoring_ready"])
        self.assertIsInstance(decision["score"], dict)
        self.assertEqual(decision["completed_turn"], 23)
        self.assertIn("childhood_pick_1", seen_kinds)
        self.assertIn("post_roll_decision", seen_kinds)
        self.assertIn("purchase_ready", seen_kinds)
        cold = self.play(player, "current_decision")
        self.assertEqual(cold, decision)
        archive = json.loads(self.save_path(player).read_text(encoding="utf-8"))
        self.assertEqual(archive["summary"]["status"], "game_over")
        self.assertEqual(len(archive["actions"]), archive["summary"]["action_count"])

    def test_cold_reload_is_exact_and_players_and_slots_are_isolated(self):
        decisions = {}
        for player, seed in (("101", 11), ("101:2", 22), ("202", 33)):
            opened = self.play(player, "start_game", seed=seed)
            action = opened["decision"]["legal_actions"][0]
            moved = self.play(
                player,
                "submit_action",
                decision_id=opened["decision"]["decision_id"],
                game_action=action,
            )
            decisions[player] = moved["decision"]
            self.assertEqual(self.play(player, "current_decision"), moved["decision"])

        self.assertEqual(
            {path.parent.name for path in self.save_root.glob("ai_life/*/save.json")},
            {"101", "101:2", "202"},
        )
        self.assertEqual(len({item["decision_id"] for item in decisions.values()}), 1)
        archives = {
            player: json.loads(self.save_path(player).read_text(encoding="utf-8"))
            for player in decisions
        }
        self.assertEqual({archive["seed"] for archive in archives.values()}, {11, 22, 33})
        self.assertEqual({len(archive["actions"]) for archive in archives.values()}, {1})

    def test_illegal_stale_duplicate_and_concurrent_retry_do_not_double_apply(self):
        player = "guest:ailifeidempotent"
        opened = self.play(player, "start_game", seed=42)
        decision = opened["decision"]
        accepted = decision["legal_actions"][0]
        alternative = decision["legal_actions"][1]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self.play,
                    player,
                    "submit_action",
                    decision_id=decision["decision_id"],
                    game_action=accepted,
                )
                for _ in range(2)
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(sum(bool(result.get("duplicate")) for result in results), 1)

        stale = self.play(
            player,
            "submit_action",
            decision_id=decision["decision_id"],
            game_action=alternative,
        )
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["error"], "stale_or_unknown_decision_id")
        current = self.play(player, "current_decision")
        illegal = self.play(
            player,
            "submit_action",
            decision_id=current["decision_id"],
            game_action={"card_id": "NOT-A-CARD"},
        )
        self.assertFalse(illegal["ok"])
        self.assertIn(illegal["error"], {"illegal_action", "invalid_action"})
        with self.assertRaisesRegex(VendorCmdError, "严格 JSON"):
            self.play(
                player,
                "submit_action",
                decision_id=current["decision_id"],
                game_action={"value": float("nan")},
            )
        archive = json.loads(self.save_path(player).read_text(encoding="utf-8"))
        self.assertEqual(len(archive["actions"]), 1)

    def test_concurrent_unconfirmed_starts_cannot_silently_overwrite(self):
        player = "guest:ailifestartrace"
        outcomes = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.play, player, "start_game", seed=seed)
                for seed in (101, 202)
            ]
            for future in futures:
                try:
                    outcomes.append(("ok", future.result(timeout=30)))
                except VendorCmdError as exc:
                    outcomes.append(("error", str(exc)))
        self.assertEqual([kind for kind, _value in outcomes].count("ok"), 1)
        self.assertEqual([kind for kind, _value in outcomes].count("error"), 1)
        self.assertIn("confirm=true", next(value for kind, value in outcomes if kind == "error"))
        archive = json.loads(self.save_path(player).read_text(encoding="utf-8"))
        self.assertIn(archive["seed"], {101, 202})

    def test_export_import_is_strict_validated_json_and_never_pickle(self):
        source = "guest:ailifeexport"
        target = "guest:ailifeimport"
        opened = self.play(source, "start_game", seed=9)
        action = opened["decision"]["legal_actions"][0]
        moved = self.play(
            source,
            "submit_action",
            decision_id=opened["decision"]["decision_id"],
            game_action=action,
        )
        exported = json.loads(self.play(source, "export")["text"])
        imported = self.play(target, "import", save_data=exported)
        self.assertTrue(imported["imported"])
        self.assertEqual(imported["decision"], moved["decision"])
        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            self.play(target, "import", save_data=exported)

        before = self.save_path(target).read_bytes()
        bad = dict(exported)
        bad["pickle"] = "cos\nsystem\n(S'echo unsafe'\ntR."
        with self.assertRaisesRegex(VendorCmdError, "未知字段 pickle"):
            self.play(target, "import", save_data=bad, confirm=True)
        self.assertEqual(self.save_path(target).read_bytes(), before)

    def test_corrupt_save_is_archived_without_starting_a_fake_game(self):
        player = "guest:ailifecorrupt"
        self.play(player, "start_game", seed=12)
        save_path = self.save_path(player)
        broken = b'{"format":"cedartoy.ai_life.replay","broken":'
        save_path.write_bytes(broken)
        with self.assertRaisesRegex(VendorCmdError, "已备份.*本次未执行动作"):
            self.play(player, "current_decision")
        self.assertFalse(save_path.exists())
        archived = list(save_path.parent.glob("save.json.corrupt-*"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), broken)

    def test_move_during_snapshot_does_not_cache_stale_state(self):
        player = "guest:ailiferace"
        opened = self.play(player, "start_game", seed=42)
        real_run = ai_life.GAME.run
        snapshot_reads = []

        def run_and_move(player_id, command, *args, **kwargs):
            result = real_run(player_id, command, *args, **kwargs)
            if command == "__snapshot__":
                snapshot_reads.append(command)
                if len(snapshot_reads) == 1:
                    decision = opened["decision"]
                    self.play(
                        player, "submit_action",
                        decision_id=decision["decision_id"],
                        game_action=decision["legal_actions"][0],
                    )
            return result

        with patch.object(ai_life.GAME, "run", side_effect=run_and_move):
            before = ai_life.spectator_snapshot(player)
            after = ai_life.spectator_snapshot(player)
            cached = ai_life.spectator_snapshot(player)
        self.assertNotEqual(before, after)
        self.assertEqual(after, cached)
        self.assertEqual(len(snapshot_reads), 2)

    def test_snapshot_and_catalog_come_from_upstream_runtime_and_are_read_only(self):
        player = "guest:ailifesnapshot"
        self.play(player, "start_game", seed=5, player_name="杉杉", player_emoji="🌲")
        before = self.save_path(player).read_bytes()
        snapshot = ai_life.spectator_snapshot(player)
        catalog = ai_life.card_catalog()
        self.assertEqual(snapshot["player_identity"], {"name": "杉杉", "emoji": "🌲"})
        self.assertEqual(snapshot["status"], "in_progress")
        self.assertTrue(catalog["cards"])
        self.assertEqual(self.save_path(player).read_bytes(), before)

    def test_missing_reads_do_not_create_false_saves_and_stats_ignore_archives(self):
        player = "guest:ailifemissing"
        with self.assertRaisesRegex(VendorCmdError, "还没有"):
            self.play(player, "current_decision")
        with self.assertRaisesRegex(VendorCmdError, "还没有"):
            self.play(
                player,
                "submit_action",
                decision_id="missing:0",
                game_action={},
            )
        self.assertFalse((self.save_root / "ai_life" / player).exists())

        invalid_player = "guest:invalidstart"
        with self.assertRaisesRegex(VendorCmdError, "seed 必须是整数"):
            self.play(invalid_player, "start_game", seed="not-an-integer")
        self.assertFalse(server._directory_vendor_save_exists(
            "ai_life", self.save_root / "ai_life" / invalid_player
        ))

        archived = self.save_root / "ai_life" / "guest:archived"
        archived.mkdir(parents=True)
        (archived / "save.json.corrupt-test").write_text("{}", encoding="utf-8")
        self.play("guest:valid", "start_game", seed=1)
        self.assertEqual(
            server._vendor_save_stats("ai_life"),
            {"save_count": 1, "file_count": 1},
        )


class AiLifePlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ai-life-platform-")
        self.save_root = Path(self.temp_dir.name) / "vendor_saves"
        self.patches = [
            patch.object(base, "SAVE_ROOT", self.save_root),
            patch.object(ai_life, "SAVE_ROOT", self.save_root),
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
            patch.object(server, "_ensure_guest_claim_code", return_value=None),
            patch.object(server, "_play_announcements", return_value=""),
            patch.object(server, "_anti_addiction_context", return_value=None),
        ]
        for current in self.patches:
            current.start()

    def tearDown(self):
        for current in reversed(self.patches):
            current.stop()
        self.temp_dir.cleanup()

    def test_catalog_guide_and_both_tool_schemas_are_directly_usable(self):
        self.assertIn("ai_life·AI单人策略人生桌游", server._tool_list_games())
        guide = json.loads(server._tool_get_guide({"game": "ai_life"}))["guide"]
        self.assertIn('action="submit_action"', guide)
        self.assertIn('"game_action":{"card_id":"C01"}', guide)
        self.assertIn("PolyForm Noncommercial License 1.0.0", guide)
        self.assertIn("并非作者官方版本", guide)
        for user_agent in ("", "Kelivo/1"):
            play_tool = next(
                tool for tool in server._root_tools(user_agent=user_agent)
                if tool["name"] == "play"
            )
            properties = play_tool["inputSchema"]["properties"]["params"]["properties"]
            for key in (
                "decision_id", "game_action", "seed", "forced_goals",
                "player_name", "player_emoji", "slot", "confirm", "save_data",
            ):
                self.assertIn(key, properties)
            self.assertEqual(properties["game_action"]["type"], "object")

        account = {"id": 74, "username": "示例小机", "is_ai": True}
        with patch.object(server, "_auto_migrate_legacy_account_saves"):
            opened = json.loads(server._tool_play(
                {
                    "game": "ai_life",
                    "action": "start_game",
                    "params": {"seed": 42},
                },
                authenticated_account=account,
            ))
            self.assertIn(
                {"card_id": "C01"},
                opened["decision"]["legal_actions"],
            )
            submitted = json.loads(server._tool_play(
                {
                    "game": "ai_life",
                    "action": "submit_action",
                    "params": {
                        "decision_id": "childhood_pick_1:0",
                        "game_action": {"card_id": "C01"},
                    },
                },
                authenticated_account=account,
            ))
        self.assertTrue(submitted["ok"])

    def test_platform_identity_overrides_reported_player_and_session_id_is_not_a_locator(self):
        account = {"id": 72, "username": "小机", "is_ai": True}
        with patch.object(server, "_auto_migrate_legacy_account_saves"):
            opened = json.loads(server._tool_play(
                {
                    "game": "ai_life",
                    "action": "start_game",
                    "player_id": "999",
                    "params": {"player_id": "888", "slot": 2, "seed": 17},
                },
                authenticated_account=account,
            ))
            current = json.loads(server._tool_play(
                {
                    "game": "ai_life",
                    "action": "current_decision",
                    "params": {"slot": 2, "session_id": "someone-elses-session"},
                },
                authenticated_account=account,
            ))
        self.assertEqual(opened["slot"], 2)
        self.assertEqual(current["slot"], 2)
        self.assertEqual(current["decision_id"], opened["decision"]["decision_id"])
        self.assertTrue((self.save_root / "ai_life" / "72:2" / "save.json").is_file())
        self.assertFalse((self.save_root / "ai_life" / "888").exists())
        self.assertFalse((self.save_root / "ai_life" / "999").exists())

    def test_duplicate_platform_retry_has_no_second_gameplay_side_effect(self):
        account = {"id": 73, "username": "幂等小机", "is_ai": True}
        with (
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_record_success", return_value="") as anti,
            patch.object(server, "_play_announcements", return_value="") as announcements,
        ):
            opened = json.loads(server._tool_play(
                {
                    "game": "ai_life",
                    "action": "start_game",
                    "params": {"seed": 42},
                },
                authenticated_account=account,
            ))
            decision = opened["decision"]
            original_action = decision["legal_actions"][0]
            json.loads(server._tool_play(
                {
                    "game": "ai_life",
                    "action": "submit_action",
                    "params": {
                        "decision_id": decision["decision_id"],
                        "game_action": original_action,
                    },
                },
                authenticated_account=account,
            ))
            anti.reset_mock()
            announcements.reset_mock()
            retry = json.loads(server._tool_play(
                {
                    "game": "ai_life",
                    "action": "submit_action",
                    "params": {
                        "decision_id": decision["decision_id"],
                        "game_action": original_action,
                    },
                },
                authenticated_account=account,
            ))
        self.assertTrue(retry["duplicate"])
        anti.assert_not_called()
        announcements.assert_not_called()

    def test_account_save_registry_lists_only_the_existing_ai_life_slot(self):
        ai_life.play({"player_id": "75:4", "action": "start_game", "seed": 8})
        user = {"id": 75, "username": "存档小机", "is_ai": True}
        missing_sessions = Path(self.temp_dir.name) / "no-sessions.db"
        accounts_db = Path(self.temp_dir.name) / "empty-accounts.db"
        sqlite3.connect(accounts_db).close()

        @contextmanager
        def connect():
            conn = sqlite3.connect(accounts_db)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

        with (
            patch.object(server, "SESSIONS_DB_PATH", missing_sessions),
            patch.object(server, "_db_connect", side_effect=connect),
            patch.object(server, "_turtle_soup_stats", return_value={}),
            patch.object(server, "_workkk_save_summary", return_value=None),
            patch.object(server, "_garden_cat_save_summary", return_value=None),
            patch.object(server, "_camping_plaza_save_summary", return_value=None),
        ):
            registry = server._account_saves_for_user(user, migrate_legacy=False)
        self.assertEqual(
            registry["saves"]["ai_life"]["slots"],
            [
                {
                    "slot": 4,
                    "status": "in_progress",
                    "kind": "childhood_pick_1",
                    "draft_round": 1,
                    "action_count": 0,
                    "updated_at": ANY,
                }
            ],
        )

    def test_homepage_card_picker_and_license_notice_are_present(self):
        homepage = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id: "ai_life"', homepage)
        self.assertIn('iconFile: "ai_life.svg"', homepage)
        self.assertIn('url: "https://github.com/racy1501/ai-life-boardgame"', homepage)
        self.assertIn('watchLabel: "围观人生 →"', homepage)
        self.assertIn("openAiLifePicker", homepage)
        self.assertIn("还没有 AI 人生桌游存档", homepage)
        self.assertIn("不会自动开局或显示演示局", homepage)
        self.assertTrue((ROOT / "assets" / "icons" / "ai_life.svg").is_file())


class AiLifeWebTests(unittest.TestCase):
    def test_human_can_resolve_only_a_bound_machine_and_valid_slot(self):
        with tempfile.TemporaryDirectory(prefix="ai-life-web-auth-") as temp_dir:
            database = Path(temp_dir) / "accounts.db"
            with sqlite3.connect(database) as conn:
                conn.executescript(
                    """
                    CREATE TABLE toy_users (
                        id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL,
                        is_ai INTEGER NOT NULL,
                        deleted_at TEXT
                    );
                    CREATE TABLE user_bindings (
                        human_user_id INTEGER NOT NULL,
                        ai_user_id INTEGER NOT NULL
                    );
                    INSERT INTO toy_users VALUES (1, '人类', 0, NULL);
                    INSERT INTO toy_users VALUES (42, '绑定小机', 1, NULL);
                    INSERT INTO toy_users VALUES (43, '别人小机', 1, NULL);
                    INSERT INTO user_bindings VALUES (1, 42);
                    """
                )

            @contextmanager
            def connect():
                conn = sqlite3.connect(database)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

            handler = make_handler({"Authorization": "Bearer human-token"})
            with (
                patch.object(server, "_current_account", return_value={"id": 1, "is_ai": False}),
                patch.object(server, "_db_connect", side_effect=connect),
            ):
                _user, target = handler._ai_life_human_target("42:3")
                self.assertEqual(target["player"], "42:3")
                with self.assertRaisesRegex(server._McpError, "没有绑定"):
                    handler._ai_life_human_target("43:3")
                with self.assertRaisesRegex(server._McpError, "没有绑定"):
                    handler._ai_life_human_target("42:6")

    def test_route_dispatch_and_query_token_becomes_scoped_cookie(self):
        route = make_handler()
        route.path = "/ai-life/?player=42"
        route._handle_ai_life_get = Mock()
        route.do_GET()
        route._handle_ai_life_get.assert_called_once_with(
            "/ai-life/", {"player": ["42"]}
        )

        handler = make_handler()
        handler._ai_life_human_target = Mock(
            return_value=({"id": 1}, {"player": "42:2", "slot": 2})
        )
        handler._handle_ai_life_page(
            {"player": ["42:2"], "token": ["secret.token/value"]}
        )
        self.assertEqual(handler.response_statuses, [303])
        headers = dict(handler.response_headers)
        self.assertEqual(headers["Location"], "/ai-life/?player=42%3A2")
        self.assertNotIn("secret.token/value", headers["Location"])
        self.assertIn("ai_life_token=secret.token%2Fvalue", headers["Set-Cookie"])
        self.assertIn("Path=/ai-life", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])

    def test_original_frontend_assets_are_same_origin_and_demo_call_is_removed(self):
        app = make_handler()
        app._handle_ai_life_static("app.js")
        self.assertEqual(app.response_statuses, [200])
        source = app.wfile.getvalue().decode("utf-8")
        self.assertIn("const spectatorBase = '/ai-life/api';", source)
        self.assertIn("get('player')", source)
        self.assertNotIn("127.0.0.1:8765", source)
        self.assertNotIn("renderDemo();\nif (sessionId)", source)
        self.assertIn("/spectator/sessions/", source)
        self.assertIn("/cards/catalog", source)

        style = make_handler()
        style._handle_ai_life_static("style.css")
        self.assertEqual(style.response_statuses, [200])
        self.assertEqual(
            style.wfile.getvalue(),
            (server.AI_LIFE_FRONTEND_ROOT / "style.css").read_bytes(),
        )
        self.assertEqual(dict(style.response_headers)["Cache-Control"], "no-cache")

        asset = make_handler()
        asset._handle_ai_life_static("assets/dice/dice-h.png")
        self.assertEqual(asset.response_statuses, [200])
        self.assertEqual(dict(asset.response_headers)["Content-Type"], "image/png")

    def test_responsive_layer_is_separate_versioned_and_covers_mobile_layout(self):
        responsive_style = make_handler()
        responsive_style._handle_ai_life_get(
            "/ai-life/cedartoy-responsive.v1.css", {}
        )
        self.assertEqual(responsive_style.response_statuses, [200])
        style_headers = dict(responsive_style.response_headers)
        self.assertEqual(style_headers["Content-Type"], "text/css; charset=utf-8")
        self.assertEqual(
            style_headers["Cache-Control"],
            "public, max-age=31536000, immutable",
        )
        css = responsive_style.wfile.getvalue().decode("utf-8")
        self.assertIn("(hover: none) and (pointer: coarse) and (max-width: 600px)", css)
        self.assertNotIn("(max-width: 900px)", css)
        self.assertIn("body {\n    min-width: 0;", css)
        self.assertIn("transform: none !important", css)
        self.assertIn("#opportunity-cards", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("grid-auto-rows: auto", css)
        self.assertIn(".card-detail-body", css)
        self.assertIn(".rules-content", css)
        self.assertNotIn("body { overflow-x: hidden", css)

        responsive_script = make_handler()
        responsive_script._handle_ai_life_get(
            "/ai-life/cedartoy-responsive.v1.js", {}
        )
        self.assertEqual(responsive_script.response_statuses, [200])
        script_headers = dict(responsive_script.response_headers)
        self.assertEqual(
            script_headers["Content-Type"], "text/javascript; charset=utf-8"
        )
        js = responsive_script.wfile.getvalue().decode("utf-8")
        self.assertIn("window.visualViewport", js)
        self.assertIn("orientationchange", js)
        self.assertIn("removeProperty('width')", js)
        self.assertIn("if (wasCompact)", js)
        self.assertIn("syncBoardScale()", js)
        self.assertIn("dialogs.some", js)

    def test_page_requires_bound_human_existing_save_without_notice_banner(self):
        denied = make_handler()
        denied._ai_life_human_target = Mock(
            side_effect=server._McpError(-32003, "你没有绑定这只小机或槽位无效")
        )
        with patch.object(server.ai_life_adapter, "save_summary") as summary:
            denied._handle_ai_life_page({"player": ["999"]})
        self.assertEqual(denied.response_statuses, [403])
        summary.assert_not_called()

        missing = make_handler()
        missing._ai_life_human_target = Mock(
            return_value=({"id": 1}, {"player": "42", "slot": 1})
        )
        with patch.object(server.ai_life_adapter, "save_summary", return_value=None):
            missing._handle_ai_life_page({"player": ["42"]})
        self.assertEqual(missing.response_statuses, [404])
        missing_page = missing.wfile.getvalue().decode("utf-8")
        self.assertIn("还没有 AI 人生桌游存档", missing_page)
        self.assertIn("不会自动创建或展示演示局", missing_page)

        allowed = make_handler()
        allowed._ai_life_human_target = Mock(
            return_value=({"id": 1}, {"player": "42", "slot": 1})
        )
        with patch.object(server.ai_life_adapter, "save_summary", return_value={"kind": "childhood_pick_1"}):
            allowed._handle_ai_life_page({"player": ["42"]})
        self.assertEqual(allowed.response_statuses, [200])
        page = allowed.wfile.getvalue().decode("utf-8")
        self.assertNotIn('class="cedartoy-adaptation-notice"', page)
        self.assertNotIn("CedarToy/4399 非商业适配版", page)
        self.assertIn('name="viewport"', page)
        self.assertNotIn("user-scalable=no", page)
        self.assertLess(
            page.index('href="style.css"'),
            page.index('href="/ai-life/cedartoy-responsive.v1.css"'),
        )
        self.assertLess(
            page.index('src="app.js"'),
            page.index('src="/ai-life/cedartoy-responsive.v1.js"'),
        )
        headers = dict(allowed.response_headers)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])

    def test_snapshot_reauthenticates_every_request_and_never_lists_sessions(self):
        handler = make_handler()
        handler._ai_life_human_target = Mock(
            return_value=({"id": 1}, {"player": "42:3", "slot": 3})
        )
        with patch.object(
            server.ai_life_adapter,
            "spectator_snapshot",
            return_value={"status": "in_progress", "current_turn": 4},
        ) as snapshot:
            handler._handle_ai_life_get(
                "/ai-life/api/spectator/sessions/42%3A3", {}
            )
        self.assertEqual(handler.response_statuses, [200])
        handler._ai_life_human_target.assert_called_once_with("42:3")
        snapshot.assert_called_once_with("42:3")
        self.assertEqual(
            json.loads(handler.wfile.getvalue()),
            {"status": "in_progress", "current_turn": 4},
        )
        self.assertEqual(dict(handler.response_headers)["Cache-Control"], "private, no-store")

        no_list = make_handler()
        no_list._handle_ai_life_get("/ai-life/api/spectator/sessions", {})
        self.assertEqual(no_list.response_statuses, [404])

        license_page = make_handler()
        license_page._handle_ai_life_get("/ai-life/LICENSE", {})
        self.assertEqual(license_page.response_statuses, [200])
        self.assertIn(
            "Required Notice: Copyright (c) 2026 racy1501 / 阿屿.",
            license_page.wfile.getvalue().decode("utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
