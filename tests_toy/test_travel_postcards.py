import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import account_deletion
import server
from cedar_backend import travel_postcards
from tests_toy import test_account_rename


class TravelPostcardTests(unittest.TestCase):
    def setUp(self):
        self.accounts = test_account_rename.AccountRenameTests()
        self.accounts.setUp()
        self.addCleanup(self.accounts.tearDown)
        with self.accounts._connect() as conn:
            account_deletion.init_schema(conn)
        root = Path(self.accounts.temp_dir.name) / "saves"
        self.root = root / "travel"
        save_root = patch.object(server, "VENDOR_SAVE_ROOT", root)
        save_root.start()
        self.addCleanup(save_root.stop)
        self.human = str(self.accounts.human_id)
        self.ai = str(self.accounts.machine_id)
        self.other = str(self.accounts.other_machine_id)

    def save(self, player, rows):
        path = self.root / player / "postcards.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return path

    def request(self, path, token=None):
        handler = object.__new__(server.CedarToyHandler)
        handler.command, handler.path = "GET", path
        handler.headers = {"Authorization": "Bearer " + (self.accounts.human_token if token is None else token)}
        handler.send_header, handler.end_headers, handler.send_response = Mock(), Mock(), Mock()
        handler.wfile = io.BytesIO()
        handler.do_GET()
        headers = dict(call.args for call in handler.send_header.call_args_list)
        body = handler.wfile.getvalue()
        if headers["Content-Type"].startswith("application/json"):
            body = json.loads(body)
        return handler.send_response.call_args.args[0], headers, body

    def history(self, player=None, token=None, expected=200):
        path = "/api/travel/postcards"
        if player is not None:
            path += "?player=" + quote(player, safe="")
        status, headers, result = self.request(path, token)
        self.assertEqual(status, expected)
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        return result

    def test_history_multiple_owners_slots_and_original_art(self):
        with self.accounts._connect() as conn:
            conn.execute("INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                         (self.accounts.human_id, self.other))
        old = {"id": "pc-before", "trip_id": "earlier", "line": "很久以前\n<b>原文</b>",
               "dest_id": "paris", "spot_id": "paris-1", "at": "2025-07-25T23:59:59+08:00",
               "image_url": "assets/postcards/paris-1.jpg"}
        recent = {**old, "trip_id": "later", "line": "回家见", "at": "2026-11-27T00:01:00+08:00"}
        path = self.save(self.ai, [old, recent])
        self.save(self.ai + ":2", [{**old, "line": "第二槽"}])
        self.save(self.other + ":5", [{**old, "line": "另一只小机"}])
        self.save(self.human + ":3", [{**old, "line": "自己的明信片"}])
        self.save(str(self.accounts.other_human_id), [old])
        self.save("guest:travel-test", [old])
        before = path.stat()
        slots = self.history()["slots"]
        self.assertEqual({s["player"] for s in slots}, {self.ai, self.ai + ":2", self.other + ":5", self.human + ":3"})
        self.assertEqual(next(s for s in slots if s["player"] == self.human + ":3")["owner"], "我")
        cards = self.history(self.ai)["postcards"]
        self.assertEqual([c["message"] for c in cards], [recent["line"], old["line"]])
        self.assertEqual((cards[1]["destination"], cards[1]["date"]), ("巴黎", "2025-07-25"))
        status, headers, art = self.request(cards[1]["image"])
        self.assertEqual((status, headers["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(art, (travel_postcards.UPSTREAM / old["image_url"]).read_bytes())
        for player, message in [(self.human + ":3", "自己的明信片"), (self.ai + ":2", "第二槽"),
                                (self.other + ":5", "另一只小机")]:
            self.assertEqual([c["message"] for c in self.history(player)["postcards"]], [message])
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_authentication_scope_and_live_unbind(self):
        self.save(self.ai, [])
        for token, code in [("", 401), ("invalid", 401), (self.accounts.machine_token, 403)]:
            self.history(None, token, code)
            self.history(self.ai, token, code)
        for player in [self.other, str(self.accounts.other_human_id), self.ai + ":0", self.ai + ":6",
                       self.ai + ":1", self.ai + ":02", "guest:travel-test", "../outside", ""]:
            self.history(player, expected=403)
        for query in [f"player_id={self.other}", f"player={self.ai}&player={self.other}"]:
            self.assertEqual(self.request("/api/travel/postcards?" + query)[0], 400)
        other_token = self.accounts._token_for(self.accounts.other_human_id)
        self.assertEqual(self.history(None, other_token)["slots"], [])
        self.history(self.ai, other_token, 403)
        with self.accounts._connect() as conn:
            conn.execute("DELETE FROM user_bindings WHERE ai_user_id=?", (self.ai,))
        self.history(self.ai, expected=403)
        self.assertEqual(self.history()["slots"], [])

    def test_missing_empty_unreadable_and_unsafe_files(self):
        self.assertEqual(self.history()["slots"], [])
        self.assertEqual(self.history(self.human)["postcards"], [])
        self.assertFalse(self.root.exists())
        path = self.save(self.human, [])
        self.assertEqual(self.history(self.human)["postcards"], [])
        path.write_bytes(b"{broken")
        self.history(self.human, expected=500)
        self.assertEqual(path.read_bytes(), b"{broken")
        foreign = self.save(self.other, [{"line": "private"}])
        path.unlink()
        path.symlink_to(foreign)
        (self.root / self.ai).symlink_to(foreign.parent, target_is_directory=True)
        self.assertEqual(self.history()["slots"], [])
        self.history(self.human, expected=403)
        self.history(self.ai, expected=403)

    def test_image_paths_are_local_originals_only(self):
        card = {"dest_id": "paris", "at": "2025-07-25", "line": "寄语一直可见"}
        inputs = ["https://example.org/image.jpg", "assets/postcards/../data.jpg",
                  "assets/postcards/missing.jpg", "assets/souvenirs_local/paris.jpg"]
        self.save(self.human, [{**card, "image_url": value} for value in inputs])
        cards = self.history(self.human)["postcards"]
        self.assertEqual(len(cards), len(inputs))
        self.assertTrue(all(c["image"] is None and c["message"] == card["line"] for c in cards))
        for name in ["../data/destinations.json", "paris-1.jpg/other.jpg", "%2e%2e%2fsecret.jpg"]:
            self.assertEqual(self.request(travel_postcards.ART_PREFIX + name)[0], 404)
        self.assertEqual(self.request(travel_postcards.ART_PREFIX + quote("lençois-maranhenses-1.jpg"))[0], 200)
