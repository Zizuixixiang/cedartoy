"""Boundary tests use temporary stores; real engine test is explicitly opt-in."""
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import server
from nowhere_adapter import handler, shared, storage, web
from vendor_cmd_adapter.base import VendorCmdError

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/nowhere"


def archive(place="北京"):
    return {"format": "cedartoy.nowhere.v1", "upstream": storage.VERSION, "generation": "test-generation",
            "files": {"journey.json": {"save_version": 1, "place_name": place, "pos": [39.9, 116.4],
                                          "path": [], "walk_step_counter": 0},
                      "marks.json": [], "notebook.json": {"flora": []},
                      "postcards.json": {"items": [{"id": 1, "text": "私信", "stamp": {}, "replies": []}]}},
            "images": {}, "runtime": {"rng": json.loads(json.dumps(random.Random(1).getstate()))}}


def travelers_module():
    spec = importlib.util.spec_from_file_location("test_nowhere_travelers", VENDOR / "nowhere/travelers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TemporaryStores(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nowhere-test-")
        self.root = Path(self.tmp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(storage, "ROOT", self.root / "vendor_saves/nowhere"))
        self.stack.enter_context(patch.dict(os.environ, {"NOWHERE_SHARED_DB": str(self.root / "shared.db")}))
        self.stack.enter_context(patch.object(server, "VENDOR_SAVE_ROOT", self.root / "vendor_saves"))
        self.stack.enter_context(patch.object(server, "SESSIONS_DB_PATH", self.root / "sessions.db"))
        self.stack.enter_context(patch.object(server, "_auto_migrate_legacy_account_saves"))
        self.stack.enter_context(patch.object(server, "_ensure_guest_claim_code", return_value=None))
        self.stack.enter_context(patch.object(server, "_play_announcements", return_value=""))
        self.stack.enter_context(patch.object(server, "_anti_addiction_context", return_value=None))
        self.stack.enter_context(patch.object(server, "_reject_claimed_guest"))
        self.stack.enter_context(patch.object(server, "_public_user", side_effect=lambda user: {"id": user["id"]}))
        @contextmanager
        def account_db():
            db = sqlite3.connect(self.root / "empty-accounts.db")
            db.row_factory = sqlite3.Row
            try: yield db
            finally: db.close()
        self.stack.enter_context(patch.object(server, "_db_connect", side_effect=account_db))

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    def put(self, player, data=None):
        with storage.locked(player):
            storage.write(player, data or archive())


