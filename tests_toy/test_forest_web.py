import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import server
from vendor_cmd_adapter import base, forest
from vendor_cmd_adapter import forest_runtime


ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor" / "mo-yao-play-games"


class ForestSharedStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="forest-web-")
        self.save_root = Path(self.temp_dir.name)
        self.base_patch = patch.object(base, "SAVE_ROOT", self.save_root)
        self.forest_patch = patch.object(forest, "SAVE_ROOT", self.save_root)
        self.base_patch.start()
        self.forest_patch.start()

    def tearDown(self):
        self.forest_patch.stop()
        self.base_patch.stop()
        self.temp_dir.cleanup()

    def state(self, player_id):
        return json.loads(
            (self.save_root / "forest" / player_id / forest.SAVE_NAME).read_text(
                encoding="utf-8"
            )
        )

    def test_all_author_v3_drafts_form_one_valid_runtime_view(self):
        game = forest_runtime._read_game(VENDOR_DIR)
        self.assertEqual(game["_forest_data_version"], "v3-display/v2-runtime")
        self.assertNotIn("_forest_data_warning", game)
        self.assertEqual(set(game["lines"]), {str(number) for number in range(1, 12)})
        for line_id, line in game["lines"].items():
            nodes = {"opening": line["opening"], **line["scenes"]}
            for scene_id, scene in nodes.items():
                self.assertEqual(scene["text"], scene["human_text"], (line_id, scene_id))
                self.assertTrue(scene["ai_slot"]["free"], (line_id, scene_id))
                self.assertTrue(scene["ai_slot"]["prompt"].strip(), (line_id, scene_id))
                for option in scene.get("options", {}).values():
                    self.assertTrue(
                        option["target"] == "free_play" or option["target"] in nodes,
                        (line_id, scene_id, option),
                    )
        self.assertEqual(game["lines"]["1"]["opening"]["options"]["D"]["target"], "1d")
        self.assertEqual(
            game["lines"]["1"]["opening"]["options"]["D"]["source"],
            "v2_fallback",
        )
        self.assertEqual(
            {(item["line"], item["scene"], item["option"]) for item in game["_forest_mapping_issues"]},
            {("1", "opening", "D"), ("11", "11a_linger", "B")},
        )
        self.assertIn("11a_end_c", game["lines"]["11"]["scenes"])

    def test_human_and_mcp_actions_are_bidirectionally_visible(self):
        initial = forest.web_state("42", player_name="小满", ai_name="阿橘")
        self.assertFalse(initial["has_save"])
        human_started = forest.web_action(
            "42",
            "start",
            expected_revision=initial["revision"],
            player_name="小满",
            ai_name="阿橘",
            line=1,
        )
        self.assertEqual(human_started["current"]["scene_id"], "opening")
        self.assertIn("小满", human_started["current"]["human_text"])
        self.assertIn("阿橘", forest.play({"player_id": "42", "action": "status"})["text"])

        forest.play({"player_id": "42", "action": "choose", "option": "A"})
        after_ai_choice = forest.web_state("42", player_name="小满", ai_name="阿橘")
        self.assertEqual(after_ai_choice["current"]["scene_id"], "1a")

        forest.play(
            {
                "player_id": "42",
                "action": "observe",
                "content": "我注意到炉灰是新的。",
            }
        )
        after_observation = forest.web_state("42", player_name="小满", ai_name="阿橘")
        self.assertEqual(after_observation["current"]["observation"], "我注意到炉灰是新的。")
        self.assertEqual(after_observation["latest_observation"]["scene_id"], "1a")

        human_finished = forest.web_action(
            "42",
            "choose",
            expected_revision=after_observation["revision"],
            expected_scene="1a",
            player_name="小满",
            ai_name="阿橘",
            option="B",
        )
        self.assertTrue(human_finished["current"]["is_ending"])
        self.assertEqual(human_finished["latest_observation"]["text"], "我注意到炉灰是新的。")
        mcp_status = forest.play({"player_id": "42", "action": "status"})["text"]
        self.assertIn(human_finished["current"]["scene_id"], mcp_status)
        self.assertIn(human_finished["current"]["souvenir"], mcp_status)

    def test_slots_are_isolated_for_web_and_mcp(self):
        first = forest.web_state("77", player_name="人类", ai_name="小机")
        second = forest.web_state("77:2", player_name="人类", ai_name="小机")
        forest.web_action(
            "77", "start", expected_revision=first["revision"],
            player_name="人类", ai_name="小机", line=2,
        )
        forest.web_action(
            "77:2", "start", expected_revision=second["revision"],
            player_name="人类", ai_name="小机", line=8,
        )
        self.assertEqual(forest.web_state("77")["current"]["line_id"], "2")
        self.assertEqual(forest.play({"player_id": "77:2", "action": "status"})["text"].split("当前位置：", 1)[1][0], "8")

    def test_stale_parallel_web_choices_conflict_without_double_advancing(self):
        empty = forest.web_state("parallel", player_name="人类", ai_name="小机")
        started = forest.web_action(
            "parallel", "start", expected_revision=empty["revision"],
            player_name="人类", ai_name="小机", line=1,
        )

        def choose():
            try:
                return forest.web_action(
                    "parallel", "choose",
                    expected_revision=started["revision"],
                    expected_scene="opening",
                    player_name="人类", ai_name="小机", option="A",
                )
            except forest.ForestConflictError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _index: choose(), range(2)))
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(sum(isinstance(item, forest.ForestConflictError) for item in outcomes), 1)
        saved = self.state("parallel")
        self.assertEqual(saved["current_scene"], "1a")
        self.assertEqual(saved["total_choices"], 1)
        self.assertFalse(list((self.save_root / "forest" / "parallel").glob(".*.tmp-*")))

    def test_v2_7_save_without_v3_fields_is_normalized_non_destructively(self):
        old_state = {
            "version": 1,
            "current_line": "1",
            "current_scene": "1d",
            "souvenirs": ["旧纪念品"],
            "completed_endings": ["1:1d"],
            "daily": {"date": "2026-08-09", "count": 2},
            "total_lines_started": 3,
            "total_choices": 4,
            "updated_at": "2026-08-09T12:00:00+08:00",
        }
        path = self.save_root / "forest" / "legacy" / forest.SAVE_NAME
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(old_state, ensure_ascii=False), encoding="utf-8")

        web = forest.web_state("legacy", player_name="旧旅人", ai_name="旧同行者")
        self.assertEqual(web["current"]["scene_id"], "1d")
        self.assertEqual(web["souvenirs"], ["旧纪念品"])
        self.assertEqual(web["revision"], 0)
        forest.play({"player_id": "legacy", "action": "observe", "content": "旧路也还在。"})
        normalized = self.state("legacy")
        self.assertEqual(normalized["souvenirs"], ["旧纪念品"])
        self.assertEqual(normalized["completed_endings"], ["1:1d"])
        self.assertEqual(normalized["observations"]["1:1d"], "旧路也还在。")
        self.assertEqual(normalized["version"], 1)

    def test_corrupt_web_save_is_backed_up_and_returns_json_snapshot(self):
        forest.play({"player_id": "webbroken", "action": "new"})
        path = self.save_root / "forest" / "webbroken" / forest.SAVE_NAME
        path.write_text("{broken", encoding="utf-8")
        snapshot = forest.web_state("webbroken", player_name="人类", ai_name="小机")
        self.assertTrue(snapshot["has_save"])
        self.assertIn("存档告警", snapshot["data_warning"])
        self.assertEqual(len(list(path.parent.glob(f"{forest.SAVE_NAME}.corrupt-*"))), 1)

    def test_web_actions_cannot_reset_or_overwrite_an_existing_save(self):
        initial = forest.web_state("protected", player_name="人类", ai_name="小机")
        forest.web_action(
            "protected", "start", expected_revision=initial["revision"],
            player_name="人类", ai_name="小机", line=1,
        )
        before = self.state("protected")
        with self.assertRaisesRegex(forest.VendorCmdError, "只支持 start / choose"):
            forest.web_action(
                "protected", "reset", expected_revision=before["revision"],
                player_name="人类", ai_name="小机",
            )
        self.assertEqual(self.state("protected"), before)


class ForestWebAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="forest-auth-")
        root = Path(self.temp_dir.name)
        self.db_path = root / "accounts.db"
        self.save_root = root / "saves"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    is_ai INTEGER NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users VALUES (1, '人类甲', 0, NULL);
                INSERT INTO toy_users VALUES (2, '人类乙', 0, NULL);
                INSERT INTO toy_users VALUES (42, '小机甲', 1, NULL);
                INSERT INTO toy_users VALUES (43, '小机乙', 1, NULL);
                INSERT INTO toy_users VALUES (44, '小机丙', 1, NULL);
                INSERT INTO user_bindings VALUES (1, 42);
                INSERT INTO user_bindings VALUES (1, 44);
                INSERT INTO user_bindings VALUES (2, 43);
                """
            )

        def connect():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

        self.db_patch = patch.object(server, "_db_connect", side_effect=connect)
        self.base_patch = patch.object(base, "SAVE_ROOT", self.save_root)
        self.forest_patch = patch.object(forest, "SAVE_ROOT", self.save_root)
        self.db_patch.start(); self.base_patch.start(); self.forest_patch.start()

    def tearDown(self):
        self.forest_patch.stop(); self.base_patch.stop(); self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_only_bound_ai_and_valid_slots_resolve(self):
        human = {"id": 1, "is_ai": False}
        self.assertEqual(server._forest_bound_target_for_user(human, "42")["player"], "42")
        self.assertEqual(
            server._forest_bound_target_for_user(human, "42:3"),
            {"player": "42:3", "ai_user_id": 42, "machine_name": "小机甲", "slot": 3},
        )
        self.assertIsNone(server._forest_bound_target_for_user(human, "43:3"))
        self.assertIsNone(server._forest_bound_target_for_user(human, "42:6"))
        self.assertIsNone(server._forest_bound_target_for_user({"id": 42, "is_ai": True}, "42"))
        self.assertIsNone(server._forest_bound_target_for_user({"id": 2, "is_ai": False}, "42"))

    def test_picker_lists_multiple_bound_ais_and_all_isolated_slots(self):
        forest.play({"player_id": "42:2", "action": "new"})
        forest.play({"player_id": "43", "action": "new"})
        machines = server._forest_watchable_slots_for_user({"id": 1, "is_ai": False})
        self.assertEqual({item["ai_user_id"] for item in machines}, {42, 44})
        self.assertTrue(all(len(item["slots"]) == 5 for item in machines))
        machine_42 = next(item for item in machines if item["ai_user_id"] == 42)
        self.assertEqual([slot["slot"] for slot in machine_42["slots"] if slot["exists"]], [2])
        self.assertNotIn(43, {item["ai_user_id"] for item in machines})


class ForestWebRouteTests(unittest.TestCase):
    def test_page_and_home_entry_use_server_state_not_browser_storage(self):
        page = (ROOT / "forest.html").read_text(encoding="utf-8")
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("/forest/api/state", page)
        self.assertIn("/forest/api/action", page)
        self.assertNotIn("localStorage", page)
        self.assertNotIn("sessionStorage", page)
        self.assertIn('fetch("/api/forest/saves"', home)
        self.assertIn("/forest/?player=", home)
        self.assertIn('watchLabel: "进入双人森林 →"', home)

    def test_home_picker_only_renders_machines_and_slots_with_existing_saves(self):
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        picker = home.split("function renderForestSlotPicker", 1)[1].split(
            "async function enterWatch", 1
        )[0]

        self.assertIn(
            "slots: (Array.isArray(machine.slots) ? machine.slots : []).filter((slot) => slot.exists)",
            picker,
        )
        self.assertIn(".filter((machine) => machine.slots.length)", picker)
        self.assertIn("machine.slots.map((save) =>", picker)
        self.assertIn("${machine.slots.length} 个已有存档", picker)
        self.assertIn("你绑定的小机还没有森林存档。", picker)
        self.assertNotIn("从森林入口开始", picker)
        self.assertNotIn("还没进入森林", picker)

    def test_page_keeps_upstream_storybook_structure_and_mobile_layout(self):
        page = (ROOT / "forest.html").read_text(encoding="utf-8")
        author = (VENDOR_DIR / "forest_panel.html").read_text(encoding="utf-8")
        author_shell = author.split("<script>", 1)[0]
        expected_shell = author_shell.replace(
            'maxlength="12" onchange="onNameChange()">',
            'maxlength="12" readonly>',
        )
        self.assertEqual(page.split("<script>", 1)[0], expected_shell)
        for marker in (
            'id="storybook-bg"',
            'class="picker-grid"',
            'class="main-split"',
            'class="story-page"',
            'class="observation-note"',
            'class="tape"',
            'id="souvenirPopup"',
            'id="souvenirList"',
            "@keyframes pageGlow",
            "@media (max-width:768px)",
        ):
            self.assertIn(marker, page)
        self.assertIn("翻开这本童话书，两个人一起走", page)
        self.assertNotIn("← CedarToy 首页", page)
        self.assertNotIn("刷新共档", page)
        self.assertNotIn("utility-bar", page)

    def test_page_has_no_second_story_source_or_editable_identity(self):
        page = (ROOT / "forest.html").read_text(encoding="utf-8")
        self.assertNotIn("const EMBEDDED", page)
        self.assertNotIn("forest_line", page)
        self.assertIn('id="playerNameInput" placeholder="旅人" maxlength="12" readonly', page)
        self.assertIn('id="aiNameInput" placeholder="同行者" maxlength="12" readonly', page)
        self.assertNotIn("onNameChange", page)
        self.assertNotIn("<textarea", page)
        self.assertIn('next.human_name', page)
        self.assertIn('next.machine_name', page)
        self.assertIn("current.human_text", page)
        self.assertIn("current.ai_prompt", page)
        self.assertIn("current.observation", page)

    def test_page_reuses_author_interactions_with_server_backing(self):
        page = (ROOT / "forest.html").read_text(encoding="utf-8")
        for interaction in (
            "function renderLinePicker()",
            "async function startLine(lineId)",
            "function goBack()",
            "function renderFreePlay()",
            "function handleEndingContinue()",
            "function showSouvenirPopup(name)",
            "function showSouvenirList()",
            "data-option=",
        ):
            self.assertIn(interaction, page)

    def test_page_posts_only_server_action_keys_with_revision_and_scene(self):
        page = (ROOT / "forest.html").read_text(encoding="utf-8")
        self.assertIn("expected_revision: snapshot.revision", page)
        self.assertIn("expected_scene: snapshot.current.scene_id", page)
        self.assertIn("performAction({action: 'start', line: lineId})", page)
        self.assertIn("action: 'choose'", page)
        self.assertNotIn("action: 'observe'", page)
        self.assertIn("credentials: 'same-origin'", page)

    def test_basic_page_and_api_routes_dispatch(self):
        get_handler = object.__new__(server.CedarToyHandler)
        get_handler.path = "/forest/?player=42"
        get_handler.headers = {}
        get_handler._handle_forest_page = Mock()
        get_handler.do_GET()
        get_handler._handle_forest_page.assert_called_once()

        state_handler = object.__new__(server.CedarToyHandler)
        state_handler.path = "/forest/api/state?player=42"
        state_handler.headers = {}
        state_handler._handle_forest_api_state = Mock()
        state_handler.do_GET()
        state_handler._handle_forest_api_state.assert_called_once()

        post_handler = object.__new__(server.CedarToyHandler)
        post_handler.path = "/forest/api/action"
        post_handler.headers = {}
        post_handler._handle_forest_api_action = Mock()
        post_handler.do_POST()
        post_handler._handle_forest_api_action.assert_called_once_with()

    def test_token_navigation_redirects_to_a_clean_url_and_httponly_cookie(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {}
        handler._forest_human_target = Mock(return_value=({"id": 1}, {"player": "42:2"}))
        responses = []
        headers = []
        handler.send_response = lambda status: responses.append(status)
        handler.send_header = lambda key, value: headers.append((key, value))
        handler.end_headers = lambda: None

        handler._handle_forest_page({"player": ["42:2"], "token": ["secret-token"]})

        self.assertEqual(responses, [303])
        values = dict(headers)
        self.assertEqual(values["Location"], "/forest/?player=42%3A2")
        self.assertNotIn("secret-token", values["Location"])
        self.assertIn("forest_token=secret-token", values["Set-Cookie"])
        self.assertIn("HttpOnly", values["Set-Cookie"])
        self.assertNotIn("Secure", values["Set-Cookie"])
        self.assertIn("SameSite=Lax", values["Set-Cookie"])

    def test_initial_navigation_exchanges_query_token_for_scoped_http_only_cookie(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {}
        responses = []
        headers = []
        handler.send_response = lambda status, *_args: responses.append(status)
        handler.send_header = lambda key, value: headers.append((key, value))
        handler.end_headers = lambda: None
        handler._forest_human_target = Mock(return_value=({}, {}))
        handler._handle_forest_page({"player": ["42:3"], "token": ["secret-token"]})
        header_map = dict(headers)
        self.assertEqual(responses, [303])
        self.assertEqual(header_map["Location"], "/forest/?player=42%3A3")
        self.assertNotIn("secret-token", header_map["Location"])
        self.assertIn("Path=/forest", header_map["Set-Cookie"])
        self.assertIn("HttpOnly", header_map["Set-Cookie"])
        self.assertNotIn("Secure", header_map["Set-Cookie"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
