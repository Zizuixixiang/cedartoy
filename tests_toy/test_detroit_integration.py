from __future__ import annotations

import http.cookiejar
import http.client
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import detroit_adapter
import account_deletion

# This worktree intentionally has a sparse vendor checkout.  These two adapters
# eagerly import their upstream engines at module import time, while the Detroit
# tests only need CedarToy's identity/router helpers.  Stub just those absent
# engines so importing server remains a local, reproducible test.
_TEST_ROOT = Path(__file__).resolve().parents[1]
for _package_name, _upstream_path in (
    ("ciyuwu_adapter", _TEST_ROOT / "vendor" / "ci-yu-wu" / "engine.py"),
    ("eco_adapter", _TEST_ROOT / "eco" / "engine.py"),
):
    if Path(_upstream_path).is_file():
        continue
    _package = importlib.import_module(_package_name)
    _handler = types.ModuleType(_package_name + ".handler")
    _handler.handle_mcp = lambda payload: payload
    _handler.summarize_save = lambda *_args, **_kwargs: {}
    _package.handler = _handler
    sys.modules[_package_name + ".handler"] = _handler

import server


ROOT = _TEST_ROOT
FIXTURE_ROOT = ROOT / "tests_toy" / "fixtures"
REMOTE_HOST = "detroit-blind-run-rongrong.d7kjvtpfc4.chatgpt.site"
FAKE_CONNECTION = "dbr_" + ("a" * 22)
MCP_URL = f"https://{REMOTE_HOST}/detroit-mcp?connection={FAKE_CONNECTION}"


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._payload


class DetroitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="detroit-adapter-")
        self.save_root = Path(self.temp_dir.name) / "vendor_saves"
        self.env = patch.dict(os.environ, {"TOY_SECRET": "detroit-test-secret"}, clear=False)
        self.env.start()
        self.root_patch = patch.object(detroit_adapter, "SAVE_ROOT", self.save_root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.env.stop()
        self.temp_dir.cleanup()

    def state(self, player="42", *, save_id="save_abcdefghijk"):
        value = {
            "version": 1,
            "created_at": 1,
            "owner_cookie": "owner-top-secret",
            "mcp_url": MCP_URL,
        }
        if save_id:
            value["save_id"] = save_id
            value["summary"] = {"name": "测试周目", "difficulty": "casual", "chapter": 1}
        detroit_adapter._write_state_unlocked(player, value)
        return value

    def test_credentials_are_encrypted_and_players_and_slots_are_isolated(self):
        for player, save_id in (("42", "save_abcdefghijk"), ("42:2", "save_bcdefghijkl"), ("43", "save_cdefghijklm")):
            self.state(player, save_id=save_id)

        files = list(self.save_root.glob("detroit/*/remote_state.fernet"))
        self.assertEqual({path.parent.name for path in files}, {"42", "42:2", "43"})
        for path in files:
            raw = path.read_bytes()
            self.assertNotIn(b"owner-top-secret", raw)
            self.assertNotIn(b"dbr_", raw)
            self.assertNotIn(b"save_", raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(detroit_adapter.save_summary("42")["name"], "测试周目")
        self.assertTrue(detroit_adapter.has_save("42:2"))
        self.assertFalse(detroit_adapter.has_save("44"))

    def test_first_mapping_is_serialized_for_same_canonical_identity(self):
        provisions = []

        def fake_ensure(player_id, state):
            if state:
                return state
            provisions.append(player_id)
            time.sleep(0.03)
            value = {
                "version": 1,
                "created_at": 1,
                "owner_cookie": "owner-top-secret",
                "mcp_url": MCP_URL,
            }
            detroit_adapter._write_state_unlocked(player_id, value)
            return value

        with patch.object(detroit_adapter, "_ensure_owner_and_connection_unlocked", side_effect=fake_ensure), patch.object(
            detroit_adapter, "_mcp_call_unlocked", return_value={"saves": []}
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: detroit_adapter.play("42", "list_saves", {}), range(2)))

        self.assertEqual(provisions, ["42"])
        self.assertEqual(results[0]["saves"], [])
        self.assertEqual(results[1]["saves"], [])

    def test_create_maps_save_and_keeps_delete_name_from_mcp_result(self):
        self.state(save_id=None)
        created = {
            "save_id": "save_newabcdefgh",
            "save": "测试新周目",
            "revision": 1,
            "node_id": "n1",
            "screen": "scene",
            "choices": [{"label": "A"}],
        }
        with patch.object(
            detroit_adapter,
            "_mcp_call_unlocked",
            side_effect=[{"sessions": [], "ending_progress": {}}, created],
        ):
            result = detroit_adapter.play(
                "42", "create_save", {"name": "测试新周目", "difficulty": "casual"}
            )
        state = detroit_adapter._read_state_unlocked("42")
        self.assertEqual(result["save_id"], detroit_adapter.PUBLIC_SAVE_ID)
        self.assertEqual(state["save_id"], "save_newabcdefgh")
        self.assertEqual(state["summary"]["name"], "测试新周目")
        self.assertEqual(state["summary"]["difficulty"], "casual")

    def test_human_page_and_machine_resolve_the_same_slot(self):
        captured = {}

        def fake_play(player_id, action, arguments):
            captured.update(player_id=player_id, action=action, arguments=arguments)
            return {"saves": []}

        with patch.object(server, "_auto_migrate_legacy_account_saves"), patch.object(
            server, "_anti_addiction_context", return_value=None
        ), patch.object(server, "_stamp_save_owner"), patch.object(
            server, "_play_announcements", return_value=""
        ), patch.object(server.detroit_adapter, "play", side_effect=fake_play):
            result = json.loads(
                server._tool_play(
                    {"game": "detroit", "action": "list_saves", "params": {"slot": 3}},
                    authenticated_account={"id": 42, "username": "小机", "is_ai": 1},
                )
            )
        self.assertEqual(captured["player_id"], "42:3")
        self.assertEqual(result["saves"], [])

        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Cookie": "detroit_token=human-token; detroit_player=42%3A3"}
        with patch.object(server, "_current_account", return_value={"id": 7, "is_ai": 0}), patch.object(
            server, "_bound_ai_slot_target_for_user",
            return_value={"player": "42:3", "ai_user_id": 42, "machine_name": "小机", "slot": 3},
        ):
            _token, target = handler._detroit_human_target()
        self.assertEqual(target["player"], captured["player_id"])

    def test_human_entry_redirect_sets_browser_session_and_protects_api(self):
        human_token = "test-human-token"
        target = {"player": "42:3", "ai_user_id": 42, "machine_name": "小机", "slot": 3}

        def current_account(raw_token):
            if raw_token != human_token:
                raise server._McpError(-32001, "未登录：当前是游客模式。已注册请把 MCP 地址改成 toy.cedarstar.org/你的token 再重连；未注册请先 login_or_register。")
            return {"id": 7, "username": "人类", "is_ai": 0}

        def bound_target(user, requested_player):
            return target if user.get("id") == 7 and requested_player == "42:3" else None

        host_fixture = (FIXTURE_ROOT / "detroit_host_v17.html").read_bytes()
        browser_payload = json.dumps(
            {"sessions": [{"id": detroit_adapter.PUBLIC_SAVE_ID, "name": "已有第一局"}]},
            ensure_ascii=False,
        ).encode("utf-8")

        with patch.object(server, "_current_account", side_effect=current_account), patch.object(
            server, "_bound_ai_slot_target_for_user", side_effect=bound_target
        ), patch.object(
            server.detroit_adapter,
            "fetch_public",
            return_value=(200, "text/html; charset=utf-8", host_fixture),
        ), patch.object(
            server.detroit_adapter,
            "browser_api",
            return_value=(200, {"content-type": "application/json; charset=utf-8"}, browser_payload),
        ) as browser_api:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CedarToyHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{httpd.server_port}"
                jar = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar)
                )
                query = urllib.parse.urlencode({"player": "42:3", "token": human_token})
                http_entry = urllib.request.Request(
                    f"{base}/detroit/?{query}", headers={"X-Forwarded-Proto": "http"}
                )
                with opener.open(http_entry) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("底特律盲玩主持台", response.read().decode("utf-8"))
                self.assertEqual({cookie.name for cookie in jar}, {"detroit_token", "detroit_player"})
                self.assertTrue(all(not cookie.secure for cookie in jar))

                with opener.open(f"{base}/detroit/api/sessions") as response:
                    self.assertEqual(json.loads(response.read())["sessions"][0]["name"], "已有第一局")
                browser_api.assert_called_once_with("42:3", "GET", "sessions", query={}, payload=None)

                unauthorized = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    unauthorized.open(f"{base}/detroit/api/sessions")
                self.assertEqual(denied.exception.code, 401)
                denied_body = denied.exception.read().decode("utf-8")
                self.assertIn("网页登录", denied_body)
                self.assertNotIn("MCP 地址", denied_body)

                unbound_query = urllib.parse.urlencode({"player": "99", "token": human_token})
                with self.assertRaises(urllib.error.HTTPError) as unbound:
                    unauthorized.open(f"{base}/detroit/?{unbound_query}")
                self.assertEqual(unbound.exception.code, 403)

                https_proxy = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                try:
                    https_proxy.request(
                        "GET",
                        f"/detroit/?{query}",
                        headers={"X-Forwarded-Proto": "https"},
                    )
                    secure_response = https_proxy.getresponse()
                    secure_response.read()
                    secure_cookies = secure_response.headers.get_all("Set-Cookie") or []
                finally:
                    https_proxy.close()
                self.assertEqual(secure_response.status, 303)
                self.assertEqual(len(secure_cookies), 2)
                self.assertTrue(all("; Secure" in cookie for cookie in secure_cookies))
                self.assertEqual(browser_api.call_count, 1)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def test_uncertain_play_step_is_never_blindly_retried(self):
        self.state()
        selection = {"revision": 1, "node_id": "n1", "label": "A", "reason": "这是我的选择"}
        uncertain = detroit_adapter.DetroitError("断线", uncertain=True, status=502)
        with patch.object(detroit_adapter, "_mcp_call_unlocked", side_effect=uncertain) as upstream:
            with self.assertRaises(detroit_adapter.DetroitError):
                detroit_adapter.play("42", "play_step", selection)
            self.assertEqual(upstream.call_count, 1)

        with patch.object(detroit_adapter, "_mcp_call_unlocked") as upstream:
            with self.assertRaisesRegex(detroit_adapter.DetroitError, "不会自动重试"):
                detroit_adapter.play("42", "play_step", selection)
            upstream.assert_not_called()

        with patch.object(
            detroit_adapter,
            "_mcp_call_unlocked",
            return_value={"revision": 1, "node_id": "n1", "screen": "scene", "choices": [{"label": "A"}]},
        ):
            checked = detroit_adapter.play("42", "read_current_scene", {})
        self.assertIn("confirm_retry=true", checked["cedartoy_retry_notice"])

        with patch.object(detroit_adapter, "_mcp_call_unlocked") as upstream:
            with self.assertRaises(detroit_adapter.DetroitError):
                detroit_adapter.play("42", "play_step", selection)
            upstream.assert_not_called()

        advanced = {"revision": 2, "node_id": "n2", "screen": "scene", "choices": []}
        with patch.object(detroit_adapter, "_mcp_call_unlocked", return_value=advanced) as upstream:
            result = detroit_adapter.play("42", "play_step", {**selection, "confirm_retry": True})
        self.assertEqual(upstream.call_count, 1)
        self.assertEqual(result["revision"], 2)
        self.assertNotIn("pending_play_step", detroit_adapter._read_state_unlocked("42"))

    def test_browser_proxy_filters_saves_and_rejects_foreign_save_id(self):
        self.state()
        sessions = {
            "sessions": [
                {"id": "save_abcdefghijk", "name": "自己的"},
                {"id": "save_foreignxxxxx", "name": "不应暴露"},
            ],
            "ending_progress": {"total": 10, "discovered": 0, "percent": 0},
        }
        with patch.object(detroit_adapter, "_browser_call_unlocked", return_value=FakeResponse(sessions)):
            status, _headers, body = detroit_adapter.browser_api("42", "GET", "sessions")
        self.assertEqual(status, 200)
        listed = json.loads(body)["sessions"]
        self.assertEqual([item["name"] for item in listed], ["自己的"])
        self.assertEqual(listed[0]["id"], detroit_adapter.PUBLIC_SAVE_ID)
        self.assertNotIn("save_abcdefghijk", body.decode("utf-8"))

        with patch.object(detroit_adapter, "_browser_call_unlocked") as upstream:
            with self.assertRaisesRegex(detroit_adapter.DetroitError, "不能访问"):
                detroit_adapter.browser_api(
                    "42", "GET", "session", query={"id": "save_foreignxxxxx"}
                )
            upstream.assert_not_called()

        captured = {}

        def browser_call(_state, method, endpoint, **kwargs):
            captured.update(method=method, endpoint=endpoint, **kwargs)
            return FakeResponse({"revision": 2, "node_id": "n2", "session": {"id": "save_abcdefghijk"}})

        with patch.object(detroit_adapter, "_browser_call_unlocked", side_effect=browser_call):
            detroit_adapter.browser_api(
                "42",
                "POST",
                "action",
                payload={"id": detroit_adapter.PUBLIC_SAVE_ID, "action": "continue", "request_id": "req_12345678"},
            )
        self.assertEqual(captured["payload"]["id"], "save_abcdefghijk")
        self.assertNotIn("mcp_url", captured["payload"])

        backup = {
            "format": "Detroit Blind Run 完整存檔",
            "session": {"id": "save_abcdefghijk", "name": "自己的"},
        }
        with patch.object(detroit_adapter, "_browser_call_unlocked", return_value=FakeResponse(backup)):
            _status, headers, body = detroit_adapter.browser_api(
                "42", "GET", "backup", query={"id": detroit_adapter.PUBLIC_SAVE_ID}
            )
        exported = json.loads(body)
        self.assertEqual(exported["session"]["id"], detroit_adapter.BACKUP_SAVE_ID)
        self.assertNotIn("save_abcdefghijk", body.decode("utf-8"))
        self.assertEqual(headers["content-disposition"], 'attachment; filename="detroit-full-save.json"')

    def test_account_purge_uses_remote_delete_before_local_mapping_cleanup(self):
        self.state()
        mapping = self.save_root / "detroit" / "42" / detroit_adapter.STATE_NAME
        callbacks = []

        def delete_remote(player_id):
            callbacks.append(player_id)
            mapping.unlink()
            lock = mapping.parent / detroit_adapter.LOCK_NAME
            if lock.exists():
                lock.unlink()
            return True

        counts = account_deletion._delete_managed_saves(
            self.save_root,
            ["42"],
            workkk_delete=None,
            garden_delete=None,
            detroit_delete=delete_remote,
        )
        self.assertEqual(callbacks, ["42"])
        self.assertEqual(counts["detroit"], 1)
        self.assertFalse(mapping.exists())

        self.state("43")
        account_deletion._delete_file_saves(self.save_root, ["43"])
        self.assertTrue((self.save_root / "detroit" / "43" / detroit_adapter.STATE_NAME).exists())

    def test_original_page_rewrite_and_public_attribution(self):
        html = detroit_adapter.rewrite_public(
            "host", (FIXTURE_ROOT / "detroit_host_v17.html").read_bytes()
        ).decode("utf-8")
        script = detroit_adapter.rewrite_public(
            "host.js", (FIXTURE_ROOT / "detroit_host_v17.js").read_bytes()
        ).decode("utf-8")
        self.assertIn("/detroit/host.css", html)
        self.assertIn("/detroit/host.js", html)
        self.assertNotIn("/cdn-cgi/", html)
        self.assertIn("/detroit/api/sessions", script)
        self.assertNotIn("/api/mcp-connection", script)
        self.assertIn("CedarToy 統一 MCP", script)
        self.assertIn('get_guide(game="detroit")', script)
        self.assertIn("遊戲與雲端存檔由作者站點托管", script)
        self.assertNotIn("上游 owner", script)
        self.assertNotIn("安全代理", script)
        self.assertNotIn("專屬網址", script)
        self.assertNotIn("存檔鑰匙", script)
        self.assertNotIn("第三方中轉", script)
        self.assertIn("如火如風的容", script)
        self.assertIn("27231843685", script)
        self.assertIn("Baba88611", script)
        self.assertIn(
            "https://detroit-blind-run-rongrong.d7kjvtpfc4.chatgpt.site/host", script
        )
        self.assertIn("作者原版 ↗", script)
        self.assertIn('target="_blank"', script)

        homepage = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id: "detroit"', homepage)
        self.assertIn(
            'url: "https://detroit-blind-run-rongrong.d7kjvtpfc4.chatgpt.site/host"',
            homepage,
        )
        self.assertIn('ctaLabel: "作者原版 ↗"', homepage)
        self.assertIn("openDetroitPicker", homepage)
        self.assertIn("/detroit/?player=", homepage)
        detroit_picker = homepage.split("async function openDetroitPicker", 1)[1].split(
            "function renderCampingPlazaSlotPicker", 1
        )[0]
        self.assertIn(".filter((machine) => machine.slots.length > 0)", detroit_picker)
        self.assertIn("还没有底特律存档", detroit_picker)
        self.assertIn("if (machine.slots.length === 1)", detroit_picker)
        self.assertNotIn("[1, 2, 3, 4, 5].map", detroit_picker)

    def test_root_tool_schema_guide_and_log_redaction(self):
        play_tool = next(tool for tool in server._PLATFORM_TOOLS if tool["name"] == "play")
        properties = play_tool["inputSchema"]["properties"]["params"]["properties"]
        for name in ("revision", "node_id", "label", "reason", "reflection", "name", "difficulty", "confirm_retry"):
            self.assertIn(name, properties)
        all_of = json.dumps(play_tool["inputSchema"]["allOf"], ensure_ascii=False)
        self.assertIn("create_save", all_of)
        self.assertIn("play_step", all_of)
        guide = json.loads(server._tool_get_guide({"game": "detroit"}))["guide"]
        self.assertIn("不会自动重试", guide)
        self.assertIn("如火如風的容", guide)
        self.assertIn("27231843685", guide)
        self.assertIn(
            "https://detroit-blind-run-rongrong.d7kjvtpfc4.chatgpt.site/host", guide
        )
        self.assertIn("detroit·底特律：变人，分支叙事", server._tool_list_games())
        redacted = server._redact_http_log_text(
            f"GET /detroit-mcp?connection={FAKE_CONNECTION} and {MCP_URL}"
        )
        self.assertNotIn("dbr_", redacted)
        self.assertIn("TOKEN_REDACTED", redacted)


if __name__ == "__main__":
    unittest.main()