class PersistenceTests(TemporaryStores):
    def test_two_accounts_two_slots_and_guest_multifile_roundtrip(self):
        for player in ("101", "101:2", "202", "202:2", "guest:nowheretest"):
            data = archive(player)
            data["files"]["marks.json"] = [{"name": player}]
            self.put(player, data)
        for player in ("101", "101:2", "202", "202:2", "guest:nowheretest"):
            exported = handler.play({"player_id": player, "action": "export"})["save_data"]
            self.assertEqual(exported["files"]["journey.json"]["place_name"], player)
            self.assertEqual(exported["files"]["marks.json"], [{"name": player}])
            self.assertNotIn("travelers.json", exported["files"])

    def test_concurrent_cross_process_saves_do_not_drop_updates(self):
        self.put("101")
        code = '''
import sys
from pathlib import Path
from nowhere_adapter import storage
storage.ROOT=Path(sys.argv[1])
for i in range(8):
 with storage.locked('101'):
  a=storage.read('101')
  a['files']['journey.json']['walk_step_counter'] += 1
  storage.write('101',a)
'''
        procs = [subprocess.Popen([sys.executable, "-c", code, str(storage.ROOT)], cwd=ROOT) for _ in range(4)]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=30), 0)
        self.assertEqual(storage.read("101")["files"]["journey.json"]["walk_step_counter"], 32)
        self.assertFalse(list((storage.ROOT / "101").glob(".save-*")))

    def test_overwrite_confirmation_precedes_worker_and_failed_write_is_atomic(self):
        self.put("101")
        original = (storage.ROOT / "101/save.json").read_bytes()
        for action in ("new", "open_door", "import"):
            with self.subTest(action=action), patch.object(handler.POOL, "acquire") as worker:
                with self.assertRaises(VendorCmdError):
                    handler.play({"player_id": "101", "action": action, "save_data": archive()})
                worker.assert_not_called()
        with patch.object(storage.os, "replace", side_effect=OSError("test disk failure")):
            with self.assertRaises(OSError):
                self.put("101", archive("巴黎"))
        self.assertEqual((storage.ROOT / "101/save.json").read_bytes(), original)

    def test_corrupt_archive_is_preserved_and_never_recreated(self):
        path = storage.ROOT / "101/save.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"broken":')
        for _ in range(2):
            with self.assertRaisesRegex(VendorCmdError, "原档"):
                handler.play({"player_id": "101", "action": "new", "confirm": True})
        self.assertEqual(path.read_text(), '{"broken":')
        self.assertEqual(len(list(path.parent.glob("save.corrupt-*.json"))), 1)

    def test_import_rejects_traversal_shared_state_and_card_html_ids(self):
        for key in ("../escape.json", "journeys/../outside.json", "travelers.json"):
            data = archive()
            data["files"][key] = {}
            with self.subTest(key=key), self.assertRaises(VendorCmdError):
                storage.validate(data)
        data = archive()
        data["files"]["postcards.json"]["items"][0]["id"] = '1);alert(1)//'
        with self.assertRaises(VendorCmdError):
            storage.validate(data)

    def test_claim_delete_and_pending_poster_cannot_resurrect_save(self):
        from nowhere_adapter.worker import Engine
        self.put("guest:claimnw")
        storage.migrate("guest:claimnw", "101:2")
        self.assertIsNone(storage.read("guest:claimnw"))
        self.assertEqual(handler.save_summary("101:2")["place"], "北京")
        with self.assertRaises(VendorCmdError):
            storage.migrate("101:2", "101:2")
        engine = object.__new__(Engine)
        engine.player = "101:2"
        self.assertTrue(storage.delete("101:2"))
        engine.merge_poster({"id": 1}, "test-generation", b"not-used")
        self.assertIsNone(storage.read("101:2"))

    def test_poster_merge_preserves_new_replies_and_rejects_old_generation(self):
        from nowhere_adapter.worker import Engine
        data = archive()
        card = copy.deepcopy(data["files"]["postcards.json"]["items"][0])
        data["files"]["postcards.json"]["items"][0]["replies"] = ["稍后来的回复"]
        self.put("101", data)
        engine = object.__new__(Engine)
        engine.player = "101"
        engine.merge_poster(card, "old-generation", b"not-used")
        self.assertEqual(storage.read("101")["images"], {})
        engine.merge_poster(card, "test-generation", b"\x89PNG\r\n\x1a\nfixture")
        saved = storage.read("101")
        self.assertEqual(saved["files"]["postcards.json"]["items"][0]["replies"], ["稍后来的回复"])
        self.assertIn("postcards/card_1.png", saved["images"])


class SharedTests(TemporaryStores):
    def test_upstream_footprint_distance_age_probability_and_archive_rules(self):
        a, b = travelers_module(), travelers_module()
        shared.install(a, "101", lambda: {"traveler_name": "旅者甲"})
        shared.install(b, "202:2", lambda: {"traveler_name": "旅者乙"})
        a.register("ignored", "北京", 39.9, 116.4)
        a.record_footprint("ignored", 39.9, 116.4, "北京")
        b.register("ignored", "北京", 39.9, 116.4)
        now = datetime.now(timezone.utc)
        class FixedTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        class FixedChance(random.Random):
            def __init__(self, chance):
                super().__init__(1)
                self.chance = chance
            def random(self):
                return self.chance
        def read(chance=.15, km=0):
            lat = 39.9 + km / (6371 * 3.141592653589793 / 180)
            return b.check_footprints("ignored", lat, 116.4, FixedChance(chance), {})
        def age(hours):
            with shared.transaction() as db:
                data = shared.get(db, "travelers.json")
                data["101"]["footprints"][0]["at"] = (now-timedelta(hours=hours)).isoformat()
                shared.put(db, "travelers.json", data)
        with patch.object(b, "datetime", FixedTime):
            self.assertIsNone(read(chance=.150001))
            self.assertIsNotNone(read(km=2.999))
            self.assertIsNone(read(km=3.001))
            age(24)
            self.assertIsNotNone(read())
            age(24.001)
            self.assertIsNone(read())
            # Upstream deliberately gives archived footprints no age cutoff.
            with shared.transaction() as db:
                data = shared.get(db, "travelers.json")
                shared.put(db, "travelers_archive.json", {"101": data.pop("101")})
                shared.put(db, "travelers.json", data)
            self.assertIsNotNone(read())
            self.assertIsNone(read(km=3.001))

    def test_same_names_use_distinct_stable_ids_and_persistent_cooldown(self):
        a, b = travelers_module(), travelers_module()
        shared.install(a, "101", lambda: {"traveler_name": "同名"})
        shared.install(b, "202:2", lambda: {"traveler_name": "同名"})
        a.register("opening-name", "北京", 39.9, 116.4)
        b.register("different-walking-name", "北京", 39.9, 116.4)
        a.record_footprint("anything", 39.9, 116.4, "北京")
        with shared.transaction() as db:
            registry = shared.get(db, "travelers.json")
        self.assertEqual(set(registry), {"101", "202:2"})
        self.assertEqual(len(registry["101"]["footprints"]), 1)
        first = a.check_meeting("untrusted", 39.9, 116.4, random.Random(1), {})
        self.assertIsNotNone(first[0])
        restarted = travelers_module()
        shared.install(restarted, "101", lambda: {})
        self.assertEqual(restarted.check_meeting("nickname", 39.9, 116.4, random.Random(1), {}), (None, None))
        self.assertEqual(b.check_meeting("nickname", 39.9, 116.4, random.Random(1), {}), (None, None))
        # Seed 1 draws 0.134..., inside upstream's 15% footprint probability.
        for _ in range(3):
            text = b.check_footprints("nickname", 39.9, 116.4, random.Random(1), {})
            self.assertIsNotNone(text)
        self.assertIn("同名", text)
        with shared.transaction() as db:
            self.assertEqual(shared.get(db, "counts")["202:2|101"], 3)
            serialized = json.dumps(shared.get(db, "travelers.json"))
        for private in ("postcards", "messages", "journal", "notebook", "marks"):
            self.assertNotIn(private, serialized)

    def test_cross_process_shared_writes_and_solo(self):
        code = '''
import sys,importlib.util
from nowhere_adapter import shared
spec=importlib.util.spec_from_file_location('travelers',sys.argv[1]); t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
shared.install(t,sys.argv[2],lambda: {'traveler_name':'同名'})
t.register('ignored','北京',39.9,116.4)
for i in range(12): t.record_footprint('ignored',39.9,116.4,'北京')
'''
        procs = [subprocess.Popen([sys.executable, "-c", code, str(VENDOR / "nowhere/travelers.py"), str(i)], cwd=ROOT) for i in range(1, 5)]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=30), 0)
        with shared.transaction() as db:
            registry = shared.get(db, "travelers.json")
        self.assertEqual(len(registry), 4)
        self.assertTrue(all(len(entry["footprints"]) == 12 for entry in registry.values()))
        solo = travelers_module()
        shared.install(solo, "5", lambda: {"cotraveler": "0"})
        solo.register("same", "巴黎", 1, 2)
        shared.forget("1")
        with shared.transaction() as db:
            self.assertEqual(set(shared.get(db, "travelers.json")), {"2", "3", "4"})


