import unittest
from unittest.mock import patch

import server


class RootMcpProtocolTests(unittest.TestCase):
    def _initialize(self, params=None):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
        }
        if params is not None:
            payload["params"] = params
        return server._handle_root_mcp(payload)

    def test_supported_protocol_versions_are_echoed(self):
        for protocol_version in (
            "2024-11-05",
            "2025-03-26",
            "2025-06-18",
            "2025-11-25",
        ):
            with self.subTest(protocol_version=protocol_version):
                response = self._initialize({"protocolVersion": protocol_version})

                self.assertNotIn("error", response)
                self.assertEqual(
                    response["result"]["protocolVersion"], protocol_version
                )

    def test_missing_or_unknown_protocol_version_uses_legacy_version(self):
        for params in (
            None,
            {},
            {"protocolVersion": "unknown"},
            {"protocolVersion": "2026-07-28"},
        ):
            with self.subTest(params=params):
                response = self._initialize(params)

                self.assertNotIn("error", response)
                self.assertEqual(
                    response["result"]["protocolVersion"], "2024-11-05"
                )

    def test_existing_tools_list_and_tools_call_still_work(self):
        listed = server._handle_root_mcp(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        self.assertEqual(
            {tool["name"] for tool in listed["result"]["tools"]},
            {"list_games", "get_guide", "play", "account"},
        )

        with patch.object(server, "_tool_list_games", return_value="game catalog"):
            called = server._handle_root_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_games", "arguments": {}},
                }
            )

        self.assertNotIn("error", called)
        self.assertFalse(called["result"]["isError"])
        self.assertEqual(
            called["result"]["content"],
            [{"type": "text", "text": "game catalog"}],
        )


if __name__ == "__main__":
    unittest.main()
