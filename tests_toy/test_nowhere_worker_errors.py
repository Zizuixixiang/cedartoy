"""Worker error recovery uses disposable archives and real IPC, never live saves."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from jsonschema import ValidationError, validate

from nowhere_adapter import diagnostics, handler, pool, storage
from tests_toy.test_nowhere import TemporaryStores, archive
import server
from vendor_cmd_adapter.base import VendorCmdError


class DiagnosticsTests(unittest.TestCase):
    def test_validation_metadata_never_echoes_values_unknown_keys_or_paths(self):
        secret = "PRIVATE-token-password-provider-url-save-text"
        for schema, instance in (({"type": "integer"}, secret),
                                 ({"enum": ["allowed"]}, secret),
                                 ({"additionalProperties": False}, {secret: secret}),
                                 ({"required": [secret]}, {})):
            with self.subTest(schema=list(schema)):
                try:
                    validate(instance, schema)
                except ValidationError as exc:
                    exc.path.append(secret)
                    result = diagnostics.internal_error(exc, {"text": secret, secret: secret}, "validate")
                self.assertNotIn(secret, json.dumps(result))
                self.assertEqual(result["module"], "jsonschema.exceptions")
                self.assertEqual(result["request_keys"], ["text"])
                self.assertEqual(result["request_types"], {"text": "string"})
                self.assertEqual(result["unknown_key_count"], 1)
                self.assertEqual(result["validation"]["path"], ["<redacted>"])
        result = diagnostics.internal_error(RuntimeError(secret), {"save_data": {"secret": secret}}, "restore")
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(diagnostics.action_name({"action": secret}), "unknown")

    def test_stderr_is_drained_without_logging_raw_content(self):
        worker = object.__new__(pool.Worker)
        worker.proc = type("Process", (), {"stderr": io.BytesIO(b"PRIVATE-provider-key\n" * 1000)})()
        with self.assertLogs("nowhere_adapter.pool", level="WARNING") as logs:
            worker._drain_stderr("guest:stderrtest")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("guest:stderrtest", logs.output[0])
        self.assertNotIn("PRIVATE", logs.output[0])
        self.assertEqual(worker.proc.stderr.read(), b"")


class ReturnedErrorTests(TemporaryStores):
    def test_all_returned_errors_discard_after_acquire_without_retry_or_commit(self):
        for error in ({"error": "upstream rejected action"},
                      {"error": "PRIVATE-exception-message", "internal_error": {"type": "ValueError"}},
                      {"internal_error": {"type": "ValueError"}}):
            with self.subTest(internal="internal_error" in error):
                self.put("101")
                original = (storage.ROOT / "101/save.json").read_bytes()
                workers = pool.Pool()
                closed = []
                class FakeWorker:
                    def __init__(self, player):
                        self.busy = False
                        self.used = 0
                    def call(self, payload):
                        self.payload = copy.deepcopy(payload)
                        return {**error, "archive": archive("MUST-NOT-COMMIT")}
                    def close(self):
                        closed.append(self.busy)
                with patch.object(pool, "Worker", FakeWorker), patch.object(handler, "POOL", workers), \
                     patch.object(storage, "write") as write, patch.object(handler.LOGGER, "error") as log:
                    with self.assertRaises(VendorCmdError) as caught:
                        handler.play({"player_id": "101", "action": "say", "text": "PRIVATE-request"})
                    self.assertEqual(closed, [False])
                    self.assertNotIn("101", workers.workers)
                    write.assert_not_called()
                    if "internal_error" in error:
                        self.assertNotIn("PRIVATE", str(caught.exception))
                        self.assertNotIn("PRIVATE", str(log.call_args))
                    with workers.acquire("101") as fresh:
                        self.assertFalse(hasattr(fresh, "payload"))
                workers.close()
                self.assertEqual((storage.ROOT / "101/save.json").read_bytes(), original)


# Runs only in a disposable test worker, through the unchanged production IPC
# loop. Fail AFTER a real action has mutated the workspace. No production hook.
_FAIL_AFTER_ACTION = '''
import asyncio, sys
from pathlib import Path
from jsonschema import validate
from nowhere_adapter.worker import Engine, main
original = Engine.run
async def run(self, payload):
    result = await original(self, payload)
    if payload['request']['action'] == 'say':
        Path(sys.argv[2]).open('a').write('action executed\\n')
        print('PRIVATE-STDERR-provider-key', file=sys.stderr)
        self.tools['say'].parameters['required'] = ['poisoned-required-field']
        validate({'text': payload['request']['text']},
                 {'properties': {'text': {'type': 'integer'}}})
    return result
Engine.run = run
asyncio.run(main())
'''


@unittest.skipUnless(os.environ.get("NOWHERE_REAL_TEST") == "1", "set NOWHERE_REAL_TEST=1 for real worker IPC")
class RealWorkerErrorTests(TemporaryStores):
    def setUp(self):
        super().setUp()
        self.pool = pool.Pool()
        self.stack.enter_context(patch.object(handler, "POOL", self.pool))

    def tearDown(self):
        self.pool.close()
        super().tearDown()

    def play(self, action, **params):
        # Include the platform's real params merge and adapter identity stripping.
        return server._play_vendor_cmd("nowhere", {"game": "nowhere", "player_id": "guest:workererror",
                                                    "slot": 1, "action": action, "params": params})

    def test_exact_screenshot_sequence_through_handler_and_real_worker(self):
        self.play("open_door", intent="安静", cotraveler="1")
        worker = self.pool.workers["guest:workererror"]
        self.play("walk", direction="forward", distance_km=1)
        text = "第一次出门就落在这儿。风是热的——小发，我到了。"
        self.play("say", text=text)
        self.assertIs(self.pool.workers["guest:workererror"], worker)
        saved = storage.read("guest:workererror")
        self.assertEqual(saved["runtime"]["metadata"]["cotraveler"], "1")
        self.assertIn(text, json.dumps(saved, ensure_ascii=False))

    def test_internal_failure_after_mutation_preserves_archive_and_next_call_is_fresh(self):
        player = "guest:workererror"
        self.play("open_door", to="北京", cotraveler="0")
        self.play("say", text="PRIVATE-ARCHIVE-CONTENT")
        path = storage.ROOT / player / "save.json"
        original = path.read_bytes()
        self.pool.discard(player)
        popen = subprocess.Popen
        calls = self.root / "action-count"
        def launch(args, **kwargs):
            return popen([args[0], "-c", _FAIL_AFTER_ACTION, player, str(calls)], **kwargs)
        with patch.object(pool.subprocess, "Popen", side_effect=launch):
            with self.pool.acquire(player) as broken:
                pass
        with self.assertLogs("nowhere_adapter", level="WARNING") as logs:
            with self.assertRaises(server._McpError) as caught:
                self.play("say", text="PRIVATE-FAILED-REQUEST")
        self.assertEqual(calls.read_text(), "action executed\n")  # No automatic retry.
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(path.parent.glob(".save-*")))
        self.assertNotIn(player, self.pool.workers)
        self.assertIsNotNone(broken.proc.poll())
        self.assertFalse(Path(broken.home).exists())
        output = "\n".join(logs.output)
        self.assertNotIn("PRIVATE", output + str(caught.exception))
        for expected in (player, "action=say", "jsonschema.exceptions", "ValidationError",
                         '"path": ["text"]', '"request_types": {"text": "string"}',
                         '"stage": "snapshot"', "type constraint failed"):
            self.assertIn(expected, output)
        self.play("say", text="RECOVERED")
        fresh = self.pool.workers[player]
        self.assertIsNot(fresh, broken)
        self.assertNotEqual(fresh.proc.pid, broken.proc.pid)
        saved = json.dumps(storage.read(player))
        self.assertIn("PRIVATE-ARCHIVE-CONTENT", saved)
        self.assertNotIn("PRIVATE-FAILED-REQUEST", saved)
        self.assertIn("RECOVERED", saved)

    def test_real_request_validation_keeps_strict_schema_and_discards_worker(self):
        self.play("open_door", to="北京", cotraveler="0")
        player = "guest:workererror"
        original = (storage.ROOT / player / "save.json").read_bytes()
        worker = self.pool.workers[player]
        for params in ({"direction": "forward", "distance_km": "PRIVATE-BAD-TYPE"},
                       {"direction": "forward", "PRIVATE-UNKNOWN-KEY": "PRIVATE-value"}):
            with self.assertLogs("nowhere_adapter.handler", level="ERROR") as logs:
                with self.assertRaises(server._McpError):
                    self.play("walk", **params)
            self.assertNotIn("PRIVATE", "\n".join(logs.output))
            self.assertIn("ValidationError", "\n".join(logs.output))
            self.assertNotIn(player, self.pool.workers)
            self.assertEqual((storage.ROOT / player / "save.json").read_bytes(), original)
        self.assertIsNotNone(worker.proc.poll())
        self.play("walk", direction="forward", distance_km=1)
        self.assertIsNot(self.pool.workers[player], worker)


if __name__ == "__main__":
    unittest.main()