class PlatformTests(TemporaryStores):
    def test_platform_claim_conflict_and_confirmed_slot_delete(self):
        self.stack.enter_context(patch.object(server, "_camping_plaza_save_summary", return_value=None))
        self.put("guest:platformclaim")
        found, conflicts = server._collect_player_saves("guest:platformclaim", "101:2")
        self.assertIn("vendor:nowhere", found)
        self.assertEqual(conflicts, [])
        result = server._migrate_player_saves("guest:platformclaim", 101, slot=2)
        self.assertIn("vendor_saves/nowhere", result["migrated"])
        self.put("guest:conflict")
        with self.assertRaises(server._McpError):
            server._migrate_player_saves("guest:conflict", 101, slot=2)
        self.assertIsNotNone(storage.read("guest:conflict"))
        self.stack.enter_context(patch.object(server, "_current_account", return_value={"id": 101, "is_ai": True}))
        with self.assertRaises(server._McpError):
            server._delete_save({"game": "nowhere", "slot": 2}, "fake-token")
        server._delete_save({"game": "nowhere", "slot": 2, "confirm": True}, "fake-token")
        self.assertIsNone(storage.read("101:2"))

    def test_my_saves_and_public_stats_use_private_envelope_summary(self):
        self.put("101", archive("北京"))
        self.put("101:2", archive("巴黎"))
        self.stack.enter_context(patch.object(server, "_account_slot_player_ids", return_value=[("101", 1), ("101:2", 2)]))
        self.stack.enter_context(patch.object(server, "_turtle_soup_stats", return_value={}))
        self.stack.enter_context(patch.object(server, "_camping_plaza_save_summary", return_value=None))
        for name in server.VENDOR_GAMES:
            adapter = getattr(server, name + "_adapter", None)
            if name != "nowhere" and adapter and hasattr(adapter, "save_summary"):
                self.stack.enter_context(patch.object(adapter, "save_summary", return_value=None))
        @contextmanager
        def connect():
            db = sqlite3.connect(self.root / "empty-accounts.db")
            try: yield db
            finally: db.close()
        self.stack.enter_context(patch.object(server, "_db_connect", side_effect=connect))
        result = server._account_saves_for_user({"id": 101, "username": "fixture", "is_ai": True}, migrate_legacy=False)
        self.assertEqual([s["place"] for s in result["saves"]["nowhere"]["slots"]], ["北京", "巴黎"])
        self.assertEqual(server._vendor_save_stats("nowhere")["save_count"], 2)

    def test_source_manifest_matches_all_actual_decorated_mcp_tools(self):
        tree = ast.parse((VENDOR / "nowhere/server.py").read_text())
        registered = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and any(ast.unparse(d) == "mcp.tool()" for d in node.decorator_list)}
        manifest = {item["name"]: item for item in handler.schema() if item.get("source") == "upstream_mcp"}
        self.assertEqual(set(registered), set(manifest))
        self.assertEqual(len(manifest), 28)
        for name, node in registered.items():
            self.assertEqual(set(manifest[name]["inputSchema"]["properties"]), {a.arg for a in node.args.args})

    def test_catalog_guide_schema_and_identity_registration(self):
        self.assertIn("nowhere·乌有乡", server._tool_list_games())
        self.assertIn("@留言未接通", server._tool_get_guide({"game": "nowhere"}))
        for collection in (server.IDENTITY_GAMES, server.PERSISTENT_SAVE_GAMES, server.VENDOR_GAMES, server.ANTI_ADDICTION_MINI_GAMES):
            self.assertIn("nowhere", collection)
        self.assertIn("send_postcard", {x["name"] for x in handler.play({"player_id": "101", "action": "schema"})["actions"]})
        for ua in ("", "Kelivo/1", "Dart/3.9 (dart:io)", "ktor-client/3.0"):
            tool = next(x for x in server._root_tools(ua) if x["name"] == "play")
            params = tool["inputSchema"]["properties"]["params"]
            self.assertIs(params["additionalProperties"], True)
            if ua:
                self.assertIn("distance_km", params["properties"])
            else:
                self.assertEqual(set(params["properties"]), {"slot", "command"})

    def test_guide_and_schema_explain_say_and_send_postcard(self):
        guide = json.loads(server._tool_get_guide({"game": "nowhere"}))["guide"]
        actions = {item["name"]: item for item in handler.play({"player_id": "101", "action": "schema"})["actions"]}
        for action, phrases in {
            "say": ("旅者", "当前旅程", "保存", "本旅程的“原话”", "quotes", "回看"),
            "send_postcard": ("寄一张", "绑定人类", "该存档", "人类旁观页明信片墙", "查看和回复"),
        }.items():
            guide_line = next(line for line in guide.splitlines() if line.startswith(f"- `{action}(text)`："))
            for source, description in (("guide", guide_line), ("schema", actions[action]["description"])):
                with self.subTest(action=action, source=source):
                    for phrase in phrases:
                        self.assertIn(phrase, description)

    def test_root_mcp_path_and_bearer_override_forged_identity_and_slots(self):
        patches = [patch.object(server, "_current_account", return_value={"id": 101, "is_ai": True, "username": "test"}),
                   patch.object(server, "_authenticated_ai_player_id", return_value=None),
                   patch.object(server, "_duel_unread_request_reminder", return_value=""),
                   patch.object(server, "_mcp_forced_announcement", return_value=""),
                   patch.object(server, "_stamp_save_owner"),
                   patch.object(server, "_finalize_play_response", side_effect=lambda response, **kw: response)]
        for p in patches:
            self.stack.enter_context(p)
        for auth in ({"path_token": "test-token"}, {"bearer_token": "test-token"}):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "play", "arguments": {
                "game": "nowhere", "action": "schema", "player_id": "202", "params": {"player_id": "202:2", "slot": 2}}}}
            with patch.object(handler, "play", side_effect=lambda args: {"player_id": args["player_id"]}) as called:
                response = server._handle_root_mcp(request, **auth)
                self.assertFalse(response.get("result", {}).get("isError"), response)
                self.assertEqual(called.call_args.args[0]["player_id"], "101:2")


