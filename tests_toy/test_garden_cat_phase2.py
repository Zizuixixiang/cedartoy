"""Garden-Cat phase-2 acceptance tests; no service process is started."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import urllib.parse
from unittest.mock import patch
import unittest
from pathlib import Path


TOY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = Path(os.environ.get("GARDEN_CAT_ENGINE_ROOT", TOY_ROOT / "vendor" / "Garden-Cat-Engine"))
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(TOY_ROOT))


class GardenCatPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="garden-cat-phase2-")
        os.environ["GARDEN_CAT_SAVE_ROOT"] = self.tempdir.name
        import game_api

        self.game_api = importlib.reload(game_api)
        self.game_api.NOTE_DB_PATH = str(Path(self.tempdir.name) / "notes.db")
        self.client = self.game_api.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def _put_state(self, player_id, mutate):
        store = self.game_api.STORE
        with store.player_state(player_id) as state:
            mutate(state)
            store.save(player_id, state)

    def _read_state(self, player_id):
        with self.game_api.STORE.player_state(player_id) as state:
            return json.loads(json.dumps(state, ensure_ascii=False))

    def test_forged_browser_identity_is_rejected_or_ignored(self):
        # A body/query claim is not identity when the trusted header is missing.
        denied = self.client.post(
            "/web/cmd?player_id=victim",
            json={"command": "water 1", "player_id": "victim", "session_id": "victim"},
        )
        self.assertEqual(denied.status_code, 400)

        def planted(state):
            state["pots"][0] = {
                "flower_id": "daisy",
                "planted_time": int(time.time()),
                "watered": False,
                "growth_progress": 0.0,
                "last_growth_update": int(time.time()),
            }

        self._put_state("trusted", planted)
        self._put_state("victim", planted)
        accepted = self.client.post(
            "/web/cmd",
            headers={"X-Player-Id": "trusted"},
            json={"command": "water 1", "player_id": "victim", "session_id": "victim"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(self._read_state("trusted")["pots"][0]["watered"])
        self.assertFalse(self._read_state("victim")["pots"][0]["watered"])

    def test_authenticated_names_flow_through_dispatch_and_web_proxy(self):
        import server
        from cedar_backend.satellite_proxy import garden_cat_request_headers

        class Response:
            def __init__(self, response):
                self.status_code = response.status_code
                self.payload = response.get_json()

            def json(self):
                return self.payload

        def forward(method, url, *, json, headers, timeout):
            self.assertEqual(headers, {
                "X-Player-Id": "42:2",
                "X-Garden-Owner-Name": urllib.parse.quote("小机阿橘"),
            })
            self.assertEqual(json, {"command": "plant daisy 1"})
            self.assertTrue(url.endswith("/api/cmd?compact=1"))
            return Response(self.client.open("/api/cmd?compact=1", method=method,
                                             json=json, headers=headers))

        with patch.object(server.httpx, "request", side_effect=forward):
            result = server._play_garden_cat({
                "game": "garden_cat", "action": "cmd", "player_id": "42:2",
                "source_name": "顶层伪造",
                "params": {"command": "plant daisy 1", "session_id": "43",
                           "player_id": "43", "source_name": "伪造小机",
                           "owner_name": "伪造", "actor_name": "伪造",
                           "headers": {"X-Garden-Owner-Name": "伪造"}},
            }, owner_name="小机阿橘")
        self.assertTrue(result["ok"])
        # Exercise the real header filter, including case-insensitive spoofing.
        headers = garden_cat_request_headers({
            "x-player-id": "43", "x-garden-owner-name": "伪造小机",
            "x-garden-human-name": "伪造人类", "Authorization": "Bearer forged",
        }, hop_by_hop_headers=set(), forwarded_for="127.0.0.1", prefix="/garden-cat",
           target={"player": "42:2", "owner_name": "小机阿橘", "slot": 2},
           human_name="人类小满")
        self.assertEqual(headers["X-Garden-Human-Name"], urllib.parse.quote("人类小满"))
        human = self.client.post("/web/cmd?source_name=伪造", headers=headers, json={
            "command": "plant daisy 2", "session_id": "43", "source": "ai",
            "source_name": "伪造", "actor_name": "伪造", "author_name": "伪造",
        })
        self.assertTrue(human.json["ok"])
        records = self.client.get("/web/status", headers=headers).json["state"]["recent_event_records"]
        planted = [e for e in records if "种下了雏菊" in e["text"]]
        self.assertEqual([(e["source"], e["source_name"]) for e in planted],
                         [("ai", "小机阿橘"), ("human", "人类小满")])
        saved = self._read_state("42:2")["events"]
        self.assertEqual([e["source_name"] for e in saved if "种下了雏菊" in e["text"]],
                         ["小机阿橘", "人类小满"])
        self.assertTrue(all("actor_name" not in e for e in saved))
        self.assertFalse(Path(self.game_api.STORE.state_file_for("43")).exists())

    def test_water_and_pet_cat_land_in_the_selected_player_save(self):
        def adopted(state):
            state["cat"] = {"name": "栗子"}
            state["cat_stats"] = {"hunger": 70.0, "thirst": 70.0, "mood": 40.0, "affection": 20.0}
            state["cat_state"].update(phase="adopted", location="home")
            state["cat_last_pet_real_time"] = 0

        self._put_state("42:3", adopted)
        self._put_state("42:4", adopted)
        response = self.client.post("/web/cmd", headers={"X-Player-Id": "42:3"}, json={"command": "pet"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertGreater(self._read_state("42:3")["cat_stats"]["affection"], 20)
        self.assertEqual(self._read_state("42:4")["cat_stats"]["affection"], 20)

    def test_web_write_whitelist_rejects_everything_else(self):
        for path in ("/web/water", "/web/pet_cat", "/web/delete", "/web/sessions"):
            response = self.client.post(path, headers={"X-Player-Id": "trusted"}, json={})
            self.assertEqual(response.status_code, 404, path)

    def test_web_status_exposes_display_ready_cat_collectibles_and_letters(self):
        def with_cat_keepsakes(state):
            state["cat"] = {"name": "半夏"}
            state["cat_stats"] = {
                "hunger": 70.0,
                "thirst": 70.0,
                "mood": 60.0,
                "affection": 50.0,
            }
            state["collectibles"] = {"shell": 2, "clover": 1}
            state["letters_received"] = [0, 2]
            # Keep this status read deterministic: no random cat drop checks are due.
            state["last_collectible_check"] = 10**20
            state["last_letter_check"] = 10**20

        self._put_state("42:2", with_cat_keepsakes)
        response = self.client.get("/web/status", headers={"X-Player-Id": "42:2"})
        self.assertEqual(response.status_code, 200)
        summary = response.json["state"]
        self.assertEqual(summary["collectibles"], {"shell": 2, "clover": 1})
        self.assertEqual([letter["index"] for letter in summary["letters"]], [1, 3])
        self.assertIn("爪印", summary["letters"][0]["text"])

        no_cat = self.client.get("/web/status", headers={"X-Player-Id": "42:5"})
        self.assertEqual(no_cat.status_code, 200)
        self.assertEqual(no_cat.json["state"]["collectibles"], {})
        self.assertEqual(no_cat.json["state"]["letters"], [])

    def test_page_assets_and_fetches_stay_under_garden_cat_prefix(self):
        page = self.client.get(
            "/web/",
            headers={
                "X-Player-Id": "42:3",
                "X-Garden-Player": "42:3",
                "X-Garden-Owner-Name": "阿橘",
                "X-Garden-Slot": "3",
                "X-Forwarded-Prefix": "/garden-cat",
            },
        )
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('/garden-cat/static/style.css?v=', html)
        self.assertIn('/garden-cat/static/app.js?v=', html)
        self.assertIn('<base href="/garden-cat/"', html)
        self.assertNotIn('id="createForm"', html)
        self.assertNotIn('id="keyBtn"', html)
        self.assertIn('data-main-pane="cottage"', html)
        self.assertIn('id="notesBtn"', html)
        js = (ENGINE_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const SELECTED_PLAYER', js)
        self.assertIn('fetch(target, { ...options, credentials: "same-origin" })', js)
        self.assertIn('target.searchParams.set("live_events"', js)
        self.assertNotIn('X-Garden-Token', js)

if __name__ == "__main__":
    unittest.main(verbosity=2)
