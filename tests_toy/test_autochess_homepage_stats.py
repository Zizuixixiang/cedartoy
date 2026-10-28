"""Public room-file totals: isolated storage and an unauthenticated HTTP request."""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server


class AutochessHomepageStatsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = self.base / "rooms"
        self.stack.enter_context(patch.object(server, "AUTOCHESS_ROOM_DATA", self.root))

    def count(self, strict=False):
        return server._count_autochess_saves(strict=strict)

    def test_counts_all_phases_only_canonical_top_level_regular_files(self):
        self.root.mkdir()
        for index, phase in enumerate(("waiting", "prepare", "finished")):
            (self.root / f"{index:012x}.json").write_text(json.dumps({"phase": phase}))
        for name in ("accounts.db", "accounts.db-wal", "slot.json", "101:2.json", "junk.json",
                     "ABCDEF123456.json", "0123456789abc.json", "0123456789a.json",
                     "abcdef123456.json.bak", "abcdef123456.json.pending", ".abcdef123456.json"):
            (self.root / name).write_text("invalid junk")
        for directory in ("economy", "backups", "101:2", "abcdef123456.json"):
            (self.root / directory).mkdir()
            (self.root / directory / "abcdef123456.json").write_text("{}")
        (self.root / "eeeeeeeeeeee.json").symlink_to(self.root / "000000000000.json")
        (self.root / "dddddddddddd.json").symlink_to(self.root / "absent")
        os.mkfifo(self.root / "ffffffffffff.json")
        before = {p.name: p.lstat() for p in self.root.iterdir()}
        # Counting must not parse or read any room/private content.
        with patch.object(Path, "read_bytes", side_effect=AssertionError("content read")), \
             patch.object(Path, "read_text", side_effect=AssertionError("content read")):
            self.assertEqual(self.count(), 3)
            self.assertEqual(self.count(True), 3)
        self.assertEqual(before, {p.name: p.lstat() for p in self.root.iterdir()})
        (self.root / "000000000002.json").unlink()
        self.assertEqual(self.count(), 2)

    def test_missing_empty_and_unreadable_are_distinct(self):
        for strict in (False, True):
            self.assertIsNone(self.count(strict))
        self.assertFalse(self.root.exists())
        self.root.mkdir()
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count(True), 0)
        with patch.object(server.public_stats.os, "scandir", side_effect=PermissionError("fixture")):
            self.assertIsNone(self.count())
            self.assertIsNone(self.count(True))
        other = self.base / "other"
        other.mkdir()
        with patch.object(server, "AUTOCHESS_ROOM_DATA", other):
            self.assertEqual(self.count(), 0)
        linked = self.base / "linked"
        linked.symlink_to(other, target_is_directory=True)
        with patch.object(server, "AUTOCHESS_ROOM_DATA", linked):
            self.assertIsNone(self.count())

    def test_time_and_entry_budgets_return_unknown_not_partial(self):
        self.root.mkdir()
        (self.root / "abcdef123456.json").touch()
        for strict in (False, True):
            with patch.object(server.public_stats.time, "monotonic", side_effect=[0, 1]):
                self.assertIsNone(self.count(strict))
            with patch.object(server.public_stats.os, "scandir") as scan:
                scan.return_value.__enter__.return_value = iter([type("Entry", (), {"name": "junk"})()] * 10001)
                with patch.object(server.public_stats.time, "monotonic", return_value=0):
                    self.assertIsNone(self.count(strict))

    def test_public_http_uses_dynamic_path_and_returns_only_aggregate(self):
        self.root.mkdir()
        (self.root / "abcdef123456.json").write_text('{"private":"must not be read"}')
        # Isolate every unrelated aggregate so no production data is accessed.
        for name, value in (("_count_puzzle_box_saves", 0), ("_count_table_rows", 0),
                            ("_sum_ciyuwu_runs", 0), ("count_saved_tarot_sessions", 0),
                            ("_count_shared_craft_saves", 0),
                            ("_vendor_save_stats", {"save_count": 0, "file_count": 0})):
            self.stack.enter_context(patch.object(server, name, return_value=value))
        self.stack.enter_context(patch.object(server, "CAMPING_PLAZA_DB_PATH", self.base / "absent.db"))
        class QuietHandler(server.CedarToyHandler):
            def log_message(self, *args):
                pass
        def get():
            output = io.BytesIO()
            class Connection:
                def makefile(self, *args):
                    return io.BytesIO(b"GET /api/games/stats HTTP/1.0\r\nHost: localhost\r\n\r\n")
                def sendall(self, data):
                    output.write(data)
            # Parse an actual unauthenticated HTTP request without opening sockets.
            QuietHandler(Connection(), ("127.0.0.1", 0), None)
            headers, body = output.getvalue().split(b"\r\n\r\n", 1)
            self.assertIn(b"200 OK", headers)
            self.assertIn(b"no-store", headers)
            return json.loads(body)["autochess"]
        self.assertEqual(get(), {"metric_label": "存档数", "metric": 1})
        with patch.object(server, "AUTOCHESS_ROOM_DATA", self.base / "missing"):
            self.assertEqual(get(), {"metric_label": "存档数", "metric": None})


class AutochessHomepageGrantTests(unittest.TestCase):
    """Real temporary account auth, without a socket or running game engine."""
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(server, "TURTLE_DB_PATH", root / "accounts.db"))
        self.stack.enter_context(patch.object(server, "TOY_SECRET", "homepage-grant-fixture"))
        self.stack.enter_context(patch.object(server, "_check_duel_web_request_rate_limit", return_value=True))
        self.stack.enter_context(patch.dict(os.environ, {"AUTOCHESS_AUTHOR_USER_IDS": "101,202"}))
        self.stack.enter_context(patch("autochess_adapter.web.request", side_effect=AssertionError("access must stay local")))
        with server._db_connect() as conn:
            conn.executescript('''CREATE TABLE toy_users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
                is_ai INTEGER DEFAULT 0,is_admin INTEGER DEFAULT 0, deleted_at TEXT, last_active_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE user_bindings(human_user_id INTEGER,ai_user_id INTEGER);
                INSERT INTO toy_users(id,username,is_ai,is_admin) VALUES
                    (101,'author',0,0),(202,'granted-ai',1,0),(303,'site-admin',0,1),(404,'bound-human',0,0);
                INSERT INTO user_bindings VALUES(404,202);''')
            server._init_account_security_schema(conn)
            self.tokens = {str(row['id']): server._issue_initial_account_token_in_transaction(conn, dict(row))
                           for row in conn.execute('SELECT * FROM toy_users').fetchall()}

    def call(self, who=None, **body):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Authorization": "Bearer " + self.tokens[who]} if who else {}
        return server.autochess_web.api(handler, server, body, "/autochess/api/stats")

    def test_server_grant_does_not_follow_admin_binding_or_claimed_id(self):
        self.assertEqual(self.call("101", op="access"), {"allowed": True})
        for who in ("303", "404"):
            self.assertEqual(self.call(who, op="access", player_id="101", ai_id="202", is_admin=True), {"allowed": False})
            with self.assertRaises(server._McpError):
                self.call(who, range="1h", player_id="101", ai_id="202", is_admin=True)
        for who in (None, "202"):
            with self.assertRaises(server._McpError):
                self.call(who, op="access")
        with patch.dict(os.environ, {"AUTOCHESS_AUTHOR_USER_IDS": ""}):
            self.assertEqual(self.call("101", op="access"), {"allowed": False})
            with self.assertRaises(server._McpError):
                self.call("101", range="1h")
