"""Cross-process claim interruptions use only task-owned /tmp databases/saves."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
GUEST = "guest:claimrecovery"


def claim(root, user=81, slot=1, fault=""):
    # Import after the child has isolated DB environment variables.
    import server

    class SessionsConnection(sqlite3.Connection):
        def commit(self):
            super().commit()
            if fault == "database":
                os._exit(71)  # committed rows, but no consumed claim code

    class AccountsConnection(sqlite3.Connection):
        def commit(self):
            super().commit()
            if fault == "reservation":
                os._exit(71)
            if fault == "pause" and not (root / "claim-release").exists():
                (root / "claim-pinned").touch()
                deadline = time.monotonic() + 15
                while not (root / "claim-release").exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("claim release timed out")
                    time.sleep(.01)

    def accounts():
        conn = sqlite3.connect(root / "accounts.db", timeout=15, factory=AccountsConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def sessions():
        conn = sqlite3.connect(root / "sessions.db", timeout=15, factory=SessionsConnection)
        conn.row_factory = sqlite3.Row
        return conn

    rename = Path.rename

    def interrupted_rename(source, target):
        if fault in {"rollback", "rollback-fails", "before-move"} and source.name == GUEST and (
                source.parent.name == "leek" or fault == "before-move"):
            raise OSError("injected move failure")
        if fault == "rollback-fails" and source.name == str(user):
            raise OSError("injected rollback failure")
        result = rename(source, target)
        if fault == "directory" and source.name == GUEST:
            os._exit(71)  # directory moved, sessions transaction uncommitted
        return result

    def camping_admin(action, **payload):
        if not (root / "camping-enabled").exists():
            return {}
        import camping_plaza_adapter as adapter
        from fastapi import Request, HTTPException
        payload.pop("timeout", None)
        request = Request({"type": "http", "client": ("127.0.0.1", 1234)})
        try:
            result = adapter.manage_save(action, adapter.SaveAdminRequest(**payload), request)
        except HTTPException as exc:
            raise server._McpError(-32004 if exc.status_code == 404 else -32602, str(exc.detail))
        if action == "migrate" and fault == "camping":
            os._exit(71)
        return result

    def camping_summary(player):
        if fault == "http-wait":
            (root / "http-waiting").touch()
            deadline = time.monotonic() + 15
            while not (root / "http-release").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("HTTP release timed out")
                time.sleep(.01)
        try:
            return camping_admin("summary", player_id=player).get("summary")
        except server._McpError as exc:
            if exc.code == -32004:
                return None
            raise

    (root / "claim-started").touch()
    with (
        patch.object(server, "VENDOR_SAVE_ROOT", root / "saves"),
        patch.object(server, "SESSIONS_DB_PATH", root / "sessions.db"),
        patch.object(server, "DIRECTORY_VENDOR_GAMES", ("arcade", "leek", "detroit")),
        patch.object(server, "_camping_plaza_save_summary", side_effect=camping_summary),
        patch.object(server, "_camping_plaza_save_admin", side_effect=camping_admin),
        patch.object(server, "_db_connect", side_effect=accounts),
        patch.object(server, "_sessions_db_connect", side_effect=sessions),
        patch.object(server, "_current_account", return_value={"id": user}),
        patch.object(server, "_public_user", side_effect=lambda value: value),
        patch.object(Path, "rename", new=interrupted_rename),
    ):
        return server._claim_guest_saves("isolated", "claim-code", slot=slot)


def guest_play(root, route):
    import server
    server.VENDOR_SAVE_ROOT = root / "saves"
    server.TURTLE_DB_PATH = root / "accounts.db"
    (root / (route + "-started")).touch()

    def mutate(*args, **kwargs):
        (root / (route + "-unexpected-write")).touch()
        return {}

    if route == "mcp":
        with patch.object(server, "_tool_play_inner_unlocked", side_effect=mutate):
            return server._tool_play_inner({"game": "arcade", "player_id": GUEST})
    with patch.object(server, "_human_test_action_unlocked", side_effect=mutate):
        return server._human_test_action("mbti", "start", "", {"player_id": GUEST})


class ClaimRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cedar-claim-recovery-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with sqlite3.connect(self.root / "accounts.db") as conn:
            from cedar_backend.save_management import _init_guest_claim_table
            _init_guest_claim_table(conn)
            conn.execute("INSERT INTO guest_claim_codes(code, guest_player_id) VALUES (?, ?)",
                         ("claim-code", GUEST))
        with sqlite3.connect(self.root / "sessions.db") as conn:
            conn.execute("CREATE TABLE eco_sessions(player_id TEXT PRIMARY KEY, user_id INTEGER, last_active TEXT, state TEXT)")
            conn.execute("INSERT INTO eco_sessions VALUES (?, NULL, 'now', 'original eco')", (GUEST,))
        for game in ("arcade", "leek"):
            directory = self.root / "saves" / game / GUEST
            directory.mkdir(parents=True)
            (directory / "save.json").write_text(json.dumps({"game": game, "value": 23}))

    def spawn(self, user=81, slot=1, fault="", route="claim"):
        code = """
