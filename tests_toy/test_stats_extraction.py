"""Regression coverage for server-level statistics dependency patch points."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import server


class StatsExtractionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="stats-extraction-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "sessions.db"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("SESSIONS_DB_PATH", self.db),
            ("TURTLE_DB_PATH", self.root / "accounts.db"),
            ("VENDOR_SAVE_ROOT", self.root / "saves"),
            ("CAMPING_PLAZA_DB_PATH", self.root / "camp.db"),
            ("SHARED_CRAFT_DATA_DIR", self.root / "craft"),
        ):
            self.stack.enter_context(patch.object(server, name, value))

    def test_account_stats_use_patched_paths_and_identity_helper(self):
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE test_results (player_id TEXT, game TEXT);
                INSERT INTO test_results VALUES
                    ('7', 'mbti'), ('alias', 'mbti'), ('other', 'mbti'),
                    ('7:2', 'mbti'), ('alias', 'dnd');
            """)
        with server._db_connect() as conn:
            self.assertEqual(server._turtle_soup_stats(conn, {"id": 7})["game_count"], 0)
            conn.executescript("""
                CREATE TABLE players (user_id INTEGER, username TEXT,
                    game_count INTEGER, win_count INTEGER, ask_count INTEGER,
                    ask_count_y INTEGER, ask_count_n INTEGER,
                    ask_count_u INTEGER, ask_count_p INTEGER);
                INSERT INTO players VALUES (7, 'renamed', 5, 2, NULL, 1, 2, 3, 4);
                INSERT INTO players VALUES (NULL, 'alias', 99, 0, 0, 0, 0, 0, 0);
            """)
            with patch.object(server, "_game_player_ids", return_value=["7", "alias"]):
                result = server._game_overview(conn, {"id": 7, "username": "alias"})
            self.assertEqual(result["mbti"], {"test_count": 2})
            self.assertEqual(result["dnd"], {"test_count": 1})
            self.assertEqual(result["bdsmtest"], {"test_count": 0})
            self.assertEqual(result["turtle_soup"], dict(
                game_count=5, win_count=2, ask_count=0, ask_count_y=1,
                ask_count_n=2, ask_count_u=3, ask_count_p=4))
            with patch.object(server, "_table_exists", return_value=False):
                self.assertEqual(server._turtle_soup_stats(conn, {"id": 7})["game_count"], 0)
            with (patch.object(server, "_test_stats", return_value=result),
                  patch.object(server, "_turtle_soup_stats", return_value={"patched": True})):
                self.assertEqual(server._game_overview(conn, {})["turtle_soup"], {"patched": True})

    def test_counts_keep_missing_defaults_and_ciyuwu_fallbacks(self):
        self.assertEqual(server._count_table_rows("eco_sessions"), 0)
        self.assertEqual(server._sum_ciyuwu_runs(), 0)
        with patch.object(server, "_game_player_ids", return_value=["7"]):
            self.assertTrue(all(v == {"test_count": 0} for v in server._test_stats({}).values()))
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE ciyuwu_sessions (meta_data TEXT)")
            conn.executemany("INSERT INTO ciyuwu_sessions VALUES (?)", [
                ('{"runs": 4}',), ('{"runs": 0}',), ('{"runs": -2}',),
                ('{"runs": "bad"}',), ('invalid',), (None,),
            ])
        self.assertEqual(server._count_table_rows("ciyuwu_sessions"), 6)
        self.assertEqual(server._sum_ciyuwu_runs(), 9)
        with patch.object(server, "_table_exists", return_value=False):
            self.assertEqual(server._count_table_rows("ciyuwu_sessions"), 0)
            self.assertEqual(server._sum_ciyuwu_runs(), 0)
        with patch.object(server, "_sessions_db_connect", side_effect=sqlite3.OperationalError("fixture")):
            with self.assertRaises(sqlite3.OperationalError):
                server._sum_ciyuwu_runs()

    def test_vendor_contracts_resolve_patched_adapters_at_call_time(self):
        self.assertEqual(server._vendor_save_stats("missing"), {"save_count": 0, "file_count": 0})
        for game, filename in (("ai_life", "custom.json"), ("bar", "lite.json"),
                               ("fishing", "custom.json"), ("workkk", "game_state.json"),
                               ("garden_cat", "state.json"), ("detroit", "custom.json")):
            for player in ("7", "7:2", "guest:fixture"):
                directory = self.root / "saves" / game / player
                directory.mkdir(parents=True)
                (directory / ".lock").touch()
            (self.root / "saves" / game / "7:2" / filename).write_text("{}")
        with (patch.object(server, "ai_life_adapter", SimpleNamespace(SAVE_NAME="custom.json")),
              patch.object(server, "bar_adapter", SimpleNamespace(FULL_SAVE_NAME="full.json", LITE_SAVE_NAME="lite.json")),
              patch.object(server, "fishing_adapter", SimpleNamespace(SAVE_FILES={"save": "custom.json"})),
              patch.object(server, "detroit_adapter", SimpleNamespace(has_save=lambda player: player == "7:2"))):
            for game in ("ai_life", "bar", "fishing", "workkk", "garden_cat", "detroit"):
                with self.subTest(game=game):
                    self.assertEqual(server._vendor_save_stats(game), {"save_count": 1, "file_count": 1})
            with patch.object(server, "fishing_adapter", None):
                self.assertEqual(server._vendor_save_stats("fishing"), {"save_count": None, "file_count": 0})

    def test_catalog_uses_patched_index_and_preserves_order_and_errors(self):
        index = self.root / "index.html"
        index.write_text('const games = [\n      {id: "soup", name: "汤"},\n      {id: "eco", name: "生态"},\n      {id: "admin", name: "管理"}\n    ];')
        with (patch.object(server, "TOY_INDEX_PATH", index),
              patch.object(server, "IDENTITY_GAMES", {"eco"}),
              patch.object(server, "RITUAL_DISPLAY_NAME", "塔罗")):
            catalog = server._activity_catalog()
            # Research/deeptalk catalogs are appended independently of this fixture.
            self.assertEqual(catalog[:4], [
                {"game": "turtle_soup", "name": "汤"}, {"game": "eco", "name": "生态"},
                {"game": "tarot", "name": "塔罗"}, {"game": "bdsmtest", "name": "BDSM倾向测试"},
            ])
            self.assertNotIn("admin", {item["game"] for item in catalog})
            index.write_text("no catalog")
            with self.assertRaisesRegex(ValueError, "Homepage catalog unavailable"):
                server._activity_catalog()

    def test_public_stats_forward_helpers_strictness_and_camping_errors(self):
        server.CAMPING_PLAZA_DB_PATH.touch()
        with (patch.object(server, "_count_puzzle_box_saves", return_value=3),
              patch.object(server, "_count_table_rows", return_value=4),
              patch.object(server, "_sum_ciyuwu_runs", return_value=9) as runs,
              patch.object(server, "_vendor_save_stats", return_value={"save_count": None, "file_count": 0}),
              patch.object(server, "_count_shared_craft_saves", return_value=5) as craft,
              patch.object(server, "count_saved_tarot_sessions", return_value=2) as tarot,
              patch.object(server, "_camping_plaza_save_admin", return_value={"save_count": "6"}) as camp):
            for strict in (False, True):
                result = server._public_game_stats(strict=strict)
                self.assertEqual(result["puzzle_box"]["metric"], 3)
                self.assertEqual(result["ciyuwu"], {"metric_label": "对局数", "metric": None if strict else 9, "save_count": 4})
                self.assertEqual(result["camping_plaza"]["metric"], 6)
                self.assertEqual(result["shared_craft"], {"metric_label": "存档数", "metric": 5})
                craft.assert_called_with(strict=strict)
                camp.assert_called_with("stats", timeout=2 if strict else 20)
                tarot.assert_called_with(strict=strict)
            runs.assert_called_once_with()
            camp.side_effect = server._McpError(-32603, "fixture")
            self.assertEqual(server._public_game_stats()["camping_plaza"]["metric"], 0)
            self.assertIsNone(server._public_game_stats(strict=True)["camping_plaza"]["metric"])
            craft.return_value = None
            self.assertIsNone(server._public_game_stats()["shared_craft"]["metric"])


class SharedCraftSaveCountTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="craft-count-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "craft"
        self.patch = patch.object(server, "SHARED_CRAFT_DATA_DIR", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def save(self, room, name, data):
        target = self.root / "rooms" / str(room) / (name + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    def count(self, strict=False):
        return server._count_shared_craft_saves(strict=strict)

    def test_empty_visits_and_creative_rooms_count_once_without_writes(self):
        self.save(1, "canvas", {"strokes": []})
        self.save(1, "beads", {"boards": [{"id": "blank", "mode": "gift", "cells": {}}]})
        self.save(1, "gallery", {"items": []})
        self.save(2, "canvas", {"strokes": [
            {"actor": "human", "points": [{"x": 1, "y": 1}]},
            {"actor": "ai-one", "points": [{"x": 2, "y": 1}]},
            {"actor": "ai-two", "points": [{"x": 3, "y": 1}]},
        ]})
        self.save(2, "beads", {"boards": [{"id": "same-room", "mode": "together", "cells": {"0,0": {}}}]})
        self.save(2, "gallery", {"items": [{"id": "work", "authors": ["human", "ai-one", "ai-two"]}]})
        self.save(3, "beads", {"boards": [{"id": "bead-only", "mode": "separate", "cells": {"0,0": {}}}]})
        self.save(4, "gallery", {"items": [{"id": "gallery-only", "authors": ["ai"]}]})
        self.save(5, "participants", {"participants": [{"id": "5"}]})
        (self.root / "rooms" / "6").mkdir()
        self.save("backup", "canvas", {"strokes": [{}]})
        (self.root / "rooms" / "7").symlink_to(self.root / "rooms" / "2", target_is_directory=True)
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        for strict in (False, True):
            self.assertEqual(self.count(strict), 3)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_missing_directory_is_not_created_and_strict_is_unknown(self):
        self.assertEqual(self.count(), 0)
        self.assertIsNone(self.count(True))
        self.assertFalse(self.root.exists())

    def test_corrupt_unreadable_and_unexpected_files_are_unknown_not_partial(self):
        self.save(1, "gallery", {"items": [{"id": "valid", "authors": []}]})
        target = self.save(2, "canvas", {})
        for raw in (b'bad json', b'\xff', b'[]', b'{"strokes":{}}', b'{"strokes":[{}]}',
                    b'{"strokes":[false]}', b'[' * 2000):
            target.write_bytes(raw)
            for strict in (False, True):
                self.assertIsNone(self.count(strict))
            self.assertEqual(target.read_bytes(), raw)
            self.assertEqual(list(target.parent.iterdir()), [target], "no backup/lock/repair writes")
        target.write_text('{"strokes":[]}')
        original = os.open
        def denied(path, *args, **kwargs):
            if Path(path) == target:
                raise PermissionError("fixture")
            return original(path, *args, **kwargs)
        with patch.object(server.public_stats.os, "open", side_effect=denied):
            self.assertIsNone(self.count())
            self.assertIsNone(self.count(True))
        with patch.object(server.public_stats.os, "scandir", side_effect=PermissionError("fixture")):
            self.assertIsNone(self.count())
        target.unlink()
        os.mkfifo(target)
        self.assertIsNone(self.count(), "special files must not block")
        target.unlink()
        target.symlink_to(self.root / "rooms" / "1" / "gallery.json")
        self.assertIsNone(self.count(), "do not follow metadata symlinks")

    def test_large_room_catalog_counts_cold_then_reuses_validated_metadata(self):
        # Real production has 200+ rooms and more than 13MB of JSON. That was
        # repeatedly hitting the original 0.25s/16MB limits, yielding "--".
        extra = "x" * (96 * 1024)
        for room in range(1, 228):
            self.save(room, "canvas", {"strokes": [{"actor": "human", "points": [{"x": 1, "y": 1}]}],
                                        "padding": extra})
        self.assertEqual(self.count(), 227)
        with patch.object(server.public_stats.json, "loads", side_effect=AssertionError("cached JSON reread")):
            self.assertEqual(self.count(strict=True), 227)
        # One edited and one deleted file must immediately update the metric;
        # no player content should survive in the count cache.
        self.save(2, "canvas", {"strokes": []})
        self.assertEqual(self.count(strict=True), 226)
        (self.root / "rooms" / "3" / "canvas.json").unlink()
        self.assertEqual(self.count(strict=True), 225)

    def test_scan_budgets_return_unknown(self):
        target = self.save(1, "canvas", {"strokes": []})
        with target.open("wb") as stream:
            stream.truncate(2 * 1024**2 + 1)
        self.assertIsNone(self.count())
        target.write_text('{"strokes":[]}')
        with patch.object(server.public_stats.time, "monotonic", side_effect=[0, 3]):
            self.assertIsNone(self.count())
        payload = {"strokes": [], "padding": " " * (1024**2)}
        for room in range(1, 18):
            self.save(room, "canvas", payload)
        with (patch.object(server.public_stats, "_CRAFT_BYTE_BUDGET", 16 * 1024**2),
              patch.object(server.public_stats.time, "monotonic", return_value=0)):
            self.assertIsNone(self.count(), "total byte budget")
        for room in range(1, 18):
            (self.root / "rooms" / str(room) / "canvas.json").unlink()
        for room in range(18, 2050):
            (self.root / "rooms" / str(room)).mkdir()
        with patch.object(server.public_stats.time, "monotonic", return_value=0):
            self.assertIsNone(self.count(), "entry budget")


if __name__ == "__main__":
    unittest.main()
