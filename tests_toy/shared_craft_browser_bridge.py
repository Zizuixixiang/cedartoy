"""Local browser tests talk to the real ASGI app over stdio, with temp stores."""
import base64
import json
import sqlite3
import sys

from tests_toy.test_shared_craft import SharedCraftTests


fixture = SharedCraftTests()
fixture.setUp()
try:
    for uid in (1, 2, 3):
        fixture.draw(uid)
    item = fixture.gallery()
    fixture.play("share_work", 2, item_id=item["id"], note="谢谢你做了这间手作房", confirm=True)
    fixture.grant()
    if "--visual" in sys.argv:
        # Exercise both columns and both share labels, including a longer AI name.
        fixture.gallery()
        with sqlite3.connect(fixture.accounts) as conn:
            conn.execute("UPDATE toy_users SET username=? WHERE id=3", ("小雪的手作伙伴",))
    bid = fixture.play("new_bead_board", 2, title="三人的豆板", mode="together", size=16)["id"]
    for uid in (1, 2, 3):
        fixture.play("place_beads", uid, board_id=bid, beads=[{"x": uid, "y": 1, "color": "F14"}])
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        task = json.loads(line)
        response = fixture.request(task["path"].lstrip("/"), task.get("method", "GET"),
                                   task.get("body"), uid=task.get("uid", 1))
        print(json.dumps({"id": task["id"], "status": response.status_code,
                          "contentType": response.headers.get("content-type"),
                          "body": base64.b64encode(response.content).decode()}), flush=True)
finally:
    fixture.doCleanups()