import json, sys
from pathlib import Path
from tests_toy.test_claim_recovery import claim, guest_play
try:
    if sys.argv[5] == "claim":
        result = claim(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    else:
        result = guest_play(Path(sys.argv[1]), sys.argv[5])
    print(json.dumps(result))
except Exception as exc:
    print(str(exc))
    sys.exit(2)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(self.root), str(user), str(slot), fault, route],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                 "TURTLE_SOUP_DB": str(self.root / "accounts.db"),
                 "SESSIONS_DB": str(self.root / "sessions.db"),
                 "CAMPING_PLAZA_DB_PATH": str(self.root / "camp.db"), "DATABASE_URL": ""},
        )
        self.addCleanup(lambda: (proc.kill(), proc.wait()) if proc.poll() is None else None)
        return proc

    def run_claim(self, **kwargs):
        proc = self.spawn(**kwargs)
        output, error = proc.communicate(timeout=25)
        self.assertFalse(error, error)
        return proc.returncode, output

    def assert_owned(self, owner):
        for game in ("arcade", "leek"):
            self.assertFalse((self.root / "saves" / game / GUEST).exists())
            self.assertEqual(json.loads((self.root / "saves" / game / str(owner) / "save.json").read_text())["value"], 23)
        with sqlite3.connect(self.root / "sessions.db") as conn:
            self.assertEqual(conn.execute("SELECT player_id, user_id, state FROM eco_sessions").fetchall(),
                             [(str(owner), owner, "original eco")])
        with sqlite3.connect(self.root / "accounts.db") as conn:
            self.assertEqual(conn.execute("SELECT claimed_by, claimed_slot FROM guest_claim_codes").fetchone(), (owner, 1))

    def wait_file(self, name):
        deadline = time.monotonic() + 10
        while not (self.root / name).exists():
            if time.monotonic() > deadline:
                self.fail("timed out waiting for " + name)
            time.sleep(.01)

    def assert_other_player_can_write(self):
        with sqlite3.connect(self.root / "sessions.db", timeout=.5) as conn:
            conn.execute("INSERT OR REPLACE INTO eco_sessions VALUES ('99', 99, 'now', 'other player')")
            conn.execute("DELETE FROM eco_sessions WHERE player_id='99'")

    def test_unrelated_detroit_operation_does_not_block_claim_or_other_writes(self):
        from save_locks import save_directory_locks
        with save_directory_locks(self.root / "saves" / "detroit" / "81"):
            self.assert_other_player_can_write()
            process = self.spawn()
            output, error = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, (output, error))
            self.assert_other_player_can_write()
        self.assert_owned(81)

    def test_waiting_for_participating_directory_does_not_lock_sessions(self):
        from save_locks import save_directory_locks
        with save_directory_locks(self.root / "saves" / "arcade" / "81"):
            process = self.spawn()
            self.wait_file("claim-started")
            time.sleep(.3)
            self.assertIsNone(process.poll())
            self.assert_other_player_can_write()
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, (output, error))
        self.assert_owned(81)

    def test_waiting_for_upstream_does_not_lock_sessions(self):
        process = self.spawn(fault="http-wait")
        self.wait_file("http-waiting")
        self.assert_other_player_can_write()
        (self.root / "http-release").touch()
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, (output, error))

    def test_target_created_during_external_phase_is_rechecked_in_transaction(self):
        with sqlite3.connect(self.root / "sessions.db") as conn:
            conn.execute("CREATE TABLE test_results(player_id TEXT, game TEXT)")
            conn.execute("INSERT INTO test_results VALUES (?, 'mbti')", (GUEST,))
        process = self.spawn(fault="pause")
        self.wait_file("claim-pinned")
        with sqlite3.connect(self.root / "sessions.db", timeout=.5) as conn:
            conn.execute("INSERT INTO test_results VALUES ('81', 'mbti')")
        (self.root / "claim-release").touch()
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 2, (output, error))
        with sqlite3.connect(self.root / "sessions.db") as conn:
            self.assertEqual(conn.execute("SELECT player_id FROM test_results ORDER BY player_id").fetchall(),
                             [('81',), (GUEST,)])
        self.assertEqual(self.run_claim(user=82)[0], 0)
        self.assert_owned(82)

    def test_proven_rollback_releases_guest_and_allows_retry(self):
        for fault in ("before-move", "rollback"):
            with self.subTest(fault=fault):
                self.assertEqual(self.run_claim(fault=fault)[0], 2)
                with sqlite3.connect(self.root / "accounts.db") as conn:
                    self.assertEqual(conn.execute(
                        "SELECT claimed_by, pending_by, pending_slot, recovery_json FROM guest_claim_codes"
                    ).fetchone(), (None, None, None, None))
                for game in ("arcade", "leek"):
                    self.assertTrue((self.root / "saves" / game / GUEST / "save.json").exists())
                process = self.spawn(route="web")
                process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0)
        self.assertEqual(self.run_claim(user=82)[0], 0)
        self.assert_owned(82)

    def test_uncertain_rollback_stays_pinned_and_same_owner_can_recover(self):
        process = self.spawn(fault="rollback-fails")
        process.communicate(timeout=15)  # Expected rollback error is logged.
        self.assertEqual(process.returncode, 2)
        with sqlite3.connect(self.root / "accounts.db") as conn:
            self.assertEqual(conn.execute("SELECT pending_by, pending_slot FROM guest_claim_codes").fetchone(), (81, 1))
        self.assertEqual(self.run_claim(user=82)[0], 2)
        self.assertEqual(self.run_claim()[0], 0)
        self.assert_owned(81)

    def camping(self, action):
        code = """
import camping_plaza_adapter as a
from fastapi import Request
import sys
request = Request({'type':'http', 'client':('127.0.0.1',1234)})
if sys.argv[1] == 'replace':
    a.manage_save('delete', a.SaveAdminRequest(player_id='81'), request)
player = '81' if sys.argv[1] == 'replace' else 'guest:claimrecovery'
a._PLAYER_ID.set(player)
a.upstream.mcp_state()
"""
        result = subprocess.run([sys.executable, "-c", code, action], cwd=ROOT,
            env={**os.environ, "CAMPING_PLAZA_DB_PATH": str(self.root / "camp.db"),
                 "DATABASE_URL": "", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.root / "camping-enabled").touch()

    def test_camping_receipt_recovers_after_lost_response(self):
        self.camping("seed")
        self.assertEqual(self.run_claim(fault="camping")[0], 71)
        self.assertEqual(self.run_claim(user=82)[0], 2)
        self.assertEqual(self.run_claim()[0], 0)
        self.assert_owned(81)

    def test_camping_replacement_is_not_accepted_as_migrated_save(self):
        self.camping("seed")
        self.assertEqual(self.run_claim(fault="camping")[0], 71)
        self.camping("replace")
        before = (self.root / "camp.db").read_bytes()
        self.assertEqual(self.run_claim()[0], 2)
        self.assertEqual(self.run_claim(user=82)[0], 2)
        self.assertEqual((self.root / "camp.db").read_bytes(), before)

    def test_reservation_survives_kill_before_any_move(self):
        self.assertEqual(self.run_claim(fault="reservation")[0], 71)
        for game in ("arcade", "leek"):
            self.assertTrue((self.root / "saves" / game / GUEST).exists())
        self.assertEqual(self.run_claim(user=82)[0], 2)
        self.assertEqual(self.run_claim()[0], 0)
        self.assert_owned(81)

    def test_waiting_guest_mcp_and_web_cannot_revive_claimed_source(self):
        claimant = self.spawn(fault="pause")
        self.wait_file("claim-pinned")
        guests = [(route, self.spawn(route=route)) for route in ("mcp", "web")]
        for route, proc in guests:
            self.wait_file(route + "-started")
            self.assertIsNone(proc.poll())
        (self.root / "claim-release").touch()
        output, error = claimant.communicate(timeout=25)
        self.assertEqual(claimant.returncode, 0, (output, error))
        for route, proc in guests:
            output, error = proc.communicate(timeout=25)
            self.assertEqual(proc.returncode, 2, (output, error))
            self.assertFalse((self.root / (route + "-unexpected-write")).exists())
        self.assert_owned(81)

    def test_kill_after_file_move_retries_only_pinned_target(self):
        self.assertEqual(self.run_claim(fault="directory")[0], 71)
        self.assertEqual(self.run_claim(user=82)[0], 2)
        self.assertEqual(self.run_claim(slot=2)[0], 2)
        self.assertEqual(self.run_claim()[0], 0)
        self.assert_owned(81)

    def test_kill_after_sqlite_commit_replays_same_result(self):
        self.assertEqual(self.run_claim(fault="database")[0], 71)
        self.assertEqual(self.run_claim(user=82)[0], 2)
        first = self.run_claim()
        second = self.run_claim()
        self.assertEqual(first[0], 0)
        self.assertEqual(json.loads(first[1]), json.loads(second[1]))
        self.assert_owned(81)

    def test_concurrent_accounts_cannot_split_the_claim(self):
        children = [(owner, self.spawn(user=owner)) for owner in (81, 82)]
        results = []
        for owner, child in children:
            output, error = child.communicate(timeout=25)
            self.assertFalse(error, error)
            results.append((owner, child.returncode, output))
        self.assertEqual(sorted(result[1] for result in results), [0, 2], results)
        self.assert_owned(next(owner for owner, status, _ in results if status == 0))

    def test_existing_save_is_not_overwritten_and_preflight_does_not_pin(self):
        destination = self.root / "saves" / "leek" / "81"
        destination.mkdir()
        (destination / "save.json").write_text('{"value":99}')
        self.assertEqual(self.run_claim()[0], 2)
        self.assertEqual(json.loads((destination / "save.json").read_text()), {"value": 99})
        with sqlite3.connect(self.root / "accounts.db") as conn:
            self.assertEqual(conn.execute("SELECT claimed_by, pending_by FROM guest_claim_codes").fetchone(), (None, None))
        self.assertEqual(self.run_claim(user=82)[0], 0)
        self.assert_owned(82)

    def test_recovery_refuses_replaced_target_directory(self):
        self.assertEqual(self.run_claim(fault="directory")[0], 71)
        destination = self.root / "saves" / "arcade" / "81"
        destination.rename(destination.with_name("preserved-original"))
        destination.mkdir()
        (destination / "save.json").write_text('{"value":99}')
        self.assertEqual(self.run_claim()[0], 2)
        self.assertEqual(json.loads((destination / "save.json").read_text()), {"value": 99})
        self.assertEqual(self.run_claim(user=82)[0], 2)


if __name__ == "__main__":
    unittest.main()