class WebTests(TemporaryStores):
    def test_radio_document_uses_cookie_slot_and_same_origin_media(self):
        from html.parser import HTMLParser
        from urllib.parse import quote
        self.put("101:2")
        stream = 'https://radio.example.test/fip.mp3?x="quoted"&y=1'
        data = archive()
        data['files']['footprints.json'] = {'items': [{'stream_url': stream}]}
        self.put('101:2', data)
        with patch('socket.socket.connect', side_effect=AssertionError('document must not fetch audio')), patch.object(handler, 'execute') as execute:
            result = self.call('/nowhere/radio?player=101%3A2&url='+quote(stream, safe=''), token='',
                               extra_headers={'Cookie':'nowhere_token=test-human'})
            execute.assert_not_called()
        self.assertEqual(result.status,200)
        self.assertNotIn('Location',result.sent_headers)
        self.assertNotIn('Set-Cookie',result.sent_headers)
        self.assertEqual(result.sent_headers['Content-Type'],'text/html; charset=utf-8')
        class Tags(HTMLParser):
            def __init__(self): super().__init__(); self.tags = []
            def handle_starttag(self, tag, attrs): self.tags.append((tag,dict(attrs)))
        parsed = Tags(); parsed.feed(result.wfile.getvalue().decode())
        audio = [attrs for tag,attrs in parsed.tags if tag=='audio']
        self.assertEqual(len(audio),1)
        self.assertEqual(audio[0]['src'],'/nowhere/radio/stream?player=101%3A2&url='+quote(web.radio.canonical_url(stream),safe=''))
        self.assertTrue({'controls','autoplay','playsinline'} <= audio[0].keys())
        self.assertEqual([a['href'] for tag,a in parsed.tags if tag=='a'],['/nowhere/?player=101%3A2'])
        self.assertNotIn('script',[tag for tag,_ in parsed.tags])

    def test_stream_checks_cookie_binding_slot_and_private_membership_before_network(self):
        from nowhere_adapter import radio
        from urllib.parse import quote
        stream='https://radio.example.test/live'
        data=archive();data['files']['footprints.json']={'items':[{'stream_url':stream}]}
        self.put('101:2',data);self.put('101')
        with patch.object(radio,'open_stream',side_effect=AssertionError('must reject before network')):
            for player,token,status in [('101:2','',401),('202','test-human',403),('101','test-human',403)]:
                response=self.call('/nowhere/radio/stream?player='+player+'&url='+quote(stream,safe=''),token=token)
                self.assertEqual(response.status,status)
            response=self.call('/nowhere/radio/stream?player=101:2&url=https://unlisted.test/stream',token='',extra_headers={'Cookie':'nowhere_token=test-human','Host':'127.0.0.1'})
            self.assertEqual(response.status,403)
        with patch.object(radio,'open_stream',side_effect=radio.RadioError('upstream unavailable',502)) as opened:
            response=self.call('/nowhere/radio/stream?player=101:2&url='+quote(stream,safe=''),token='',extra_headers={'Cookie':'nowhere_token=test-human'})
            self.assertEqual(response.status,502)
            opened.assert_called_once_with(stream)

    def test_history_radio_normalization_is_shared_by_both_routes(self):
        from urllib.parse import quote
        from tests_toy.test_nowhere_radio import historical_archive, upstream_footprints
        data=historical_archive()
        self.put('101:2',data);self.put('101');self.put('202')
        items=upstream_footprints(data)
        stored=next(f['stream_url'] for f in items if f.get('station',{}).get('name')=='FIP')
        request=web.radio.canonical_url(stored)+'#browser-fragment'
        page=self.call('/nowhere/radio?player=101:2&url='+quote(request,safe=''))
        self.assertEqual(page.status,200)
        with patch.object(web.radio,'open_stream',side_effect=web.radio.RadioError('test-source',502)) as opened:
            response=self.call('/nowhere/radio/stream?player=101:2&url='+quote(request,safe=''))
            self.assertEqual(response.status,502)  # Authorization passed; fake source failed.
            opened.assert_called_once_with(web.radio.canonical_url(stored))
        for path in ('/radio','/radio/stream'):
            for player in ('101','202'):
                with patch.object(web.radio,'open_stream') as opened:
                    self.assertEqual(self.call('/nowhere'+path+'?player='+player+'&url='+quote(request,safe='')).status,403)
                    opened.assert_not_called()

    def test_radio_rejects_bad_urls_and_http_context(self):
        from urllib.parse import quote
        self.put('101:2')
        for url in ('javascript:alert(1)','data:audio/mp3,bad','file:///tmp/test','ftp://host/file',
                    '//host/file','https://','https://user:password@host/file','https://host:bad/file',
                    'https://host/\\evil','https://host/\nfile','https://host/'+'a'*4096):
            with self.subTest(url=url[:50]):
                response=self.call('/nowhere/radio?player=101:2&url='+quote(url,safe=''))
                self.assertEqual(response.status,400)
                self.assertNotIn('Location',response.sent_headers)
        data = archive()
        data['files']['footprints.json'] = {'items': [{'stream_url': scheme+'://radio.example.test/stream'} for scheme in ('http','https')]}
        self.put('101:2', data)
        for scheme in ('http','https'):
            self.assertEqual(self.call('/nowhere/radio?player=101:2&url='+quote(scheme+'://radio.example.test/stream')).status,200)
        self.assertEqual(self.call('/nowhere/radio?player=101:2&url=https://radio.test/fip',extra_headers={'X-Forwarded-Proto':'http'}).status,426)
        self.assertEqual(self.call('/nowhere/radio?player=101:2&url=https://radio.test/fip',method='POST').status,404)

    def test_main_page_retains_upstream_markup_with_security_bridge_and_radio_hint(self):
        original = (web.STATIC / "index.html").read_text()
        rendered = web.render_page().decode()
        self.assertNotIn("platform-credit", rendered)
        # An exact document comparison limits the adapter to these security
        # escapes, radio hint, bridge assets and hidden error-only status element.
        restored = rendered.replace('<link rel="stylesheet" href="/nowhere/platform.css">\n<script src="/nowhere/platform.js"></script>', '')
        restored = restored.replace('<div id="platform-status" role="status" aria-live="polite" hidden></div>', '')
        restored = restored.replace('${escapeHtml(frontImg)}', '${frontImg}')
        restored = restored.replace('${escapeHtml(window.nowhereRadioUrl(streamUrl))}', '${escapeHtml(streamUrl)}')
        restored = restored.replace('电台流 ↗</a>`:f.stream_unavailable===true?`<br><span class="traillink">${station?escapeHtml(station)+" 电台流":"该电台流"}已失效，可让小机再次 listen 寻找附近其他可用电台。</span>`:""}</div>', '电台流 ↗</a>`:""}</div>')
        restored = restored.replace('${esc(c.sent_at?localDateTime(c.sent_at):"旧明信片未记录")}', '${c.sent_at?localDateTime(c.sent_at):"旧明信片未记录"}')
        restored = restored.replace('${esc(st.elevation!=null?"海拔 "+st.elevation+"m":"")} ${esc(st.weather||"")}', '${st.elevation!=null?"海拔 "+st.elevation+"m":""} ${st.weather||""}')
        restored = restored.replace('function safeHttpUrl(value){\n  if(typeof value!=="string"||!/^https?:\\/\\//i.test(value))return "";', 'function safeHttpUrl(value){')
        self.assertNotIn("nowhereLinkRadio", rendered)
        self.assertNotIn("nowhereLinkRadio", (web.ASSETS / "platform.js").read_text())
        self.assertEqual(restored, original)
        css = (web.ASSETS / "platform.css").read_text()
        self.assertTrue(all(line.startswith(('.platform-access', '#platform-status')) for line in css.splitlines()))

    def test_radio_anchors_preserve_upstream_new_window_target(self):
        source = web.STATIC / "index.html"
        before = source.read_bytes()
        original = before.decode()
        rendered = web.render_page().decode()
        radio = '<a class="traillink" href="${escapeHtml(streamUrl)}" target="_blank" rel="noopener noreferrer">'
        self.assertEqual(original.count(radio), 1)
        self.assertIn(radio.replace('escapeHtml(streamUrl)', 'escapeHtml(window.nowhereRadioUrl(streamUrl))'), rendered)
        import re
        anchors = lambda text: re.findall(r'<a\s[^>]*>', text)
        self.assertEqual(anchors(original), anchors(rendered.replace('escapeHtml(window.nowhereRadioUrl(streamUrl))', 'escapeHtml(streamUrl)')))
        self.assertEqual(source.read_bytes(), before)

    def setUp(self):
        super().setUp()
        self.db = self.root / "accounts.db"
        with sqlite3.connect(self.db) as db:
            db.executescript('''CREATE TABLE toy_users(id INTEGER, username TEXT,is_ai INTEGER,deleted_at TEXT);
CREATE TABLE user_bindings(human_user_id INTEGER,ai_user_id INTEGER);
INSERT INTO toy_users VALUES (101,'自己的小机',1,NULL),(202,'别人小机',1,NULL);
INSERT INTO user_bindings VALUES (1,101);''')
        @contextmanager
        def connect():
            db = sqlite3.connect(self.db)
            db.row_factory = sqlite3.Row
            try: yield db
            finally: db.close()
        self.stack.enter_context(patch.object(server, "_db_connect", side_effect=connect))
        def account(token):
            if token != "test-human":
                raise server._McpError(-32001, "需要登录")
            return {"id": 1, "is_ai": False}
        self.stack.enter_context(patch.object(server, "_current_account", side_effect=account))

    def call(self, path, method="GET", body=None, token="test-human", extra_headers=None):
        h = object.__new__(server.CedarToyHandler)
        h.path, h.command = path, method
        raw = json.dumps(body).encode() if body is not None else b""
        h.headers = {"Host": "nowhere.test", "X-Forwarded-Proto": "https", "Content-Length": str(len(raw)), **(extra_headers or {})}
        if token: h.headers["Authorization"] = "Bearer " + token
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.sent_headers = {}
        h.send_response = lambda code: setattr(h, "status", code)
        h.send_header = lambda k,v: h.sent_headers.update({k:v})
        h.end_headers = lambda: None
        web.serve(h, server)
        return h

    def test_every_private_route_denies_another_account_missing_auth_and_unbound(self):
        routes = [("/state", "GET"), ("/history", "GET"), ("/postcards", "GET"), ("/message", "POST"),
                  ("/postcard/1/reply", "POST"), ("/postcard/1", "DELETE"),
                  ("/static/postcards/card_1.png", "GET"), ("/radio", "GET"), ("/radio/stream", "GET"), ("/", "GET")]
        with patch.object(handler, "execute") as execute:
            for path, method in routes:
                for player, token, expected in (("202", "test-human", 403), ("101", "", 401), ("101:6", "test-human", 403)):
                    with self.subTest(path=path, player=player, token=bool(token)):
                        result = self.call("/nowhere" + path + "?player=" + player, method, {"content": "x"}, token)
                        self.assertEqual(result.status, expected)
            execute.assert_not_called()
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM user_bindings")
        self.assertEqual(self.call("/nowhere/state?player=101").status, 403)

    def test_page_redirect_cookie_and_original_assets(self):
        result = self.call("/nowhere/?player=101:2&token=test-human", token="")
        self.assertEqual(result.status, 303)
        self.assertEqual(result.sent_headers["Location"], "/nowhere/?player=101%3A2")
        for expected in ("HttpOnly", "SameSite=Strict", "Path=/nowhere/", "Secure"):
            self.assertIn(expected, result.sent_headers["Set-Cookie"])
        self.put("101:2")
        result = self.call("/nowhere/?player=101:2")
        self.assertEqual(result.status, 200)
        for part in (b'world110m.js', b'platform.js', b'id="mapwrap"'):
            self.assertIn(part, result.wfile.getvalue())
        self.assertEqual(self.call("/nowhere/static/world110m.js", token="").status, 200)
        self.assertEqual(self.call("/nowhere/static/../server.py?player=101").status, 404)

    def test_http_document_does_not_exchange_or_forward_credentials(self):
        with patch.object(server, "_current_account") as account:
            result = self.call("/nowhere/?player=101&token=test-human", extra_headers={"X-Forwarded-Proto":"http"})
            account.assert_not_called()
        self.assertEqual(result.status,426)
        self.assertNotIn("Set-Cookie",result.sent_headers)
        self.assertNotIn("Location",result.sent_headers)
        self.assertIn(b'text/html',result.sent_headers["Content-Type"].encode())
        self.assertNotIn(b'test-human',result.wfile.getvalue())

    def test_missing_expired_and_unbound_document_has_recovery_page(self):
        result = self.call("/nowhere/?player=101", token="")
        self.assertEqual(result.status,401)
        self.assertIn(b'access-home',result.wfile.getvalue())
        with patch.object(server,"_current_account",side_effect=ValueError("MCP SECRET ADVICE")):
            for path in ("/", "/state", "/static/postcards/card_1.png"):
                result = self.call("/nowhere"+path+"?player=101")
                self.assertEqual(result.status,401)
                self.assertNotIn(b'MCP SECRET ADVICE',result.wfile.getvalue())
        result = self.call("/nowhere/?player=202")
        self.assertEqual(result.status,403)
        self.assertIn(b'access-home',result.wfile.getvalue())

    def test_bearer_document_sets_same_secure_cookie_without_url_token(self):
        self.put("101:2")
        result = self.call("/nowhere/?player=101:2")
        self.assertEqual(result.status,200)
        for flag in ('Secure','HttpOnly','SameSite=Strict','Path=/nowhere/'):
            self.assertIn(flag,result.sent_headers['Set-Cookie'])
        self.assertNotIn('Location',result.sent_headers)

    def test_rejected_write_cannot_poison_the_next_keepalive_request(self):
        result = self.call('/nowhere/postcard/1/reply?player=202', 'POST', {'content':'denied'})
        self.assertEqual(result.status,403)
        self.assertTrue(result.close_connection)
        self.assertEqual(result.sent_headers['Connection'],'close')

    def test_write_rejects_spoofed_identity_cross_site_and_unconfirmed_delete(self):
        for body in ({"player_id": "202"}, {"slot": 2}, {"player": "202"}):
            self.assertEqual(self.call("/nowhere/message?player=101", "POST", body).status, 403)
        self.assertEqual(self.call("/nowhere/message?player=101", "POST", {}, extra_headers={"Origin": "https://attacker.test"}).status, 403)
        self.assertEqual(self.call("/nowhere/postcard/1?player=101", "DELETE").status, 400)
        with patch.object(handler, "execute", return_value={"status": 200, "body": {"ok": True}}) as execute:
            self.assertEqual(self.call("/nowhere/postcard/1/reply?player=101:2", "POST", {"content": "回信"}).status, 200)
            self.assertEqual(execute.call_args.args[0], "101:2")


