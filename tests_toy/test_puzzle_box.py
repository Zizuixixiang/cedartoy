import json
import re
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import puzzle_box as box
from puzzle_box_data import PUZZLES, PROMPT, _ROWS
import server

ROOT = Path(__file__).resolve().parents[1]


class PuzzleBoxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "sessions.db"

    def play(self, action, pid=None, ai=1, **params):
        return box.play(self.db, ai, action, dict(puzzle_id=pid, **params))

    def test_catalog_complete_unique_and_no_spoilers(self):
        expected = [f"N{i:02}" for i in range(1, 17)] + [f"H{i:02}" for i in range(1, 7)]
        self.assertEqual(list(PUZZLES), expected)
        self.assertEqual(len(_ROWS), 22)
        self.assertEqual(len({p["title"] for p in PUZZLES.values()}), 22)
        self.assertEqual(len({str(p["answer"]) for p in PUZZLES.values()}), 22)
        self.assertEqual(sum(p["challenge"] for p in PUZZLES.values()), 6)
        for p in PUZZLES.values():
            self.assertTrue(p["answer"] and p["body"] and p["steps"])
        for p in box.catalog()["items"]:
            self.assertEqual(set(p), {"id", "title", "challenge", "status"})
        self.assertIn(r"\u504F", PUZZLES["N01"]["body"])
        self.assertIn(r"\u53EA", PUZZLES["N03"]["body"])

    def test_all_answers_and_normalization(self):
        for pid, p in PUZZLES.items():
            with self.subTest(pid=pid):
                self.assertTrue(box.validate(pid, p["answer"]))
                if pid == "N03":
                    continue
                a = p["answer"]
                variants = [a.lower(), f'答案是：“{a}”', f'Answer: "{a}"',
                            "  " + a.replace(" ", "   ").lower() + "!  ",
                            a.replace("'", "’"), a.replace("'", "ʼ"),
                            re.sub(r"[.,?!，。]", "", a)]
                if pid in {"N01", "N11"}:
                    variants += ["， ".join(a)]
                for v in variants:
                    self.assertTrue(box.validate(pid, v), (pid, v))
                for v in [a + " NOT", "WRONG", None, [], a * 30]:
                    self.assertFalse(box.validate(pid, v), (pid, v))
        self.assertFalse(box.validate("N07", "HIDDEN IS SPECIAL"))
        self.assertFalse(box.validate("N13", "I LEFT THIS ONE FOR ME."))

    def test_two_readings_are_required(self):
        a, b = PUZZLES["N03"]["answer"]
        for value in [a, b, [a, a], "只要你想我就会回来", [a, "只要你想他，就会回来。"], [a, "只要你想，我就回来。"],
                      ["不" + a, b], [a, "只要你想我，就会回来吧。"]]:
            self.assertFalse(box.validate("N03", value), value)
        for value in [[a, b], a + "\n" + b, f"1. {a}\n2. {b}", [a.replace("，", ","), b.replace("，", "；")]]:
            self.assertTrue(box.validate("N03", value), value)

    def test_open_submit_history_and_isolation(self):
        self.assertFalse(self.play("submit", "N01", answer=PUZZLES["N01"]["answer"])["ok"])
        self.assertEqual(self.play("open", 1)["status"], "opened")
        wrong = self.play("submit", "N01", answer="wrong")
        self.assertEqual(wrong, {"id": "N01", "correct": False, "status": "opened"})
        self.assertEqual(self.play("submit", "N01", answer=PUZZLES["N01"]["answer"])["status"], "solved")
        self.assertEqual(self.play("open", "N01")["status"], "solved")
        self.assertEqual(self.play("submit", "N01", answer="wrong")["status"], "solved")
        self.assertEqual(self.play("progress")["summary"], {"solved": 1, "opened": 0, "unseen": 21})
        self.assertEqual(self.play("progress", ai=2)["summary"], {"solved": 0, "opened": 0, "unseen": 22})

    def test_draw_excludes_seen_and_exhausts(self):
        self.play("open", "N01")
        self.play("open", "N02")
        self.play("submit", "N02", answer=PUZZLES["N02"]["answer"])
        draws = [self.play("draw") for _ in range(20)]
        self.assertEqual(len({d["id"] for d in draws}), 20)
        self.assertFalse({"N01", "N02"} & {d["id"] for d in draws})
        for _ in range(2):
            self.assertEqual(self.play("draw"), {"ok": False, "message": "都已拆过，可回看待解题"})

    def test_concurrent_draw_claims_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.play("draw"), range(25)))
        self.assertEqual(len({r["id"] for r in results if "id" in r}), 22)
        self.assertEqual(sum("id" in r for r in results), 22)

    def test_checkpoints_do_not_solve_or_leak(self):
        for pid, p in PUZZLES.items():
            if not p["checkpoints"]:
                continue
            self.play("open", pid)
            for checkpoint, expected in p["checkpoints"].items():
                self.assertEqual(self.play("check_step", pid, checkpoint_id=checkpoint, answer=expected), {"correct": True})
                self.assertEqual(self.play("check_step", pid, checkpoint_id=checkpoint, answer="wrong"), {"correct": False})
            self.assertEqual(self.play("open", pid)["status"], "opened")
            self.play("submit", pid, answer=p["answer"])
            self.play("check_step", pid, checkpoint_id=next(iter(p["checkpoints"])), answer="wrong")
            self.assertEqual(self.play("open", pid)["status"], "solved")
        for value in [None, [], "unknown"]:
            with self.assertRaises(ValueError):
                self.play("check_step", "H01", checkpoint_id=value, answer="wrong")

    def test_spoilers_need_confirmation_and_never_change_progress(self):
        for pid, state in [("N01", "unseen"), ("N02", "opened"), ("N03", "solved")]:
            if state != "unseen":
                self.play("open", pid)
            if state == "solved":
                self.play("submit", pid, answer=PUZZLES[pid]["answer"])
            before = self.play("progress")
            reply = box.reveal(self.db, 1, pid)
            self.assertEqual("answer" in reply, state == "solved")
            self.assertEqual(box.reveal(self.db, 1, pid, True)["answer"], PUZZLES[pid]["answer"])
            self.assertEqual(before, self.play("progress"))

    def test_additive_repeat_init_preserves_data(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE existing_data (value TEXT)")
            conn.execute("INSERT INTO existing_data VALUES ('keep me')")
        self.play("open", "H06")
        self.play("submit", "H06", answer=PUZZLES["H06"]["answer"])
        with sqlite3.connect(self.db) as conn:
            before = conn.execute("SELECT * FROM puzzle_box_progress").fetchall()
        box.init_db(self.db)
        box.init_db(self.db)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT * FROM puzzle_box_progress").fetchall(), before)
            self.assertEqual(conn.execute("SELECT * FROM existing_data").fetchall(), [("keep me",)])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_public_save_count_counts_distinct_ai_and_empty_is_zero(self):
        with patch.object(server, "SESSIONS_DB_PATH", self.db), \
             patch.object(server, "VENDOR_SAVE_ROOT", Path(self.tmp.name) / "vendor"), \
             patch.object(server, "CAMPING_PLAZA_DB_PATH", Path(self.tmp.name) / "camping.db"), \
             patch.object(server, "count_saved_tarot_sessions", return_value=0):
            def check_count(expected):
                self.assertEqual(server._public_game_stats()["puzzle_box"],
                                 {"metric_label": "存档数", "metric": expected})

            check_count(0)
            self.assertFalse(self.db.exists())  # Public stats must not initialize storage.
            with sqlite3.connect(self.db) as conn:
                conn.execute("CREATE TABLE unrelated (value TEXT)")
            check_count(0)
            with sqlite3.connect(self.db) as conn:
                self.assertIsNone(conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='puzzle_box_progress'"
                ).fetchone())
            box.init_db(self.db)
            check_count(0)
            self.play("open", "N01", ai=1)
            self.play("open", "N02", ai=1)
            self.play("submit", "N01", ai=1, answer=PUZZLES["N01"]["answer"])
            check_count(1)
            self.play("open", "N01", ai=2)
            check_count(2)
            self.play("open", "H06", ai=2)
            check_count(2)

    def test_single_puzzle_response_and_safe_guide(self):
        for pid in PUZZLES:
            result = self.play("open", pid)
            self.assertEqual(result["body"], PUZZLES[pid]["body"])
            self.assertEqual(result["prompt"], PROMPT)
            self.assertFalse({"answer", "steps", "items"} & result.keys())
            self.assertLess(len(json.dumps(result, ensure_ascii=False)), 1800)
        guide = json.loads(server._tool_get_guide({"game": "puzzle_box"}))["guide"]
        self.assertIn(
            "这类题不一定要自己一口气解完，也很适合拉上人类一起拆。解到一半时，可以把当前发现、猜测或卡住的地方说出来，一起猜接下来会是什么；如果已经解出了谜底，也可以先问问人类想不想自己猜一下，再决定什么时候揭晓。怎么玩都可以，重点是一起玩得开心。",
            guide,
        )
        schema = json.dumps(server._root_tools(), ensure_ascii=False)
        for pid, p in PUZZLES.items():
            self.assertNotIn(p["body"], guide + schema)
            for answer in p["answer"] if isinstance(p["answer"], list) else [p["answer"]]:
                self.assertNotIn(answer, guide + schema)


