"""Demo acceptance uses disposable databases and ASGI only, never production."""
import asyncio
from collections import Counter
from contextlib import closing
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import create_rummikub_review_demo as demo
from app import achievements, database, framework
from app.games.rummikub import TILES
from cedar_backend import duel_history


class RummikubDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-rummikub-demo-")
        self.db = Path(self.tmp.name) / "duel.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db)
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_engine_conservation_scores_and_terminal_projection(self):
        room = demo.build_demo()
        state = room["board_state"]
        self.assertEqual(Counter(sum(state["hands"].values(), []) + sum(state["melds"], []) + state["pool"]),
                         Counter(TILES.keys()))
        self.assertTrue(all(state["hands"].values()))
        self.assertEqual(room["result"]["reason"], "blocked")
        self.assertEqual(sum(room["result"]["tile_scores"].values()), 0)
        self.assertEqual(room["result"]["remaining_values"],
                         {p: sum(TILES[t]["number"] or 30 for t in hand) for p, hand in state["hands"].items()})
        self.assertEqual(room["stake"], 0)
        view = demo.validate_projection(room)
        self.assertEqual(len(view["board_state"]["terminal_hands"]), 4)
        for status in ("finished", "archived"):
            room["status"] = status
            self.assertEqual(demo.validate_projection(room)["board_state"], view["board_state"])

    def test_insertion_idempotence_no_side_effects_history_and_backfill(self):
        allowed = {"rooms", "room_participants", "room_invites", "room_messages", "room_event_cursors", "sqlite_sequence"}
        with closing(database.connect()) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in allowed]
            before = {t: conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
        demo.create_in_database(self.db)
        room = framework.get_room(demo.ROOM_ID, "human", "1")
        self.assertEqual(room["room_kind"], "invite")
        self.assertTrue(room["preserved"])
        self.assertEqual(room["status"], "finished")
        with self.assertRaisesRegex(ValueError, "重复"):
            demo.create_in_database(self.db)
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM rooms").fetchone()[0], 1)
            self.assertEqual(achievements.backfill_authoritative_matches(conn), 0)
            for t in tables:
                self.assertEqual(conn.execute(f'SELECT * FROM "{t}"').fetchall(), before[t], t)
        history = duel_history._duel_history_for_user(
            {"id": 1, "is_ai": 0}, limit=10, duel_db_path=self.db, logger=logging.getLogger(__name__),
            history_outcome=duel_history._duel_history_outcome,
            history_result_detail=duel_history._duel_history_result_detail)
        self.assertTrue(history["available"])
        self.assertEqual(history["total"], 0)
        self.assertEqual(history["matches"], [])
        # Startup migrations must not turn the example into a match/notification.
        database.init_db()
        with closing(database.connect()) as conn:
            self.assertEqual(achievements.backfill_authoritative_matches(conn), 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM room_messages").fetchone()[0], 1)

    def test_transaction_rolls_back_on_failed_projection(self):
        with patch.object(demo, "validate_projection", side_effect=ValueError("projection failed")):
            with self.assertRaises(ValueError):
                demo.create_in_database(self.db)
        with closing(database.connect()) as conn:
            for table in ("rooms", "room_participants", "room_messages", "room_invites", "room_event_cursors"):
                self.assertEqual(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_account_exact_identity_and_no_credentials(self):
        account = Path(self.tmp.name) / "accounts.db"
        # Credential columns intentionally do not exist.
        with sqlite3.connect(account) as conn:
            conn.execute("CREATE TABLE toy_users(id INTEGER, username TEXT, is_ai INTEGER)")
            conn.execute("INSERT INTO toy_users VALUES (1,'南杉',0)")
        self.assertEqual(demo.read_account(account, "南杉", 1)["id"], 1)
        for name, uid in (("南山", 1), ("南杉", 2), (None, None)):
            with self.assertRaises(ValueError):
                demo.read_account(account, name, uid)
        with sqlite3.connect(account) as conn:
            conn.execute("UPDATE toy_users SET is_ai=1")
        with self.assertRaises(ValueError):
            demo.read_account(account, "南杉", 1)

    def test_apply_cli_requires_confirmation_backup_and_rejects_repeat(self):
        account = Path(self.tmp.name) / "accounts.db"
        backup = Path(self.tmp.name) / "backup.db"
        with sqlite3.connect(account) as conn:
            conn.execute("CREATE TABLE toy_users(id INTEGER, username TEXT, is_ai INTEGER)")
            conn.execute("INSERT INTO toy_users VALUES (1,'南杉',0)")
        with closing(sqlite3.connect(self.db)) as source, closing(sqlite3.connect(backup)) as dest:
            source.backup(dest)
        command = [sys.executable, str(demo.ROOT / "scripts/create_rummikub_review_demo.py"), "--apply",
                   "--account-db", str(account), "--duel-db", str(self.db), "--backup", str(backup)]
        denied = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(denied.returncode, 0)
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM rooms").fetchone()[0], 0)
        command += ["--confirm-username", "南杉", "--confirm-user-id", "1"]
        accepted = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn('/duel/?room=RKDEMO01', accepted.stdout)
        repeated = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(repeated.returncode, 0)
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM rooms").fetchone()[0], 1)

    def test_real_asgi_route_owner_only_no_token_impersonation(self):
        import httpx
        from app.main import app
        demo.create_in_database(self.db)

        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                url = f"/api/rooms/{demo.ROOM_ID}"
                for viewer in ("", "2", "human:1", "1:2"):
                    response = await client.get(url, headers={"X-Duel-Human-Player": viewer})
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertNotIn("terminal_hands", response.text)
                response = await client.get(url + "?player_id=1", headers={"X-Duel-Human-Player": "2"})
                self.assertEqual(response.status_code, 403)
                for _ in range(2):
                    response = await client.get(url, headers={"X-Duel-Human-Player": "1"})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(len(response.json()["room"]["board_state"]["terminal_hands"]), 4)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
