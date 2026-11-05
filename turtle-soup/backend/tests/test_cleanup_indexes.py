"""Cleanup regressions on disposable SQLite databases; no live accounts or jobs."""
import ast
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import database
import scheduler


INDEXES = {
    "idx_judge_audit_game_log": ("judge_audit_logs", ["game_log_id"]),
    "idx_judge_audit_player": ("judge_audit_logs", ["player_id"]),
    "idx_game_logs_room": ("game_logs", ["room_id"]),
    "idx_game_logs_player_created": ("game_logs", ["player_id", "created_at"]),
}


class CleanupIndexesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(prefix="soup-cleanup-test-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.db"
        patched = patch.object(database, "DB_PATH", self.path)
        patched.start()
        self.addCleanup(patched.stop)
        await database.init_db()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def snapshot(self):
        with closing(self.connect()) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )]
            return {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}

    def assert_indexes_and_integrity(self):
        with closing(self.connect()) as conn:
            for name, (table, columns) in INDEXES.items():
                self.assertIn(name, [r[1] for r in conn.execute(f'PRAGMA index_list("{table}")')])
                self.assertEqual([r[2] for r in conn.execute(f'PRAGMA index_info("{name}")')], columns)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def plan(self, query, params):
        with closing(self.connect()) as conn:
            return "\n".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + query, params))

    async def player(self, name, *, guest=False, old=True, active=False):
        pid = await database.execute(
            "INSERT INTO players(username, is_guest) VALUES (?, ?)", (name, int(guest))
        )
        # INSERT triggers deliberately normalize timestamps, so age fixtures afterwards.
        if old:
            await database.execute(
                "UPDATE players SET created_at=datetime('now','localtime','-72 hours'), "
                "last_active_at=datetime('now','localtime','-72 hours') WHERE id=?", (pid,)
            )
        if active:
            await database.execute(
                "UPDATE players SET last_active_at=datetime('now','localtime') WHERE id=?", (pid,)
            )
        return pid

    async def room(self, rid, pid, *, status="finished", old=True, finished=True):
        await database.execute(
            "INSERT INTO rooms(id, surface, answer, status, created_by, winner_id) "
            "VALUES (?, 'fixture surface', 'fixture answer', ?, ?, ?)", (rid, status, pid, pid)
        )
        age = "-72 hours" if old else "-1 hour"
        await database.execute(
            "UPDATE rooms SET created_at=datetime('now','localtime',?), "
            "finished_at=CASE WHEN ? THEN datetime('now','localtime',?) END WHERE id=?",
            (age, finished, age, rid),
        )

    async def log(self, rid, pid, *, old=True):
        lid = await database.execute(
            "INSERT INTO game_logs(room_id, player_id, type, content) VALUES (?, ?, 'ask', 'fixture')",
            (rid, pid),
        )
        if old:
            await database.execute(
                "UPDATE game_logs SET created_at=datetime('now','localtime','-72 hours') WHERE id=?", (lid,)
            )
        return lid

    async def references(self, rid, pid, lid):
        audit = await database.execute(
            "INSERT INTO judge_audit_logs(room_id, player_id, game_log_id, action, request_json, raw_response) "
            "VALUES (?, ?, ?, 'ask', 'fixture request', 'fixture response')", (rid, pid, lid)
        )
        report = await database.execute(
            "INSERT INTO reports(room_id, reporter_id, target_player_id, log_id, reason) "
            "VALUES (?, ?, ?, ?, 'fixture reason')", (rid, pid, pid, lid)
        )
        for table in ("room_presence", "room_answer_reveals", "room_answer_reveal_prompts"):
            await database.execute(f"INSERT INTO {table}(room_id, player_id) VALUES (?, ?)", (rid, pid))
        await database.execute("INSERT INTO room_hint_views(log_id, player_id) VALUES (?, ?)", (lid, pid))
        await database.execute(
            "INSERT INTO room_notes(room_id, player_id, content) VALUES (?, ?, 'fixture note')", (rid, pid)
        )
        return audit, report

    async def test_new_database_and_repeated_init_preserve_data(self):
        pid = await self.player("registered")
        await self.room("fixture", pid)
        await self.references("fixture", pid, await self.log("fixture", pid))
        before = self.snapshot()
        for _ in range(2):
            await database.init_db()
            self.assertEqual(self.snapshot(), before)
            self.assert_indexes_and_integrity()

    async def test_old_database_additive_migration_is_independently_extractable(self):
        pid = await self.player("old-schema")
        await self.room("fixture", pid)
        await self.references("fixture", pid, await self.log("fixture", pid))
        with closing(self.connect()) as conn:
            for name in INDEXES:
                conn.execute(f'DROP INDEX "{name}"')
            conn.commit()
        before = self.snapshot()
        # This is also the deployment extraction path: no module import/init side effects.
        tree = ast.parse((BACKEND / "database.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and
                    any(isinstance(t, ast.Name) and t.id == "CLEANUP_INDEX_SQL" for t in n.targets))
        sql = ast.literal_eval(node.value)
        with closing(self.connect()) as conn:
            for _ in range(2):
                conn.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")
        self.assertEqual(self.snapshot(), before)
        self.assert_indexes_and_integrity()
        # Separately exercise startup upgrading an old database, not only the SQL constant.
        with closing(self.connect()) as conn:
            for name in INDEXES:
                conn.execute(f'DROP INDEX "{name}"')
            conn.commit()
        for _ in range(2):
            await database.init_db()
            self.assertEqual(self.snapshot(), before)
            self.assert_indexes_and_integrity()

    async def test_actual_room_delete_and_unlink_plans_do_not_scan_audit_table(self):
        with patch.object(scheduler, "execute", new_callable=AsyncMock) as execute:
            await scheduler._delete_room("fixture")
        delete = next(c for c in execute.call_args_list if c.args[0].startswith("DELETE FROM game_logs"))
        plan = self.plan(*delete.args)
        self.assertIn("idx_game_logs_room", plan)
        self.assertIn("idx_judge_audit_game_log", plan)
        self.assertNotIn("SCAN judge_audit_logs", plan)
        unlink = next(c for c in execute.call_args_list if c.args[0].startswith("UPDATE judge_audit_logs SET game_log_id"))
        plan = self.plan(*unlink.args)
        self.assertIn("idx_judge_audit_game_log", plan)
        self.assertIn("idx_game_logs_room", plan)
        with patch.object(scheduler, "execute", new_callable=AsyncMock) as execute:
            await scheduler._unlink_player_refs(1)
        for prefix, index in (("UPDATE judge_audit_logs", "idx_judge_audit_player"),
                              ("UPDATE game_logs", "idx_game_logs_player_created")):
            call = next(c for c in execute.call_args_list if c.args[0].startswith(prefix))
            self.assertIn(index, self.plan(*call.args))
        plan = self.plan("DELETE FROM players WHERE id=?", (1,))
        self.assertIn("idx_judge_audit_player", plan)
        self.assertIn("idx_game_logs_player_created", plan)
        self.assertNotIn("SCAN judge_audit_logs", plan)

    async def test_actual_guest_recent_activity_query_uses_indexes(self):
        with patch.object(scheduler, "fetch_all", new_callable=AsyncMock, return_value=[]) as fetch:
            await scheduler.cleanup_guests()
        plan = self.plan(*fetch.call_args.args)
        self.assertIn("idx_game_logs_player_created", plan)
        self.assertIn("idx_game_logs_room", plan)
        self.assertNotIn("SCAN gl", plan)
        # The compound index supports ordered recent speech as well as its player prefix.
        plan = self.plan("SELECT created_at FROM game_logs WHERE player_id=? ORDER BY created_at DESC LIMIT 10", (1,))
        self.assertIn("idx_game_logs_player_created", plan)
        self.assertNotIn("USE TEMP B-TREE", plan)

    async def test_finished_cleanup_preserves_other_rooms_and_audit_report_payloads(self):
        pid = await self.player("registered")
        retained = ["recent", "playing", "waiting"]
        for rid, status, old, finished in (
            ("expired", "finished", True, True),
            ("legacy-expired", "finished", True, False),
            ("recent", "finished", False, True),
            ("playing", "playing", True, False),
            ("waiting", "waiting", True, False),
        ):
            await self.room(rid, pid, status=status, old=old, finished=finished)
            await self.references(rid, pid, await self.log(rid, pid, old=old))
        before = self.snapshot()
        await scheduler.cleanup_finished_rooms()
        after = self.snapshot()
        self.assertEqual({r["id"] for r in await database.fetch_all("SELECT id FROM rooms")}, set(retained))
        for table, null_fields in (("judge_audit_logs", ("room_id", "game_log_id")),
                                   ("reports", ("room_id", "log_id"))):
            with closing(self.connect()) as conn:
                columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            expected = []
            for row in before[table]:
                record = dict(zip(columns, row))
                if record["room_id"] not in retained:
                    record.update(dict.fromkeys(null_fields))
                expected.append(tuple(record[c] for c in columns))
            self.assertEqual(after[table], expected)
        for table in ("game_logs", "room_notes", "room_presence", "room_answer_reveals", "room_answer_reveal_prompts"):
            rows = await database.fetch_all(f"SELECT room_id FROM {table}")
            self.assertEqual({r["room_id"] for r in rows}, set(retained))
        self.assertEqual(len(after["room_hint_views"]), len(retained))
        self.assertEqual(after["players"], before["players"])
        self.assert_indexes_and_integrity()

    async def test_guest_cleanup_retains_all_active_cases_and_unlinks_old_guest(self):
        old = await self.player("expired-guest", guest=True)
        registered = await self.player("old-registered")
        keep = [registered]
        keep.append(await self.player("new-guest", guest=True, old=False))
        keep.append(await self.player("active-guest", guest=True, active=True))
        recent_speaker = await self.player("recent-speaker", guest=True)
        recent_owner = await self.player("recent-room-owner", guest=True)
        keep.extend([recent_speaker, recent_owner])
        await self.room("old-owned", old)
        await self.references("old-owned", old, await self.log("old-owned", old))
        await self.room("retained", registered, status="playing", old=False)
        retained_log = await self.log("retained", old)
        audit, report = await self.references("retained", old, retained_log)
        await self.log("retained", recent_speaker, old=False)
        await self.room("active-owned", recent_owner, status="playing", old=False)
        # Old room ownership with recent speech by someone else also protects a guest.
        owner = await self.player("active-room-owner", guest=True)
        keep.append(owner)
        await self.room("old-active-owned", owner, status="playing")
        await self.log("old-active-owned", registered, old=False)
        await database.execute("INSERT INTO player_daily_asks VALUES (?, '2026-01-01', 3)", (old,))
        await scheduler.cleanup_guests()
        self.assertEqual({r["id"] for r in await database.fetch_all("SELECT id FROM players")}, set(keep))
        self.assertIsNone(await database.fetch_one("SELECT id FROM rooms WHERE id='old-owned'"))
        self.assertIsNone((await database.fetch_one("SELECT player_id FROM game_logs WHERE id=?", (retained_log,)))["player_id"])
        row = await database.fetch_one("SELECT * FROM judge_audit_logs WHERE id=?", (audit,))
        self.assertEqual((row["room_id"], row["game_log_id"], row["player_id"], row["raw_response"]),
                         ("retained", retained_log, None, "fixture response"))
        row = await database.fetch_one("SELECT * FROM reports WHERE id=?", (report,))
        self.assertEqual((row["room_id"], row["log_id"], row["reporter_id"], row["target_player_id"], row["reason"]),
                         ("retained", retained_log, None, None, "fixture reason"))
        self.assertEqual((await database.fetch_one("SELECT COUNT(*) AS n FROM judge_audit_logs"))["n"], 2)
        self.assertEqual((await database.fetch_one("SELECT COUNT(*) AS n FROM reports"))["n"], 2)
        self.assertEqual(await database.fetch_all("SELECT * FROM player_daily_asks"), [])
        self.assert_indexes_and_integrity()
