"""Opt-in sharing and legacy snapshots, exclusively in disposable stores."""
import sqlite3
import unittest
import uuid

from shared_craft_adapter.service import create_app
from tests_toy import test_shared_craft


def seed_legacy(fixture, item, note="", status="pending", actor="2"):
    """Represent a pre-upgrade row without using the new submit policy."""
    submission = uuid.uuid4().hex
    image = fixture.request("api/gallery/" + item["id"] + ".png").content
    with sqlite3.connect(fixture.app.state.shares.path) as conn:
        conn.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (submission, "1", item["id"], actor, item["title"], note,
                      image, status, 123, 124 if status == "declined" else None))
    return submission


class SharedCraftSharingTests(unittest.TestCase):
    def setUp(self):
        self.f = test_shared_craft.SharedCraftTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.draw(1)
        self.f.draw(2)
        self.item = self.f.gallery()
        self.f.grant()

    def rows(self):
        with self.f.app.state.shares.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM submissions ORDER BY id")]

    def test_default_private_joint_ai_human_and_legacy_confirm(self):
        f = self.f
        self.assertEqual(self.rows(), [])
        self.assertEqual(f.request("author", uid=6).json()["total"], 0)
        ai = f.play("share_work", 2, item_id=self.item["id"])
        human = f.request("api/shares", "POST", {"item_id": self.item["id"]}).json()
        self.assertEqual(ai["status"], "shared")
        self.assertEqual(human["status"], "shared")
        self.assertEqual(ai, f.play("share_work", 2, item_id=self.item["id"], confirm=True))
        self.assertEqual(ai, f.play("share_work", 2, item_id=self.item["id"], confirm=False))
        self.assertEqual(f.request("api/gallery").json()["pending_shares"], [])
        f.play("share_work", 3, ok=False, item_id=self.item["id"])
        f.play("share_work", 4, ok=False, item_id=self.item["id"])
        self.assertEqual(f.request("author", uid=3).status_code, 403)
        self.assertEqual(f.request("author/" + ai["id"], uid=3).status_code, 403)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(f.request("author", uid=6).json()["total"], 2)

    def test_pending_promoted_only_by_original_actor_same_note_with_frozen_snapshot(self):
        f = self.f
        old_id = seed_legacy(f, self.item)
        before = self.rows()[0]
        restarted = create_app(f.root / "craft", f.accounts, f.grants, "test-secret-" * 4)
        self.assertEqual(f.request("author", uid=6, app=restarted).json()["total"], 0)
        self.assertEqual(self.rows()[0], before, "starting the app must not publish pending")
        # Another participant may submit their own snapshot, never promote this row.
        f.play("share_work", 1, item_id=self.item["id"])
        self.assertEqual(next(r for r in self.rows() if r["id"] == old_id), before)
        f.play("share_work", 3, ok=False, item_id=self.item["id"])
        f.play("share_work", 4, ok=False, item_id=self.item["id"])
        # Only this disposable fixture's current image changes; old snapshots stay fixed.
        f.app.state.rooms["1"].gallery.image_path(self.item["id"]).write_bytes(b"changed current image")
        promoted = f.play("share_work", 2, item_id=self.item["id"])
        self.assertEqual(promoted, {"id": old_id, "status": "shared"})
        after = next(r for r in self.rows() if r["id"] == old_id)
        self.assertGreater(after["decided"], before["created"])
        for key in ("id", "room", "item", "actor", "title", "note", "image", "created"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(f.request("author/" + old_id, uid=6).content, before["image"])
        self.assertEqual(f.play("share_work", 2, item_id=self.item["id"], confirm=True), promoted)
        self.assertEqual(len(self.rows()), 2)
        changed_note = f.play("share_work", 2, item_id=self.item["id"], note="另一条附言")
        self.assertNotEqual(changed_note["id"], old_id)
        self.assertEqual(changed_note["status"], "shared")
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(f.request("author/" + changed_note["id"], uid=6).content, b"changed current image")

    def test_declined_stays_declined_and_author_reads_only_shared(self):
        f = self.f
        declined = seed_legacy(f, self.item, "已拒绝", "declined")
        pending = seed_legacy(f, self.item, "仍私密")
        before = self.rows()
        self.assertEqual(f.play("share_work", 2, item_id=self.item["id"], note="已拒绝"),
                         {"id": declined, "status": "declined"})
        self.assertEqual(self.rows(), before)
        for sid in (pending, declined):
            self.assertEqual(f.request("author/" + sid, uid=6).status_code, 403)
        shared = f.play("share_work", 2, item_id=self.item["id"], note="新的主动分享")
        self.assertEqual([r["id"] for r in f.request("author", uid=6).json()["items"]], [shared["id"]])
        for method in ("POST", "DELETE"):
            self.assertEqual(f.request("author/" + shared["id"], method, {}, 6).status_code, 405)
        self.assertEqual([r for r in self.rows() if r["id"] != shared["id"]], before)

    def test_note_limit_and_invalid_notes_do_not_publish(self):
        f = self.f
        for note in ("字" * 501, None, 123, {}):
            response = f.request("api/shares", "POST", {"item_id": self.item["id"], "note": note}, 2)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.rows(), [])
        note = "字" * 500
        shared = f.play("share_work", 2, item_id=self.item["id"], note=note)
        self.assertEqual(shared["status"], "shared")
        self.assertEqual(self.rows()[0]["note"], note)
        self.assertEqual(f.request("author", uid=6).json()["items"][0]["note"], note)

    def test_legacy_decision_still_scoped_and_immutable(self):
        f = self.f
        sid = seed_legacy(f, self.item)
        for uid in (2, 3, 4):
            self.assertEqual(f.request("api/shares/" + sid, "POST", {"confirm": True}, uid).status_code, 403)
            self.assertEqual(f.request("api/shares/" + sid + ".png", uid=uid).status_code, 403)
        self.assertEqual(f.request("api/shares/" + sid, "POST", {"confirm": False}).json()["status"], "declined")
        self.assertEqual(f.request("api/shares/" + sid, "POST", {"confirm": True}).json()["status"], "declined")
