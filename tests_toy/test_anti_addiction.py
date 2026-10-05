"""Anti-addiction behavior and runtime server patch compatibility, on temporary DBs."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class RecordingLock:
    def __init__(self, events):
        self.events = events
        self.entered = False

    def __enter__(self):
        assert not self.entered
        self.entered = True
        self.events.append("lock")

    def __exit__(self, *args):
        self.events.append("unlock")
        self.entered = False


class AntiAddictionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="anti-addiction-")
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "accounts.db"
        self.events = []
        self.lock = RecordingLock(self.events)
        self.ai = {"id": 42, "username": "temporary-ai", "is_ai": 1}
        self.human = {"id": 7, "is_ai": 0}
        self.patch(server, "TURTLE_DB_PATH", self.db_path)
        self.patch(server, "SESSIONS_DB_PATH", Path(temporary.name) / "unused.db")
        self.sessions = self.patch(server, "_sessions_db_connect", side_effect=AssertionError("wrong database"))
        self.patch(server, "_ANTI_ADDICTION_ANY_ENABLED", None)
        self.patch(server, "_ANTI_ADDICTION_LOCK", self.lock)
        self.clock = self.patch(server.time, "time", return_value=1000.0)
        self.bound = self.patch(server, "_require_bound_ai", return_value=self.ai)
        self.current = self.patch(server, "_current_account", return_value=self.human)
        self.public = self.patch(server, "_public_user", side_effect=lambda user: {"id": user["id"]})
        self.connect = server._db_connect
        with self.connect() as conn:
            server._init_anti_addiction_tables(conn)
            conn.executescript("""
                CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT, is_ai INTEGER,
                    is_admin INTEGER, avatar_type TEXT, avatar_value TEXT, created_at TEXT,
                    last_active_at TEXT, deleted_at TEXT);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER, created_at TEXT);
                INSERT INTO toy_users(id, username, is_ai) VALUES
                    (42, 'temporary-ai', 1), (43, 'other-ai', 1), (44, 'deleted-ai', 1);
                UPDATE toy_users SET deleted_at = 'deleted' WHERE id = 44;
                INSERT INTO user_bindings VALUES (7, 42, '2026-01-01'), (7, 44, '2026-01-02');
            """)
        self.patch(server, "_db_connect", side_effect=self.traced_connect)

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def traced_connect(self):
        conn = self.connect()
        # Freeze SQL timestamps too, so full snapshots can be compared to the baseline.
        conn.create_function("datetime", -1, lambda *args: "2026-10-05 20:00:00")
        conn.set_trace_callback(self.events.append)
        self.addCleanup(conn.close)
        return conn

    def snapshot(self):
        with self.connect() as conn:
            return {
                table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                for table in ("anti_addiction_settings", "anti_addiction_states")
            }

    def call(self, name, *args):
        """Single observation point for the pre/post extraction comparison runner."""
        return getattr(server, name)(*args)

    def save(self, **overrides):
        body = dict(ai_user_id=42, enabled=True, remind_threshold=2,
                    force_threshold=3, lock_minutes=1, allow_self_reset=False)
        body.update(overrides)
        return self.call("_save_anti_addiction_settings", "token", body)

    def context(self, player="42", game="eco"):
        return self.call("_anti_addiction_context", game, self.ai, player)

    def seed(self, player="42", streak=2, locked=0, locked_at=None, last_play_at=1000):
        with self.connect() as conn:
            conn.execute("""INSERT OR REPLACE INTO anti_addiction_states
                (player_id, streak, locked, locked_at, last_play_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'fixture')""",
                (player, streak, locked, locked_at, last_play_at))

    def state(self, player="42"):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM anti_addiction_states WHERE player_id = ?", (player,)).fetchone()
            return dict(row) if row else None

    def test_defaults_settings_and_validation(self):
        defaults = dict(enabled=False, remind_threshold=30, force_threshold=50,
                        lock_minutes=30, allow_self_reset=True)
        self.assertEqual(self.call("_anti_addiction_defaults"), defaults)
        with self.connect() as conn:
            self.assertEqual(self.call("_anti_addiction_settings_for_ai", conn, 42), defaults)
        for field in ("remind_threshold", "force_threshold", "lock_minutes"):
            for raw in (None, "bad", 0, -1, 10001):
                with self.subTest(field=field, raw=raw), self.assertRaises(server._McpError) as caught:
                    self.call("_anti_addiction_validate_settings", {field: raw})
                self.assertEqual(caught.exception.code, -32602)
        with self.assertRaisesRegex(server._McpError, "强制阈值不能小于提醒阈值"):
            self.call("_anti_addiction_validate_settings", {"force_threshold": 29})
        self.assertEqual(self.call("_anti_addiction_validate_settings", {
            "enabled": "false", "remind_threshold": "1", "force_threshold": 10000,
            "lock_minutes": 1.9, "allow_self_reset": 0,
        }), dict(enabled=True, remind_threshold=1, force_threshold=10000,
                 lock_minutes=1, allow_self_reset=False))

    def test_machine_listing_and_account_callbacks(self):
        machines = self.call("_anti_addiction_machines", "token")["machines"]
        self.assertEqual([item["id"] for item in machines], [42])
        # Existing LEFT JOIN semantics: absent setting has allow_self_reset=False.
        self.assertFalse(machines[0]["anti_addiction"]["allow_self_reset"])
        self.save()
        self.assertTrue(self.call("_anti_addiction_machines", "token")["machines"][0]["anti_addiction"]["enabled"])
        self.bound.assert_called_with("token", 42)
        self.current.return_value = self.ai
        with self.assertRaisesRegex(server._McpError, "只有人类账号可以管理小机"):
            self.call("_anti_addiction_machines", "token")
        self.bound.side_effect = server._McpError(-32004, "未绑定该小机")
        for name in ("_save_anti_addiction_settings", "_reset_anti_addiction_state"):
            with self.assertRaisesRegex(server._McpError, "未绑定该小机"):
                self.call(name, "token", {"ai_user_id": 43})

    def test_enable_cache_rechecks_after_lock_and_save(self):
        self.assertFalse(self.call("_anti_addiction_any_enabled"))
        self.events.clear()
        self.assertFalse(self.call("_anti_addiction_any_enabled"))
        self.assertEqual(self.events, [])
        self.save()
        self.assertIs(server._ANTI_ADDICTION_ANY_ENABLED, True)
        self.assertTrue(self.call("_anti_addiction_any_enabled"))
        server._ANTI_ADDICTION_ANY_ENABLED = None

        class ConcurrentCacheFill(RecordingLock):
            def __enter__(self):
                super().__enter__()
                server._ANTI_ADDICTION_ANY_ENABLED = False

        with patch.object(server, "_ANTI_ADDICTION_LOCK", ConcurrentCacheFill(self.events)):
            self.events.clear()
            self.assertFalse(self.call("_anti_addiction_any_enabled"))
            self.assertEqual(self.events, ["lock", "unlock"])

    def test_context_filter_and_no_context_paths(self):
        self.assertIsNone(self.context())
        self.save()
        for game, user, player in (("mbti", self.ai, "42"), ("unknown", self.ai, "42"),
                                   ("eco", None, "42"), ("eco", self.human, "7"),
                                   ("eco", self.ai, None), ("eco", {"id": 43, "is_ai": 1}, "43")):
            self.assertIsNone(self.call("_anti_addiction_context", game, user, player))
        self.assertEqual(self.context("42:2")["player_id"], "42:2")
        self.assertIsNone(self.call("_anti_addiction_preflight", "eco", None))
        self.assertEqual(self.call("_anti_addiction_record_success", None), "")
        for player in (None, 42):
            self.assertEqual(self.call("_anti_addiction_rest", None, player), {
                "player_id": str(player) if player else None, "text": "已重置，可以开新局了。"})

    def test_count_reminder_lock_and_rest_across_games(self):
        self.save()
        eco = self.context()
        duel = self.context(game="duel")
        self.assertEqual(self.call("_anti_addiction_record_success", eco), "")
        self.assertEqual(self.call("_anti_addiction_record_success", duel), "玩了 2 轮了，喘口气；到 3 轮会请你休息一下。")
        text = self.call("_anti_addiction_record_success", eco)
        self.assertIn("1 分钟后自动解锁", text)
        self.assertEqual(self.state()["locked_at"], 1000.0)
        self.assertEqual(self.call("_anti_addiction_preflight", "duel", duel)["text"], text)
        self.assertIn("这次需要真的休息", self.call("_anti_addiction_rest", eco, "ignored")["text"])
        self.assertEqual(self.state()["streak"], 3)
        self.assertEqual(self.state()["locked"], 1)
        self.assertEqual(self.call("_anti_addiction_record_success", self.context("42:2")), "")
        self.assertEqual(self.state("42:2")["streak"], 1)
        self.save(allow_self_reset=True)
        self.assertIn("发送 rest 即可继续", self.call("_anti_addiction_preflight", "eco", self.context())["text"])
        self.assertEqual(self.call("_anti_addiction_rest", self.context(), "ignored")["text"], "已重置，可以开新局了。")
        self.assertEqual(self.state()["streak"], 0)
        self.assertIsNone(self.state()["locked_at"])

    def test_lock_and_idle_window_boundaries(self):
        self.save()
        context = self.context()
        for locked in (0, 1):
            for elapsed in (59.999, 60, 60.001):
                with self.subTest(locked=locked, elapsed=elapsed):
                    self.seed(locked=locked, locked_at=1000 if locked else None)
                    self.clock.return_value = 1000 + elapsed
                    result = self.call("_anti_addiction_preflight", "eco", context)
                    self.assertEqual(result is not None, bool(locked and elapsed < 60))
                    self.assertEqual(self.state()["streak"], 2 if elapsed < 60 else 0)
        self.seed(locked=1, locked_at=None)
        self.clock.return_value = 999999
        self.assertIsNotNone(self.call("_anti_addiction_preflight", "eco", context))
        self.seed(last_play_at=None)
        self.assertIsNone(self.call("_anti_addiction_preflight", "eco", context))
        self.assertEqual(self.state()["streak"], 2)
        self.assertEqual(self.call("_anti_addiction_lock_seconds", {"lock_minutes": 0}), 60)

    def test_disable_and_human_reset_only_target_ai_slots(self):
        self.save()
        for player in ("42", "42:2", "42:5", "420", "43", "guest:42"):
            self.seed(player, locked=1, locked_at=1000)
        self.clock.return_value = 1001
        self.save(enabled=False)
        self.assertIs(server._ANTI_ADDICTION_ANY_ENABLED, False)
        for player in ("42", "42:2", "42:5"):
            self.assertEqual(self.state(player)["streak"], 0)
            self.assertEqual(self.state(player)["last_play_at"], 1001)
        before = self.snapshot()
        self.clock.return_value = 1002
        self.save(enabled=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.call("_reset_anti_addiction_state", "token", {"ai_user_id": 42}),
                         {"ok": True, "ai": {"id": 42}, "message": "已重置"})
        for player in ("42", "42:2", "42:5"):
            self.assertIsNone(self.state(player))
        for player in ("420", "43", "guest:42"):
            self.assertEqual(self.state(player)["streak"], 2)

    def test_lock_transaction_order_and_rollback(self):
        self.save()
        context = self.context()
        for name, args in (("_anti_addiction_record_success", (context,)),
                           ("_anti_addiction_rest", (context, "42")),
                           ("_anti_addiction_preflight", ("eco", context))):
            self.events.clear()
            self.call(name, *args)
            self.assertEqual(self.events[0], "lock")
            self.assertEqual(self.events[-1], "unlock")
            if "BEGIN " in self.events:
                self.assertLess(self.events.index("BEGIN "), self.events.index("COMMIT"))
        before = self.snapshot()
        original = server._anti_addiction_state_for_update

        def fail_after_write(conn, player, settings, now):
            original(conn, player, settings, now)
            conn.execute("UPDATE anti_addiction_states SET streak = 999")
            raise RuntimeError("temporary failure")

        with patch.object(server, "_anti_addiction_state_for_update", side_effect=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, "temporary failure"):
                self.call("_anti_addiction_record_success", context)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("ROLLBACK", self.events)
        self.assertFalse(self.lock.entered)

    def test_nested_server_helpers_and_constants_remain_patchable(self):
        with patch.object(server, "ANTI_ADDICTION_DEFAULT_REMIND", 9):
            self.assertEqual(self.call("_anti_addiction_public_settings")["remind_threshold"], 9)
        with patch.object(server, "_anti_addiction_defaults", return_value={"patched": True}):
            self.assertEqual(self.call("_anti_addiction_public_settings"), {"patched": True})
        self.save()
        context = self.context()
        with patch.object(server, "_anti_addiction_notice", return_value="patched notice") as notice:
            self.assertEqual(self.call("_anti_addiction_record_success", context), "patched notice")
            notice.assert_called_once_with(1, context["settings"])
        with patch.object(server, "_anti_addiction_lock_text", return_value="patched lock"):
            self.assertEqual(self.call("_anti_addiction_notice", 3, context["settings"]), "patched lock")
        with patch.object(server, "ANTI_ADDICTION_MINI_GAMES", frozenset({"test-game"})):
            self.assertIsNotNone(self.context(game="test-game"))
            self.assertIsNone(self.context())
        self.sessions.assert_not_called()

    def test_failed_settings_commit_preserves_existing_cache_write_order(self):
        self.assertFalse(self.call("_anti_addiction_any_enabled"))

        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("temporary commit failure")

        def failing_connect():
            conn = sqlite3.connect(self.db_path, factory=FailingCommit)
            conn.row_factory = sqlite3.Row
            conn.set_trace_callback(self.events.append)
            self.addCleanup(conn.close)
            return conn

        before = self.snapshot()
        self.events.clear()
        with patch.object(server, "_db_connect", side_effect=failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "temporary commit failure"):
                self.save()
        self.assertEqual(self.snapshot(), before)
        # Preserve the original cache-before-commit behavior, even on failure.
        self.assertIs(server._ANTI_ADDICTION_ANY_ENABLED, True)
        self.assertIn("ROLLBACK", self.events)
        self.assertNotIn("lock", self.events)


if __name__ == "__main__":
    unittest.main()
