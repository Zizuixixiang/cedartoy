"""Garden-Cat phase-2 acceptance tests; no service process is started."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


TOY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = TOY_ROOT / "vendor" / "Garden-Cat-Engine"
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(TOY_ROOT))


class GardenCatPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="garden-cat-phase2-")
        os.environ["GARDEN_CAT_SAVE_ROOT"] = self.tempdir.name
        import game_api

        self.game_api = importlib.reload(game_api)
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
            "/web/water?player_id=victim",
            json={"pot": 1, "player_id": "victim", "session_id": "victim"},
        )
        self.assertEqual(denied.status_code, 400)

        def planted(state):
            state["pots"][0] = {
                "flower_id": "daisy",
                "planted_time": 1,
                "watered": False,
                "growth_progress": 0.0,
                "last_growth_update": 1,
            }

        self._put_state("trusted", planted)
        self._put_state("victim", planted)
        accepted = self.client.post(
            "/web/water",
            headers={"X-Player-Id": "trusted"},
            json={"pot": 1, "player_id": "victim", "session_id": "victim"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(self._read_state("trusted")["pots"][0]["watered"])
        self.assertFalse(self._read_state("victim")["pots"][0]["watered"])

    def test_water_and_pet_cat_land_in_the_selected_player_save(self):
        def adopted(state):
            state["cat"] = {"name": "栗子"}
            state["cat_stats"] = {"hunger": 70.0, "thirst": 70.0, "mood": 40.0, "affection": 20.0}
            state["cat_last_pet_real_time"] = 0

        self._put_state("42:3", adopted)
        self._put_state("42:4", adopted)
        response = self.client.post("/web/pet_cat", headers={"X-Player-Id": "42:3"}, json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertGreater(self._read_state("42:3")["cat_stats"]["affection"], 20)
        self.assertEqual(self._read_state("42:4")["cat_stats"]["affection"], 20)

    def test_web_write_whitelist_rejects_everything_else(self):
        for path in ("/web/cmd", "/web/register", "/web/new_game", "/web/delete", "/web/sessions"):
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
        self.assertEqual(
            summary["collectibles"],
            [
                {"id": "shell", "name": "完整的贝壳", "emoji": "🐚", "count": 2},
                {"id": "clover", "name": "四叶草", "emoji": "🍀", "count": 1},
            ],
        )
        self.assertEqual([letter["index"] for letter in summary["letters"]], [1, 3])
        self.assertIn("爪印", summary["letters"][0]["text"])

        no_cat = self.client.get("/web/status", headers={"X-Player-Id": "42:5"})
        self.assertEqual(no_cat.status_code, 200)
        self.assertEqual(no_cat.json["state"]["collectibles"], [])
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
        self.assertIn("正在围观：<span>阿橘</span> 的花园·槽3", html)
        self.assertIn('href="/garden-cat/static/style.css"', html)
        self.assertIn('src="/garden-cat/static/human.js"', html)
        self.assertNotIn("/static/app.js", html)
        self.assertNotIn("存档码", html)
        self.assertNotIn("注册", html)
        self.assertIn('id="catCollectiblesPanel" class="panel hidden"', html)
        self.assertIn('id="catLettersPanel" class="panel hidden"', html)
        self.assertIn('id="notesList"', html)
        self.assertIn('id="notesComposer"', html)

        js = (ENGINE_ROOT / "static" / "human.js").read_text(encoding="utf-8")
        self.assertIn("`${BASE_PATH}/static/orange_cat.png`", js)
        self.assertIn('requestJson(`/web/notes?page=${page}`)', js)
        self.assertIn("window.setInterval(refresh, 3000)", js)
        self.assertIn("renderCatCollectionsAndLetters()", js)
        self.assertNotIn('fetch("/web/', js)

if __name__ == "__main__":
    unittest.main(verbosity=2)
