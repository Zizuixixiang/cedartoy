"""Framer regression: original assets and real handlers, disposable storage only."""
import base64
import io
import subprocess
import unittest
from unittest.mock import patch

from shared_craft_adapter import ui
from shared_craft_adapter.service import create_app
from tests_toy import test_shared_craft


class SharedCraftFramerTests(unittest.TestCase):
    def test_script_rewrites_only_the_two_framer_fetch_urls(self):
        original = (ui.STATIC / "beads-framer.js").read_text()
        prefix = "/api/${encodeURIComponent(location.pathname.split('/').filter(Boolean)[1])}"
        expected = original
        for suffix in ("/bead-boards/${board.id}", "/gallery"):
            old = f"fetch(`{prefix}{suffix}`"
            new = f"fetch(`/shared-craft/api{suffix}`"
            self.assertEqual(original.count(old), 1)
            expected = expected.replace(old, new)
        response = ui.asset("beads-framer.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertEqual(response.body.decode(), expected)
        self.assertNotIn(prefix, response.body.decode())
        checked = subprocess.run(["node", "--check"], input=response.body, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode())

    def test_finished_decoration_and_gallery_survive_app_restart(self):
        fixture = test_shared_craft.SharedCraftTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from PIL import Image
        from shared_craft_room import beads as engine

        bid = fixture.play("new_bead_board", title="装裱回归", mode="together", size=8)["id"]
        fixture.play("place_beads", board_id=bid,
                     beads=[{"x": x, "y": 0, "color": "F14"} for x in range(3)])
        for when, action in ((100, "cover"), (100, "heat"), (105, "start_iron"),
                             (109, "finish_iron"), (113, "lift")):
            with patch.object(engine, "unix_now", return_value=when):
                fixture.play("work_bead_finish", board_id=bid, action=action)
        board_path = "api/bead-boards/" + bid
        self.assertEqual(fixture.request(board_path).json()["board"]["craft"]["stage"], "finished")
        self.assertIn('id="framer-export"', fixture.request("beads/").text)

        def png(color, size):
            out = io.BytesIO()
            Image.new("RGBA", size, color).save(out, format="PNG")
            return out.getvalue()

        # Distinct PNGs detect accidental loss or interchange of the full/piece images.
        full, piece = png("#ff8899", (32, 48)), png("#335577", (16, 8))
        payload = {
            "action": "decoration",
            "data_url": "data:image/png;base64," + base64.b64encode(full).decode(),
            "piece_data_url": "data:image/png;base64," + base64.b64encode(piece).decode(),
            "has_pouch": True,
        }

        def json_response(path, method="GET", body=None, app=None):
            response = fixture.request(path, method, body, app=app)
            self.assertEqual(response.status_code, 200, response.text[:500])
            self.assertIn("application/json", response.headers["content-type"])
            return response.json()

        self.assertTrue(json_response(board_path, "POST", payload)["ok"])
        gallery_payload = {"kind": "bead", "source_id": bid, "title": "装裱回归",
                           "piece_columns": 3, "piece_rows": 1}
        item = json_response("api/gallery", "POST", gallery_payload)["item"]
        restarted = create_app(fixture.root / "craft", fixture.accounts, fixture.grants,
                               "test-secret-" * 4)
        for app in (fixture.app, restarted):
            with self.subTest(restarted=app is restarted):
                craft = json_response(board_path, app=app)["board"]["craft"]
                self.assertTrue(craft["decorated"])
                self.assertTrue(craft["has_pouch"])
                self.assertTrue(craft["decorated_at"])
                saved = json_response("api/gallery", app=app)["items"][0]
                self.assertEqual(saved, item)
                self.assertTrue(saved["has_pouch"])
                self.assertEqual(saved["presentation"], "decorated")
                self.assertEqual((saved["piece_columns"], saved["piece_rows"]), (3, 1))
                for suffix, expected in ((".png", full), ("/display.png", piece)):
                    image = fixture.request("api/gallery/" + item["id"] + suffix, app=app)
                    self.assertEqual(image.status_code, 200)
                    self.assertIn("image/png", image.headers["content-type"])
                    self.assertEqual(image.content, expected)
                room = app.state.rooms["1"]
                self.assertEqual(room.beads.decorated_bytes("1", bid), full)
                self.assertEqual(room.beads.display_piece_bytes("1", bid), piece)
