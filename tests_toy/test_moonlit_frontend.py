from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
from vendor_cmd_adapter import base, moonlit
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


class MoonlitAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="moonlit-frontend-")
        self.save_root = Path(self.temp_dir.name) / "vendor_saves"
        self.patches = [
            patch.object(base, "SAVE_ROOT", self.save_root),
            patch.object(moonlit, "SAVE_ROOT", self.save_root),
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
        ]
        for current in self.patches:
            current.start()

    def tearDown(self):
        for current in reversed(self.patches):
            current.stop()
        self.temp_dir.cleanup()

    def view_path(self, player_id):
        return self.save_root / "moonlit" / player_id / moonlit.VIEW_RELATIVE_PATH

    def new_and_render(self, player_id):
        moonlit.play({"action": "new", "player_id": player_id})
        moonlit.play({"action": "table", "player_id": player_id})

    def test_table_rejects_all_caller_paths_and_exact_legacy_command_is_safe(self):
        moonlit.play({"action": "new", "player_id": "42"})
        escaped = Path(self.temp_dir.name) / "escaped.html"

        with self.assertRaisesRegex(VendorCmdError, "不接受输出路径"):
            moonlit.play(
                {
                    "action": "table",
                    "player_id": "42",
                    "output_path": str(escaped),
                }
            )
        with self.assertRaisesRegex(VendorCmdError, "不允许为牌桌指定输出路径"):
            moonlit.play(
                {
                    "action": "cmd",
                    "player_id": "42",
                    "command": f"牌桌 {escaped}",
                }
            )
        with self.assertRaisesRegex(VendorCmdError, "不允许为牌桌指定输出路径"):
            moonlit.play(
                {
                    "action": "cmd",
                    "player_id": "42",
                    "command": f"牌桌{escaped}",
                }
            )
        with self.assertRaisesRegex(VendorCmdError, "不接受输出路径"):
            moonlit.play(
                {
                    "action": "cmd",
                    "player_id": "42",
                    "command": "牌桌",
                    "path": str(escaped),
                }
            )

        result = moonlit.play(
            {"action": "cmd", "player_id": "42", "command": "牌桌"}
        )
        self.assertIn("牌桌快照已更新", result["text"])
        self.assertTrue(self.view_path("42").is_file())
        self.assertFalse(escaped.exists())

    def test_table_snapshots_are_isolated_by_machine_and_slot(self):
        for player_id in ("42", "42:2", "43"):
            moonlit.play({"action": "new", "player_id": player_id})
            self.assertTrue(moonlit.ensure_table(player_id)["refreshed"])

        snapshots = {player_id: moonlit.read_table(player_id) for player_id in ("42", "42:2", "43")}
        self.assertTrue(all(snapshot and snapshot["body"] for snapshot in snapshots.values()))
        self.assertTrue(all(b'<meta http-equiv="refresh" content="5">' in snapshot["body"] for snapshot in snapshots.values()))
        self.assertEqual(
            {path.parent.parent.name + "/" + path.parent.name for path in map(self.view_path, snapshots)},
            {"42/.view", "42:2/.view", "43/.view"},
        )

        slot_two_before = snapshots["42:2"]["body"]
        self.view_path("42").write_bytes(b"<html>only machine 42 slot 1</html>")
        self.assertEqual(moonlit.read_table("42")["body"], b"<html>only machine 42 slot 1</html>")
        self.assertEqual(moonlit.read_table("42:2")["body"], slot_two_before)

    def test_auto_render_uses_sandbox_and_preserves_real_main_and_backup(self):
        moonlit.play({"action": "new", "player_id": "42"})
        save_dir = self.save_root / "moonlit" / "42"
        main_save = save_dir / moonlit.SAVE_NAME
        backup_save = save_dir / f"{moonlit.SAVE_NAME}.bak"
        backup_save.write_bytes(main_save.read_bytes())
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (main_save, backup_save)
        }

        snapshot = moonlit.ensure_table("42")

        self.assertTrue(snapshot["refreshed"])
        self.assertIn(b'<meta http-equiv="refresh" content="5">', snapshot["body"])
        for path, expected in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), expected)

    def test_old_empty_command_log_auto_renders_without_touching_real_saves(self):
        moonlit.play({"action": "new", "player_id": "42"})
        save_dir = self.save_root / "moonlit" / "42"
        main_save = save_dir / moonlit.SAVE_NAME
        backup_save = save_dir / f"{moonlit.SAVE_NAME}.bak"
        old_save = json.loads(main_save.read_text(encoding="utf-8"))
        old_save["_cmd_log"] = []
        main_save.write_text(
            json.dumps(old_save, ensure_ascii=False),
            encoding="utf-8",
        )
        backup_save.write_bytes(main_save.read_bytes())
        before = {
            path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in (main_save, backup_save)
        }
        handler = make_handler()
        handler._moonlit_human_target = Mock(
            return_value=({"id": 1}, {"player": "42", "slot": 1})
        )

        handler._handle_moonlit_page({"player": ["42"]})

        self.assertEqual(handler.response_statuses, [200])
        self.assertIn(
            b'<meta http-equiv="refresh" content="5">',
            handler.wfile.getvalue(),
        )
        self.assertTrue(self.view_path("42").is_file())
        for path, expected in before.items():
            actual = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            self.assertEqual(actual, expected)

    def test_unchanged_save_reuses_snapshot_and_changed_save_refreshes_it(self):
        moonlit.play({"action": "new", "player_id": "42"})
        main_save = self.save_root / "moonlit" / "42" / moonlit.SAVE_NAME
        original_renderer = moonlit._render_snapshot_in_sandbox
        with patch.object(
            moonlit,
            "_render_snapshot_in_sandbox",
            wraps=original_renderer,
        ) as renderer:
            first = moonlit.ensure_table("42")
            second = moonlit.ensure_table("42")
            self.assertTrue(first["refreshed"])
            self.assertFalse(second["refreshed"])
            self.assertEqual(first["etag"], second["etag"])
            self.assertEqual(renderer.call_count, 1)

            original_body = main_save.read_bytes()
            original_mtime = main_save.stat().st_mtime_ns
            time.sleep(0.02)
            main_save.write_bytes(original_body)
            self.assertGreater(main_save.stat().st_mtime_ns, original_mtime)

            third = moonlit.ensure_table("42")
            self.assertTrue(third["refreshed"])
            self.assertEqual(renderer.call_count, 2)

    def test_get_with_save_and_no_snapshot_generates_and_returns_page(self):
        moonlit.play({"action": "new", "player_id": "42"})
        self.assertFalse(self.view_path("42").exists())
        handler = make_handler()
        handler._moonlit_human_target = Mock(
            return_value=({"id": 1}, {"player": "42", "slot": 1})
        )

        handler._handle_moonlit_page({"player": ["42"]})

        self.assertEqual(handler.response_statuses, [200])
        self.assertIn("月幕万象".encode("utf-8"), handler.wfile.getvalue())
        self.assertTrue(self.view_path("42").is_file())
        self.assertIn("ETag", dict(handler.response_headers))

    def test_read_table_does_not_parse_the_game_save(self):
        save_dir = self.save_root / "moonlit" / "42"
        save_dir.mkdir(parents=True)
        (save_dir / moonlit.SAVE_NAME).write_bytes(b"not-json-and-must-not-be-read")
        self.view_path("42").parent.mkdir()
        self.view_path("42").write_bytes(b"<html>snapshot</html>")

        self.assertEqual(moonlit.read_table("42")["body"], b"<html>snapshot</html>")
        self.assertEqual(moonlit.save_summary("42"), {"saved": True})

    def test_new_import_and_delete_invalidate_snapshot_and_export_omits_html(self):
        self.new_and_render("42")
        self.assertTrue(self.view_path("42").is_file())

        exported = json.loads(moonlit.play({"action": "export", "player_id": "42"})["text"])
        self.assertTrue(exported)
        self.assertTrue(set(exported).issubset(moonlit.SAVE_FILES))
        self.assertFalse(any(name.endswith(".html") for name in exported))

        moonlit.play({"action": "new", "player_id": "42", "confirm": True})
        self.assertFalse(self.view_path("42").exists())

        moonlit.play({"action": "table", "player_id": "42"})
        moonlit.play(
            {
                "action": "import",
                "player_id": "42",
                "save_data": exported,
                "confirm": True,
            }
        )
        self.assertFalse(self.view_path("42").exists())

        moonlit.play({"action": "table", "player_id": "42"})
        self.assertTrue(moonlit.delete_save("42"))
        self.assertFalse(self.view_path("42").exists())

    def test_platform_delete_routes_moonlit_through_locked_adapter_helper(self):
        user = {"id": 42, "username": "小机", "is_ai": True}
        with (
            patch.object(server, "_current_account", return_value=user),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(moonlit, "delete_save", return_value=True) as delete,
        ):
            result = server._delete_save(
                {"game": "moonlit", "slot": 3, "confirm": True},
                "valid-token",
            )
        delete.assert_called_once_with("42:3")
        self.assertEqual(result["deleted"], [{"target": "vendor_saves/moonlit/42:3", "rows": 1}])


class MoonlitAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="moonlit-auth-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
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
                INSERT INTO user_bindings VALUES (1, 42);
                INSERT INTO user_bindings VALUES (2, 43);
                """
            )

        def connect():
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            return connection

        self.db_patch = patch.object(server, "_db_connect", side_effect=connect)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_only_human_owner_bound_machine_and_slots_one_to_five_resolve(self):
        human = {"id": 1, "is_ai": False}
        self.assertEqual(
            server._moonlit_bound_target_for_user(human, "42:5"),
            {"player": "42:5", "ai_user_id": 42, "machine_name": "小机甲", "slot": 5},
        )
        self.assertEqual(server._moonlit_bound_target_for_user(human, "42")["player"], "42")
        self.assertIsNone(server._moonlit_bound_target_for_user(human, "42:0"))
        self.assertIsNone(server._moonlit_bound_target_for_user(human, "42:6"))
        self.assertIsNone(server._moonlit_bound_target_for_user(human, "43"))
        self.assertIsNone(server._moonlit_bound_target_for_user({"id": 42, "is_ai": True}, "42"))
        self.assertIsNone(server._moonlit_bound_target_for_user({"id": 2, "is_ai": False}, "42"))


class MoonlitRouteTests(unittest.TestCase):
    @staticmethod
    def handler(headers=None):
        return make_handler(headers)

    def test_get_route_dispatches_without_adding_a_moonlit_post_handler(self):
        handler = self.handler()
        handler.path = "/moonlit/?player=42"
        handler._handle_moonlit_page = Mock()
        handler.do_GET()
        handler._handle_moonlit_page.assert_called_once_with({"player": ["42"]})
        self.assertFalse(hasattr(server.CedarToyHandler, "_handle_moonlit_post"))

    def test_query_token_is_exchanged_for_scoped_httponly_cookie_and_clean_url(self):
        handler = self.handler()
        handler._moonlit_human_target = Mock(
            return_value=({"id": 1}, {"player": "42:2", "slot": 2})
        )
        handler._handle_moonlit_page(
            {"player": ["42:2"], "token": ["secret.token/value"]}
        )

        self.assertEqual(handler.response_statuses, [303])
        headers = dict(handler.response_headers)
        self.assertEqual(headers["Location"], "/moonlit/?player=42%3A2")
        self.assertNotIn("secret.token/value", headers["Location"])
        self.assertIn("moonlit_token=secret.token%2Fvalue", headers["Set-Cookie"])
        self.assertIn("Path=/moonlit", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_missing_save_is_friendly_and_keeps_private_security_headers(self):
        handler = self.handler()
        handler._moonlit_human_target = Mock(return_value=({"id": 1}, {"player": "42"}))
        with patch.object(server.moonlit_adapter, "ensure_table", return_value=None):
            handler._handle_moonlit_page({"player": ["42"]})

        self.assertEqual(handler.response_statuses, [404])
        message = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("还没有月幕存档", message)
        self.assertNotIn("table", message)
        self.assertNotIn("生成快照", message)
        headers = dict(handler.response_headers)
        self.assertEqual(headers["Cache-Control"], "private, no-cache, max-age=0")
        self.assertEqual(headers["Vary"], "Cookie")
        self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_render_error_is_logged_but_page_does_not_leak_traceback(self):
        handler = self.handler()
        handler._moonlit_human_target = Mock(
            return_value=({"id": 1}, {"player": "42"})
        )
        internal_error = (
            "Traceback (most recent call last): TypeError: unhashable type: 'dict' "
            "at /tmp/cedartoy-moonlit-view-secret/牌桌.py:464"
        )

        with self.assertLogs(server.logger, level="ERROR") as captured:
            with patch.object(
                server.moonlit_adapter,
                "ensure_table",
                side_effect=VendorCmdError(internal_error),
            ):
                handler._handle_moonlit_page({"player": ["42"]})

        self.assertEqual(handler.response_statuses, [500])
        page = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("牌桌生成失败，请稍后刷新重试。", page)
        self.assertNotIn("Traceback", page)
        self.assertNotIn("TypeError", page)
        self.assertNotIn("cedartoy-moonlit-view-secret", page)
        self.assertIn(internal_error, "\n".join(captured.output))

    def test_snapshot_returns_etag_and_matching_request_returns_304_after_reauth(self):
        etag = '"0123456789abcdef"'
        snapshot = {"body": b"<html>table</html>", "etag": etag}

        first = self.handler({"Cookie": "moonlit_token=valid"})
        first._moonlit_human_target = Mock(return_value=({"id": 1}, {"player": "42"}))
        with patch.object(server.moonlit_adapter, "ensure_table", return_value=snapshot):
            first._handle_moonlit_page({"player": ["42"]})
        first_headers = dict(first.response_headers)
        self.assertEqual(first.response_statuses, [200])
        self.assertEqual(first_headers["ETag"], etag)
        self.assertEqual(first_headers["Vary"], "Cookie")
        self.assertEqual(first.wfile.getvalue(), snapshot["body"])
        first._moonlit_human_target.assert_called_once_with("42", "")

        cached = self.handler(
            {"Cookie": "moonlit_token=valid", "If-None-Match": etag}
        )
        cached._moonlit_human_target = Mock(return_value=({"id": 1}, {"player": "42"}))
        with patch.object(server.moonlit_adapter, "ensure_table", return_value=snapshot):
            cached._handle_moonlit_page({"player": ["42"]})
        cached_headers = dict(cached.response_headers)
        self.assertEqual(cached.response_statuses, [304])
        self.assertEqual(cached_headers["ETag"], etag)
        self.assertEqual(cached_headers["Cache-Control"], "private, no-cache, max-age=0")
        self.assertEqual(cached.wfile.getvalue(), b"")
        cached._moonlit_human_target.assert_called_once_with("42", "")


class MoonlitHomepageAndDocsTests(unittest.TestCase):
    def setUp(self):
        self.home = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_home_picker_reads_moonlit_slots_and_never_reuses_workkk_shape(self):
        start = self.home.index("    function renderMoonlitSlotPicker(machine)")
        end = self.home.index("    function renderCampingPlazaSlotPicker", start)
        picker = self.home[start:end]
        self.assertIn("savedMachine?.saves?.moonlit?.slots", picker)
        self.assertNotIn("saves?.workkk", picker)
        self.assertNotIn("table_ready", picker)
        self.assertNotIn("使用 table", picker)
        self.assertIn("/moonlit/?player=", self.home)

    def test_desktop_and_mobile_watch_buttons_share_the_moonlit_entry(self):
        moonlit_card = self.home[
            self.home.index('        id: "moonlit"') : self.home.index('        id: "mbti"')
        ]
        self.assertIn("watch: true", moonlit_card)
        self.assertIn('watchLabel: "围观牌桌 →"', moonlit_card)
        for button_id in ("watchButton", "guideWatchButton", "drawerWatchButton"):
            self.assertIn(f'id="{button_id}"', self.home)
        self.assertIn(
            '["watchButton", "guideWatchButton", "drawerWatchButton"].forEach((id) => {',
            self.home,
        )
        self.assertIn('if (game.id === "moonlit")', self.home)
        self.assertIn("await openMoonlitPicker(bindings);", self.home)
        self.assertIn('url: "https://github.com/xinwithyu/moonlit-myriad"', moonlit_card)

    def test_guide_explains_automatic_snapshot_refresh_without_server_path(self):
        guide = (ROOT / "vendor_cmd_adapter" / "guides.py").read_text(encoding="utf-8")
        moonlit_guide = guide[guide.index('    "moonlit":'):guide.index('    "imitator_td":')]
        self.assertIn('table — 可选兼容动作', moonlit_guide)
        self.assertIn("人类前端不要求小机先执行它", moonlit_guide)
        self.assertIn("快照不存在或存档更新后", moonlit_guide)
        self.assertIn("每 5 秒刷新会检查存档是否变化", moonlit_guide)
        self.assertIn("不会替小机出牌", moonlit_guide)
        self.assertNotIn(".view", moonlit_guide)
        self.assertNotIn("月幕万象.html", moonlit_guide)

    def test_backup_excludes_regenerable_moonlit_view_html(self):
        backup = (ROOT / "scripts" / "backup_cedartoy.sh").read_text(encoding="utf-8")
        self.assertIn("--exclude='data/vendor_saves/*/*/.view/*.html'", backup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