@unittest.skipUnless(os.environ.get("NOWHERE_REAL_TEST") == "1", "requires installed Python >=3.11 + real upstream dependencies; set NOWHERE_REAL_TEST=1")
class RealEngineTests(TemporaryStores):
    def tearDown(self):
        handler.POOL.close()
        super().tearDown()

    def test_real_walks_publish_shared_footprints_and_solo_publishes_nothing(self):
        a, b = "guest:footprinta", "guest:footprintb"
        def play(player, action, **params):
            return handler.play({"player_id": player, "action": action, **params})
        for player in (a, b):
            play(player, "open_door", to="北京", cotraveler="1", traveler_name="同名旅者")
        for step in (1, 2):
            play(a, "walk", direction="N", distance_km=.1)
            with shared.transaction() as db:
                entry = shared.get(db, "travelers.json")[a]
            self.assertEqual(len(entry["footprints"]), step)
            self.assertEqual(entry["display_name"], "同名旅者")
        # Read the real worker's recorded footprint through another identity's
        # actual upstream rules, with a deterministic probability draw.
        reader = travelers_module()
        shared.install(reader, b, lambda: {"traveler_name": "同名旅者"})
        fp = entry["footprints"][-1]
        self.assertIsNotNone(reader.check_footprints("untrusted", fp["lat"], fp["lon"], random.Random(1), {}))
        with shared.transaction() as db:
            self.assertEqual(shared.get(db, "counts")[b+"|"+a], 1)
            self.assertIn("|".join(sorted([a,b])), shared.get(db, "meetings"))
        play(a, "send_postcard", text="PRIVATE-FOOTPRINT-POSTCARD")
        play(a, "say", text="PRIVATE-FOOTPRINT-JOURNAL")
        with shared.transaction() as db:
            shared_text = json.dumps(db.execute('SELECT * FROM shared').fetchall())
        self.assertNotIn("PRIVATE-FOOTPRINT", shared_text)
        play(a, "walk_alone")
        handler.POOL.close()
        play(a, "continue_journey")
        play(a, "walk", direction="N", distance_km=.1)
        self.assertTrue(storage.read(a)["files"]["journey.json"]["cotraveler_alone"])
        with shared.transaction() as db:
            self.assertNotIn(a, shared.get(db, "travelers.json"))
            self.assertNotIn(a, shared.get(db, "travelers_archive.json"))
        self.assertIsNone(reader.check_footprints("untrusted", fp["lat"], fp["lon"], random.Random(1), {}))

    def test_solo_survives_cold_and_warm_resume_but_new_door_resets_it(self):
        player = "guest:soloresume"
        def play(action, **params):
            return handler.play({"player_id": player, "action": action, **params})
        def assert_solo():
            files = storage.read(player)["files"]
            self.assertTrue(files["journey.json"]["cotraveler_alone"])
            journeys = [value for name, value in files.items()
                        if name.startswith("journeys/") and "pos" in value]
            self.assertTrue(journeys)
            self.assertTrue(all(value["cotraveler_alone"] for value in journeys))
            with shared.transaction() as db:
                self.assertNotIn(player, shared.get(db, "travelers.json"))
                self.assertNotIn(player, shared.get(db, "travelers_archive.json"))
        play("open_door", to="北京", cotraveler="1")
        play("walk_alone")
        assert_solo()
        handler.POOL.close()
        play("continue_journey")
        assert_solo()
        play("continue_journey")
        assert_solo()
        self.assertEqual(storage.read(player)["runtime"]["metadata"]["cotraveler"], "1")
        play("open_door", to="北京 斯", confirm=True)
        self.assertFalse(storage.read(player)["files"]["journey.json"]["cotraveler_alone"])
        with shared.transaction() as db:
            self.assertIn(player, shared.get(db, "travelers.json"))

    def test_open_walk_cold_continue_and_full_real_mcp_registry(self):
        player = "guest:realnowhere"
        handler.play({"player_id": player, "action": "open_door", "to": "北京", "cotraveler": "0"})
        before = storage.read(player)
        handler.play({"player_id": player, "action": "walk", "direction": "N", "distance_km": .1})
        moved = storage.read(player)
        self.assertNotEqual(before["files"]["journey.json"]["pos"], moved["files"]["journey.json"]["pos"])
        handler.POOL.close()
        handler.play({"player_id": player, "action": "continue_journey"})
        self.assertEqual(storage.read(player)["files"]["journey.json"]["pos"], moved["files"]["journey.json"]["pos"])
        handler.play({"player_id": player, "action": "send_postcard", "text": "跨进程明信片"})
        data = handler.play({"player_id": player, "action": "export"})["save_data"]
        handler.play({"player_id": "101:2", "action": "import", "save_data": data})
        self.assertEqual(handler.save_summary("101:2")["postcards"], 1)


if __name__ == "__main__":
    unittest.main()
