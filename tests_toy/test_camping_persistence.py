"""Isolated HTTP/SQLite regressions; each operation imports a fresh adapter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import json, os, sqlite3, sys, time
from pathlib import Path
import camping_plaza_adapter as a
from fastapi.testclient import TestClient

job = json.loads(sys.argv[1])
player = job.get("player", "guest:auditcamp")
sid = a.session_id_for_player(player)
mode = job["mode"]
if mode == "seed":
    with a._connect() as conn:
        conn.execute("DELETE FROM runtime_snapshot WHERE session_id=?", (sid,))
    eng = a.CampingPlazaEngine(db_path=a.upstream.DB_PATH, session_id=sid, create_new=True)
    eng.state.player_name = "Audit"
    for key, value in job.get("state", {}).items():
        setattr(eng.state, key, value)
    eng.save_state()
    print(json.dumps({"seeded": True}))
elif mode == "admin":
    from fastapi import Request
    request = Request({"type": "http", "client": ("127.0.0.1", 1234)})
    print(json.dumps(a.manage_save(job["action"], a.SaveAdminRequest(**job["payload"]), request)))
elif mode == "read":
    with sqlite3.connect(a.upstream.DB_PATH) as conn:
        row = conn.execute("SELECT snapshot_json FROM runtime_snapshot WHERE session_id=?", (sid,)).fetchone()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(json.dumps({"raw": row[0] if row else None, "integrity": integrity}))
else:
    injected = []
    fault = job.get("fault")
    class Proxy:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def execute(self, sql, *args):
            if fault == "save" and "INSERT INTO runtime_snapshot" in sql:
                injected.append("save")
                raise sqlite3.OperationalError("injected snapshot write failure")
            return self.connection.execute(sql, *args)
        def commit(self):
            if fault == "commit":
                injected.append("commit")
                raise sqlite3.OperationalError("injected commit failure")
            return self.connection.commit()
    if fault == "readonly":
        def readonly():
            injected.append("readonly")
            return sqlite3.connect("file:" + a.upstream.DB_PATH + "?mode=ro", uri=True)
        a._persistence.connect = readonly
    elif fault == "commit":
        a._persistence.connect = lambda: Proxy(a._connect())
    elif fault == "save":
        original_connect = a._engine_module.sqlite3.connect
        a._engine_module.sqlite3.connect = lambda *args, **kwargs: Proxy(original_connect(*args, **kwargs))

    if mode == "concurrent":
        # Exercise the same registered endpoint wrapper without the unrelated
        # identity-metadata write serializing requests before the game load.
        a._PLAYER_ID.set(player)
        original_get = a._ORIGINAL_GET_ENGINE
        loaded_turns = []
        def observed_get(*args, **kwargs):
            eng = original_get(*args, **kwargs)
            loaded_turns.append(eng.state.turn)
            if job.get("loaded"):
                Path(job["loaded"]).write_text(str(eng.state.turn))
                deadline = time.monotonic() + 10
                while not Path(job["wait_for"]).exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("second writer did not start")
                    time.sleep(0.01)
                time.sleep(0.3)
            return eng
        a._ORIGINAL_GET_ENGINE = observed_get
        if job.get("started"):
            Path(job["started"]).touch()
        result = a.upstream.submit_turn_plan(a.upstream.TurnPlanRequest())
        result["loaded_turns"] = loaded_turns
        print(json.dumps(result, ensure_ascii=False))
    else:
        with TestClient(a.app) as client:
            if mode == "mcp":
                from cedar_backend.game_dispatch import _play_camping_plaza
                from types import SimpleNamespace
                import httpx
                class McpError(Exception):
                    pass
                try:
                    result = _play_camping_plaza(
                        {"action": "advance_turn", "player_id": player},
                        CAMPING_PLAZA_BASE="http://testserver", _McpError=McpError,
                        _camping_plaza_save_admin=None, _parse_json_import_save_data=None,
                        httpx=SimpleNamespace(request=client.request, HTTPError=httpx.HTTPError), json=json)
                except McpError as exc:
                    result = {"mcp_error": list(exc.args), "injected": injected}
            else:
                response = client.request(job.get("method", "POST"), job["path"],
                                          json=job.get("body", {}), headers={"X-Player-Id": player})
                result = {"status": response.status_code, "body": response.json(), "injected": injected}
        print(json.dumps(result, ensure_ascii=False))
'''


class CampingPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cedar-camping-fixes-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ, CAMPING_PLAZA_DB_PATH=str(self.root / "camp.db"),
                        DATABASE_URL="", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))

    def start(self, **job):
        return subprocess.Popen([sys.executable, "-B", "-c", CHILD, json.dumps(job)],
                                cwd=self.root, env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def finish(self, process):
        try:
            out, err = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("isolated Camping request timed out")
        self.assertEqual(process.returncode, 0, err)
        return json.loads(out.strip().splitlines()[-1])

    def run_child(self, **job):
        return self.finish(self.start(**job))

    def read(self, **kwargs):
        result = self.run_child(mode="read", **kwargs)
        self.assertEqual(result["integrity"], "ok")
        return result["raw"]

    def test_migration_receipt_survives_play_but_not_import_or_recreation(self):
        def admin(action, **payload):
            return self.run_child(mode="admin", action=action, payload=payload)

        self.run_child(mode="seed", state={"turn": 1})
        admin("migrate", source_player_id="guest:auditcamp", target_player_id="81", migration_receipt="fixture-proof")
        self.assertEqual(admin("summary", player_id="81")["summary"]["migration_receipt"], "fixture-proof")
        result = self.run_child(mode="concurrent", player="81")
        self.assertEqual(result["loaded_turns"], [1])
        self.assertEqual(admin("summary", player_id="81")["summary"]["migration_receipt"], "fixture-proof")
        exported = admin("export", player_id="81")["save_data"]
        admin("import", player_id="81", save_data=exported, confirm=True)
        self.assertNotIn("migration_receipt", admin("summary", player_id="81")["summary"])
        admin("migrate", source_player_id="81", target_player_id="82", migration_receipt="second-proof")
        admin("delete", player_id="82")
        self.run_child(mode="seed", player="82")
        self.assertNotIn("migration_receipt", admin("summary", player_id="82")["summary"])

    def test_write_and_commit_failures_never_report_success_or_change_snapshot(self):
        # Baselines prove each fixture reaches a valid write, rather than a
        # validation/confirmation rejection hiding an untested failure path.
        cases = [
            ("/api/action", {"action": "restart_game", "params": {"confirm": "确认重新开始"}}, {"day": 3}, "day", 1),
            ("/api/action", {"action": "advance_turn"}, {"turn": 1}, "turn", 2),
            ("/api/turn/advance", {}, {"turn": 1}, "turn", 2),
            ("/api/turn/plan", {"free_actions": [], "actions": []}, {"turn": 2}, "turn", 3),
            ("/api/day/end", {"day_end_actions": []}, {"turn": 6, "day_end_completed": False}, "day_end_completed", True),
            ("/api/day/start", {}, {"turn": 6, "day_end_completed": True}, "day", 2),
            ("/api/player/name", {"name": "Camper"}, {"player_name": None}, "player_name", "Camper"),
        ]
        for path, body, state, field, expected in cases:
            with self.subTest(path=path, body=body, fault="none"):
                self.run_child(mode="seed", state=state)
                result = self.run_child(mode="http", path=path, body=body)
                self.assertEqual(result["status"], 200, result)
                self.assertIsNot(result["body"].get("success"), False, result)
                self.assertEqual(json.loads(self.read())["state"][field], expected)
            for fault in ("save", "commit", "readonly"):
                with self.subTest(path=path, body=body, fault=fault):
                    self.run_child(mode="seed", state=state)
                    before = self.read()
                    result = self.run_child(mode="http", path=path, body=body, fault=fault)
                    self.assertEqual(result["status"], 503, result)
                    self.assertIsNot(result["body"].get("success"), True, result)
                    self.assertIn(fault, result["injected"])
                    self.assertEqual(before, self.read())

    def test_first_session_and_first_read_failures_are_not_successful(self):
        self.run_child(mode="seed")  # schema and a separate player's saved game
        before = self.read()
        for index, (path, method) in enumerate((("/api/session", "POST"), ("/mcp/state", "GET"))):
            for fault in ("save", "commit", "readonly"):
                with self.subTest(path=path, fault=fault):
                    player = f"guest:newcamp{index}{fault}"
                    result = self.run_child(mode="http", path=path, method=method, player=player, fault=fault)
                    self.assertEqual(result["status"], 503, result)
                    self.assertIn(fault, result["injected"])
                    self.assertIsNone(self.read(player=player))
        self.assertEqual(before, self.read())

    def test_concurrent_processes_load_latest_committed_turn(self):
        self.run_child(mode="seed", state={"turn": 2})
        marker = self.root / "first-loaded"
        second_marker = self.root / "second-started"
        first = self.start(mode="concurrent", loaded=str(marker), wait_for=str(second_marker))
        self.addCleanup(lambda: first.poll() is None and first.kill())
        deadline = time.monotonic() + 10
        while not marker.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), "2")
        second = self.start(mode="concurrent", started=str(second_marker))
        self.addCleanup(lambda: second.poll() is None and second.kill())
        one = self.finish(first)
        two = self.finish(second)
        self.assertTrue(one["success"], one)
        self.assertTrue(two["success"], two)
        self.assertEqual(one["loaded_turns"], [2])
        self.assertEqual(two["loaded_turns"], [3])
        self.assertEqual(json.loads(self.read())["state"]["turn"], 4)

    def test_commit_failure_reaches_root_mcp_as_error(self):
        self.run_child(mode="seed", state={"turn": 1})
        before = self.read()
        result = self.run_child(mode="mcp", fault="commit")
        self.assertEqual(result["mcp_error"][0], -32603, result)
        self.assertIn("commit", result["injected"])
        self.assertEqual(before, self.read())


if __name__ == "__main__":
    unittest.main()
