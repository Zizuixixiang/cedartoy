"""Web submissions must never cross runs, even across process death/restarts."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


WORKER = r'''
import json, pathlib, sys, time
from unittest.mock import patch
import server
db, operation, raw = sys.argv[1:]
body = json.loads(raw)
server.SESSIONS_DB_PATH = pathlib.Path(db)
server.VENDOR_SAVE_ROOT = pathlib.Path(db).parent / "vendor_saves"
server.TURTLE_DB_PATH = pathlib.Path(db).parent / "accounts.db"
server.game_activity.record = lambda *args, **kwargs: None
for config in server.HUMAN_TEST_GAMES.values():
    if not config.get("research"):
        config["handler"].DB_PATH = db
game = body.pop("game", "mbti")
handler = server.HUMAN_TEST_GAMES[game]["handler"]
try:
    if operation == "pause_finish":
        engine = getattr(handler, "_engine", handler)
        original = engine._finish_test
        def finish(*args):
            result = original(*args)
            pathlib.Path(db + ".ready").touch()
            deadline = time.monotonic() + 15
            while not pathlib.Path(db + ".release").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("test release timed out")
                time.sleep(.01)
            return result
        engine._finish_test = finish
        operation = "answer_batch"
    if operation == "mcp_batch":
        result = getattr(handler, game + "_answer_batch")(body)
    elif operation == "mcp_start":
        # Identical timestamps must still denote distinct generations.
        with patch("time.time", return_value=2000000000):
            result = getattr(handler, game + "_start")(body)
    else:
        result = server._human_test_action(game, operation, "", body)
    print(json.dumps({"result": result}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"error": str(exc), "code": getattr(exc, "code", None)}))
'''


class HumanTestAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="human-test-atomic-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "sessions.db")
        self.player = "guest:webatomic"
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    def spawn(self, operation, body):
        env = {**os.environ, "SESSIONS_DB": self.db,
               "TURTLE_SOUP_DB": str(Path(self.temp.name) / "accounts.db")}
        child = subprocess.Popen(
            [sys.executable, "-c", WORKER, self.db, operation, json.dumps(body)],
            cwd=Path(__file__).resolve().parents[1], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.children.append(child)
        return child

    def collect(self, child):
        stdout, stderr = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, stderr)
        return json.loads(stdout)

    def run_action(self, operation, **body):
        return self.collect(self.spawn(operation, {"player_id": self.player, **body}))

    def start(self, **extra):
        result = self.run_action("start", edition="complete", **extra)
        self.assertNotIn("error", result)
        return result["result"]

    def submission(self, state, answer=3):
        return {"player_id": self.player, "game": state["game"],
                "session_id": state["session_id"], "expected_index": state["expected_index"],
                "answers": [answer] * state["total"]}

    def row(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT current_question, answers, web_generation FROM test_sessions "
                "WHERE player_id=? AND game='mbti'", (self.player,),
            ).fetchone()

    def wait_ready(self):
        deadline = time.monotonic() + 15
        while not Path(self.db + ".ready").exists():
            self.assertLess(time.monotonic(), deadline, "child never reached finish")
            time.sleep(.01)

    def test_old_generation_and_missing_metadata_never_touch_restarted_run(self):
        old = self.start()
        new = self.start()
        self.assertNotEqual(old["session_id"], new["session_id"])
        result = self.run_action("answer_batch", **self.submission(old))
        self.assertEqual(result["code"], -32002)
        result = self.run_action("answer_batch", answers=[3] * 93)
        self.assertEqual(result["code"], -32602)
        self.assertEqual(self.row(), (0, "[]", new["session_id"]))

    def test_mcp_restart_same_clock_changes_generation(self):
        self.start()
        arguments = {"mode": "full_fast"}
        self.assertNotIn("error", self.run_action("mcp_start", **arguments))
        first = self.run_action("result")["result"]
        self.assertNotIn("error", self.run_action("mcp_start", **arguments))
        second = self.run_action("result")["result"]
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertIn("error", self.run_action("answer_batch", **self.submission(first)))
        self.assertEqual(self.row(), (0, "[]", second["session_id"]))

    def test_mcp_progress_rejects_old_index_and_conflicting_saved_prefix(self):
        old = self.start()
        self.assertNotIn("error", self.run_action("mcp_batch", a_scores=[1] * 16))
        body = self.submission(old)
        self.assertIn("error", self.run_action("answer_batch", **body))
        body["expected_index"] = 16
        self.assertIn("error", self.run_action("answer_batch", **body))
        self.assertEqual(self.row()[:2], (16, json.dumps([1] * 16)))
        body["answers"][:16] = [1] * 16
        self.assertNotIn("error", self.run_action("answer_batch", **body))

    def test_invalid_late_answer_leaves_entire_run_unchanged(self):
        state = self.start()
        body = self.submission(state)
        body["answers"][-1] = 100
        self.assertIn("error", self.run_action("answer_batch", **body))
        self.assertEqual(self.row(), (0, "[]", state["session_id"]))

    def test_killed_transaction_rolls_back_and_retry_completes(self):
        state = self.start()
        body = self.submission(state)
        child = self.spawn("pause_finish", body)
        self.wait_ready()
        child.kill()
        child.communicate(timeout=10)
        self.assertEqual(self.row(), (0, "[]", state["session_id"]))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0], 0)
        self.assertNotIn("error", self.run_action("answer_batch", **body))
        before = Path(self.db).read_bytes()
        self.assertNotIn("error", self.run_action("answer_batch", **body))
        self.assertEqual(Path(self.db).read_bytes(), before)
        body["answers"][0] = 0
        self.assertIn("error", self.run_action("answer_batch", **body))

    def test_restarting_process_waits_for_whole_submission_and_new_run_is_empty(self):
        state = self.start()
        submitting = self.spawn("pause_finish", self.submission(state))
        self.wait_ready()
        restarting = self.spawn("mcp_start", {"player_id": self.player, "mode": "full_fast"})
        time.sleep(.3)
        self.assertIsNone(restarting.poll())
        Path(self.db + ".release").touch()
        self.assertNotIn("error", self.collect(submitting))
        self.assertNotIn("error", self.collect(restarting))
        row = self.row()
        self.assertEqual(row[:2], (0, "[]"))
        self.assertNotEqual(row[2], state["session_id"])
        self.assertIn("error", self.run_action("answer_batch", **self.submission(state)))

    def test_parallel_conflicting_whole_submissions_choose_one_result(self):
        state = self.start()
        first = self.spawn("answer_batch", self.submission(state, 1))
        second = self.spawn("answer_batch", self.submission(state, 5))
        results = [self.collect(first), self.collect(second)]
        self.assertEqual(sum("result" in result for result in results), 1)
        self.assertEqual(sum("error" in result for result in results), 1)
        self.assertIsNone(self.row())

    def test_all_standard_web_engines_keep_scoring_contract(self):
        for game in ("mbti", "dnd", "enneagram", "love", "ecr", "humanity", "sins_virtues"):
            with self.subTest(game=game):
                state = self.start(game=game)
                body = self.submission(state, 1)
                result = self.run_action("answer_batch", **body)
                self.assertNotIn("error", result)
                self.assertTrue(result["result"]["complete"])
                self.assertEqual(result["result"]["result_data"]["kind"], game)
                self.assertNotIn("error", self.run_action("answer_batch", **body))

    def test_browser_keeps_request_generation_and_does_not_retag_old_drafts(self):
        script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const page = fs.readFileSync("test_game.html", "utf8");
const api = page.split("    async function api(")[1].split("    function renderModes(")[0];
const restore = page.split("    function restoreDraft(")[1].split("    function draftSnapshot(")[0];
const requests = [], archived = [];
let saved = JSON.stringify({game:"mbti", session_id:"old", edition:"complete", total:2, answers:[1,2], cursor:1});
const context = vm.createContext({
  CONFIG:{game:"mbti",research:false}, activeState:{session_id:"current",expected_index:0},
  identityBody: x=>x, requestHeaders: ()=>({}), token: ()=>"", currentDraftKey: ()=>"draft",
  localStorage:{getItem:()=>saved}, preserveDraft: raw=>archived.push(raw),
  fetch:async (url, request)=>{ requests.push(JSON.parse(request.body)); return {ok:true,status:200,json:async()=>({})}; },
  answers:[],cursor:0
});
vm.runInContext("async function api(" + api + "function restoreDraft(" + restore, context);
(async()=>{
  await context.api("answer_batch", {answers:[1,2]});
  assert.equal(requests[0].session_id,"current");
  assert.equal(requests[0].expected_index,0);
  await context.api("answer_batch", {answers:[1,2],session_id:"old",expected_index:16});
  assert.equal(requests[1].session_id,"old");
  assert.equal(requests[1].expected_index,16);
  const state={session_id:"current",edition:"complete",total:2};
  context.restoreDraft(state);
  assert.equal(context.answers.filter(Number.isInteger).length,0);
  assert.equal(archived.length,1);
  saved=saved.replace('"old"','"current"');
  context.restoreDraft(state);
  assert.equal(JSON.stringify(context.answers),"[1,2]");
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1], timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
