"""Framer regression: original assets and real handlers, disposable storage only."""
import base64
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from shared_craft_adapter import ui
from shared_craft_adapter.service import create_app
from tests_toy import test_shared_craft

LEGACY = "996967978a115422d3c6b455a501de65634bd8a0"
COMPOSED = "a2d7cb7f7f6afce6d03df9b8d8c555345d8d53ea"


def upstream_source(revision, name="static/beads-framer.js"):
    # Immutable fetched commits; never checkout/fetch or edit the vendor tree.
    repo = Path(__file__).resolve().parents[1] / "vendor/shared-craft-room"
    return subprocess.check_output(
        ["git", "-C", str(repo), "show", revision + ":src/shared_craft_room/" + name],
        text=True, timeout=15)


class SharedCraftFramerTests(unittest.TestCase):
    def test_script_rewrites_only_fetch_urls_and_separates_full_from_piece_export(self):
        original = upstream_source(LEGACY)
        disk = (ui.STATIC / "beads-framer.js").read_bytes()
        prefix = "/api/${encodeURIComponent(location.pathname.split('/').filter(Boolean)[1])}"
        expected = original
        for suffix in ("/bead-boards/${board.id}", "/gallery"):
            old = f"fetch(`{prefix}{suffix}`"
            new = f"fetch(`/shared-craft/api{suffix}`"
            self.assertEqual(original.count(old), 1)
            expected = expected.replace(old, new)
        for old, new in (
            ("function finishedDataURL(){",
             "function assembledDataURL(){try{draw(false);return canvas.toDataURL('image/png')}finally{draw(true)}}\n"
             "function finishedDataURL(){"),
            ("dataUrl=finishedDataURL(),pieceUrl=pieceDataURL()",
             "dataUrl=assembledDataURL(),pieceUrl=pieceDataURL()"),
        ):
            self.assertEqual(original.count(old), 1)
            expected = expected.replace(old, new)
        with patch.object(type(ui.STATIC), "read_bytes", return_value=original.encode()):
            response = ui.asset("beads-framer.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertEqual(response.body.decode(), expected)
        self.assertNotIn(prefix, response.body.decode())
        checked = subprocess.run(["node", "--check"], input=response.body, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode())
        self.assertEqual((ui.STATIC / "beads-framer.js").read_bytes(), disk)
        for name in ("finishedDataURL", "pieceDataURL"):
            original_function = next(line for line in original.splitlines()
                                     if line.startswith(f"function {name}("))
            self.assertIn(original_function, response.body.decode())

    def test_changed_upstream_export_fails_closed(self):
        original = upstream_source(LEGACY).encode()
        for altered in (original.replace(b"function finishedDataURL(){", b"function renamed(){"),
                        original + b"\nfunction finishedDataURL(){}",
                        original.replace(b"dataUrl=finishedDataURL()", b"dataUrl=otherExport()"),
                        original + b"\ndataUrl=finishedDataURL(),pieceUrl=pieceDataURL()"):
            with patch.object(type(ui.STATIC), "read_bytes", return_value=altered):
                with self.assertRaisesRegex(ValueError, "装裱导出"):
                    ui.asset("beads-framer.js")

    def test_composed_export_keeps_author_implementation_and_only_rewrites_two_urls(self):
        original = upstream_source(COMPOSED)
        prefix = "/api/${encodeURIComponent(location.pathname.split('/').filter(Boolean)[1])}"
        expected = original
        for suffix in ("/bead-boards/${board.id}", "/gallery"):
            expected = expected.replace(f"fetch(`{prefix}{suffix}`", f"fetch(`/shared-craft/api{suffix}`")
        with patch.object(type(ui.STATIC), "read_bytes", return_value=original.encode()):
            response = ui.asset("beads-framer.js")
        text = response.body.decode()
        self.assertEqual(text, expected)
        self.assertEqual(text.count("fetch(`/shared-craft/api"), 2)
        self.assertNotIn(prefix, text)
        self.assertNotIn("assembledDataURL", text)
        self.assertIn("exportCanvas=composedCanvas(),dataUrl=exportCanvas.toDataURL('image/png'),pieceUrl=pieceDataURL()", text)
        self.assertIn("data_url:dataUrl,piece_data_url:pieceUrl", text)
        checked = subprocess.run(["node", "--check"], input=response.body, capture_output=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode())
        # Execute the actual unchanged exporters with instrumented canvas/object
        # boundaries: full copies the unselected composition; piece omits its bag.
        functions = "\n".join(line for line in text.splitlines()
                              if line.startswith(("function composedCanvas()", "function pieceDataURL()")))
        script = r"""
const assert=require('node:assert/strict'), events=[];
const canvas={width:900,height:900}, piece={kind:'piece',shell:true,keyring:true}, objects=[piece];
function draw(selected){events.push(['draw',selected])}
const document={createElement:()=>({getContext:()=>({drawImage:(source,x,y)=>{
  assert.equal(source,canvas);assert.equal(x,0);assert.equal(y,0);events.push(['copy']);
}}),toDataURL:()=> 'full'})};
function finishedDataURL(){assert.equal(piece.shell,false);assert.equal(piece.keyring,false);return 'piece'}
eval(JSON.parse(process.argv[1]));
assert.equal(composedCanvas().toDataURL('image/png'),'full');
assert.deepEqual(events,[['draw',false],['copy'],['draw',true]]);
assert.equal(pieceDataURL(),'piece');assert.equal(piece.shell,true);assert.equal(piece.keyring,true);
"""
        result = subprocess.run(["node", "-e", script, json.dumps(functions)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_unknown_composed_or_mixed_exports_fail_closed(self):
        original = upstream_source(COMPOSED)
        for altered in (
            original.replace("exportCanvas=composedCanvas()", "exportCanvas=finishedCanvas()"),
            original.replace("pieceUrl=pieceDataURL()", "pieceUrl=exportCanvas.toDataURL('image/png')"),
            original.replace("data_url:dataUrl,piece_data_url:pieceUrl", "data_url:pieceUrl,piece_data_url:dataUrl"),
            original.replace("function composedCanvas(){draw(false);", "function composedCanvas(){draw(true);"),
            original + "\nfunction pieceDataURL(){return 'wrong'}",
            original + "\ndataUrl=finishedDataURL(),pieceUrl=pieceDataURL()",
        ):
            with self.subTest(altered=altered[-70:]), patch.object(type(ui.STATIC), "read_bytes", return_value=altered.encode()):
                with self.assertRaisesRegex(ValueError, "装裱导出"):
                    ui.asset("beads-framer.js")

    def test_installed_upstream_asset_is_supported(self):
        response = ui.asset("beads-framer.js")
        self.assertEqual(response.status_code, 200)
        checked = subprocess.run(["node", "--check"], input=response.body, capture_output=True, timeout=15)
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
        # Both 6 MB PNG limits reject before changing either persisted image.
        for field in ("data_url", "piece_data_url"):
            oversized = "data:image/png;base64," + base64.b64encode(b"x" * 6_000_001).decode()
            response = fixture.request(board_path, "POST", {**payload, field: oversized})
            self.assertEqual(response.status_code, 400)
            self.assertIn("too large", response.json()["error"])
            room = fixture.app.state.rooms["1"]
            self.assertEqual(room.beads.decorated_bytes("1", bid), full)
            self.assertEqual(room.beads.display_piece_bytes("1", bid), piece)
        # The existing transport cap is stricter than 20 MB, including JSON/base64.
        response = fixture.request(board_path, "POST", {**payload, "padding": "x" * 17_000_001})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "请求过大")
        self.assertEqual(room.beads.decorated_bytes("1", bid), full)
        self.assertEqual(room.beads.display_piece_bytes("1", bid), piece)
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