class PuzzleBoxBoundaryTests(unittest.TestCase):
    play = PuzzleBoxTests.play

    def setUp(self):
        PuzzleBoxTests.setUp(self)
        self.accounts = Path(self.tmp.name) / "accounts.db"
        with sqlite3.connect(self.accounts) as conn:
            conn.executescript('''
                CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT, is_ai INTEGER,
                    is_admin INTEGER, created_at TEXT, last_active_at TEXT, deleted_at TEXT);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users VALUES (1,'machine',1,0,'','',NULL),(2,'other',1,0,'','',NULL),
                    (3,'second',1,0,'','',NULL),(10,'human',0,0,'','',NULL);
                INSERT INTO user_bindings VALUES (10,1),(10,3);
            ''')
        def account(token):
            users = {"ai-token": {"id": 1, "is_ai": True, "username": "machine"},
                     "human-token": {"id": 10, "is_ai": False, "username": "human"}}
            if token not in users:
                raise server._McpError(-32001, "unauthorized")
            return users[token]
        for p in [patch.object(server, "SESSIONS_DB_PATH", self.db),
                  patch.object(server, "TURTLE_DB_PATH", self.accounts),
                  patch.object(server, "_current_account", side_effect=account),
                  patch.object(server, "_anti_addiction_context", return_value=None),
                  patch.object(server, "_play_announcements", return_value=""),
                  patch.object(server, "_mcp_forced_announcement", return_value=""),
                  patch.object(server, "_duel_unread_request_reminder", return_value="")]:
            p.start()
            self.addCleanup(p.stop)

    def test_mcp_path_and_bearer_use_authenticated_machine_ignore_forgery(self):
        for channel in ("path_token", "bearer_token"):
            result = server._handle_root_mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "play", "arguments": {"game": "puzzle_box", "action": "open", "params": {
                    "puzzle_id": "H06", "player_id": "2", "ai_user_id": 2, "slot": 5}}}}, **{channel: "ai-token"})
            self.assertFalse(result["result"]["isError"], result)
            reply = json.loads(result["result"]["content"][0]["text"])
            self.assertEqual(reply["id"], "H06")
            self.assertNotIn("slot", reply)
        self.assertEqual(self.play("progress")["summary"]["opened"], 1)
        self.assertEqual(self.play("progress", ai=2)["summary"]["opened"], 0)
        for token in (None, "human-token", "bad-token"):
            with self.assertRaises(server._McpError):
                server._tool_play({"game": "puzzle_box", "action": "draw"}, path_token=token)

    def request(self, *, token="human-token", ai=1, pid=None, confirm=False):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Authorization": "Bearer " + token}
        handler._send_json = Mock()
        if pid:
            handler.path = "/api/puzzle-box/reveal"
            handler._read_json_body = Mock(return_value={"ai_user_id": ai, "puzzle_id": pid, "confirm_spoiler": confirm})
            handler.do_POST()
        else:
            handler._handle_puzzle_box(params={} if ai is None else {"ai_user_id": [str(ai)]})
        args, kw = handler._send_json.call_args
        self.assertIn("no-store", kw["extra_headers"]["Cache-Control"])
        return args[0], kw.get("status", 200)

    def test_http_real_binding_gate_and_per_puzzle_reveal(self):
        for token, ai in [("", 1), ("ai-token", 1), ("human-token", 2)]:
            for pid in (None, "N01"):
                payload, status = self.request(token=token, ai=ai, pid=pid, confirm=True)
                self.assertIn(status, (401, 403))
                self.assertNotIn("answer", payload)
        self.assertIsNone(self.request(token="", ai=None)[0]["summary"])
        self.play("open", "N01")
        self.assertEqual(self.request(ai=1)[0]["summary"]["opened"], 1)
        self.assertEqual(self.request(ai=3)[0]["summary"]["opened"], 0)
        self.assertTrue(self.request(pid="N01")[0]["confirmation_required"])
        self.assertTrue(self.request(pid="N01", confirm="true")[0]["confirmation_required"])
        revealed, status = self.request(pid="N01", confirm=True)
        self.assertEqual(status, 200)
        self.assertEqual(revealed["answer"], PUZZLES["N01"]["answer"])
        self.assertNotIn("items", revealed)
        self.assertEqual(self.play("progress")["summary"]["solved"], 0)
        self.play("submit", "N01", answer=PUZZLES["N01"]["answer"])
        self.assertIn("answer", self.request(pid="N01")[0])


class PuzzleBoxFrontendTests(unittest.TestCase):
    def test_homepage_interactions(self):
        result = subprocess.run(["node", "tests_toy/puzzle_box_frontend.cjs"], cwd=ROOT,
                                input=json.dumps(box.catalog()), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
