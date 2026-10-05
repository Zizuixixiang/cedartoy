"""Identity and side-effect ordering at the root MCP gameplay boundary."""

from contextlib import ExitStack
import copy
import json
import unittest
from unittest.mock import patch

import server


def observe_scenario(scenario, implementation=None):
    """Record ordering without databases, live game services, or real credentials."""
    events = []
    dispatched = []
    guest = scenario in {"guest", "claimed_guest"}
    account = {"id": 42, "username": "fixture-ai", "is_ai": True}

    def callback(label, result=None):
        def call(*args, **kwargs):
            events.append(label)
            return copy.deepcopy(result)
        return call

    def tombstone(player_id):
        events.append("tombstone:" + player_id)
        if scenario == "claimed_guest":
            raise server._McpError(-32001, "claimed fixture")

    def dispatch(game, arguments):
        events.append("dispatch")
        dispatched.append((game, copy.deepcopy(arguments)))
        return {"error": "fixture"} if scenario == "game_error" else {"text": "engine"}

    with ExitStack() as stack:
        dependencies = {
            "_authenticated_ai_player_id": callback("root_identity", None if guest else "42"),
            "_duel_unread_request_reminder": callback("duel_reminder", ""),
            "_mcp_forced_announcement": callback("forced_announcement", ""),
            "_current_account": callback("account", account),
            "_auto_migrate_legacy_account_saves": callback("legacy_migration"),
            "_reject_claimed_guest": tombstone,
            "_anti_addiction_context": callback("anti_context", {"fixture": True}),
            "_anti_addiction_preflight": callback("preflight", {"text": "blocked"} if scenario == "blocked" else None),
            "_play_vendor_cmd": dispatch,
            "_stamp_save_owner": callback("stamp_owner"),
            "_ensure_guest_claim_code": callback("claim_code", "fixture-code"),
            "_anti_addiction_record_success": callback("success", "rest notice"),
            "_play_announcements": callback("announcement", "fixture notice"),
        }
        for name, value in dependencies.items():
            stack.enter_context(patch.object(server, name, value))
        stack.enter_context(patch.object(server.game_activity, "record", callback("activity")))
        # Resolve after patches, also allowing a pre-extraction source comparison.
        handler = implementation() if implementation else server._handle_root_mcp
        response = handler({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "play", "arguments": {
                "game": "bar", "action": "cmd", "player_id": "forged",
                "params": {"player_id": "other", "slot": 3, "command": "status"},
            }},
        }, bearer_token=None if guest else "fixture-token")
    return response, events, dispatched


class McpDispatchOrderTests(unittest.TestCase):
    def test_canonical_identity_and_finalization_order(self):
        prefixes = {
            "account": ["root_identity", "duel_reminder", "account", "legacy_migration", "anti_context", "preflight"],
            "guest": ["root_identity", "duel_reminder", "tombstone:guest:other", "anti_context", "preflight"],
        }
        for scenario in ("account", "guest", "game_error", "blocked", "claimed_guest"):
            with self.subTest(scenario=scenario):
                response, events, dispatched = observe_scenario(scenario)
                if scenario == "claimed_guest":
                    self.assertEqual(events, ["root_identity", "duel_reminder", "tombstone:guest:other", "forced_announcement"])
                    self.assertTrue(response["result"]["isError"])
                    self.assertEqual(dispatched, [])
                    continue
                prefix = prefixes["guest" if scenario == "guest" else "account"]
                if scenario == "blocked":
                    self.assertEqual(events, prefix + ["forced_announcement"])
                    self.assertEqual(dispatched, [])
                    continue
                suffix = ["dispatch", "activity"]
                if scenario != "game_error":
                    suffix += ["claim_code" if scenario == "guest" else "stamp_owner", "success", "announcement"]
                self.assertEqual(events, prefix + suffix + ["forced_announcement"])
                expected = "guest:other" if scenario == "guest" else "42:3"
                self.assertEqual(dispatched[0][1]["player_id"], expected)
                self.assertEqual(dispatched[0][1]["params"]["player_id"], expected)
                self.assertNotIn("slot", dispatched[0][1]["params"])
                json.loads(response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
