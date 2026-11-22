"""Authenticated Duel paths and real ASGI room actions on disposable databases."""
import base64
import json
import unittest

from tests_toy import test_duel_new_games_integration as integration
from app import framework
import server


class LiarsBarRootTests(unittest.TestCase):
    setUp = integration.NewGamesRootIntegration.setUp
    http = integration.NewGamesRootIntegration.http
    rpc = integration.NewGamesRootIntegration.rpc

    def test_catalog_guide_and_shared_schema(self):
        catalog = self.rpc("catalog", auth="bearer_token")["games"]
        entry = next(g for g in catalog if g["game_type"] == "liars_bar")
        self.assertEqual(entry["allowed_player_counts"], [2, 3, 4])
        self.assertEqual(entry["category"], "tabletop")
        self.assertFalse(entry["supports_stakes"])
        self.assertEqual(entry["guide"], "duel:liars_bar")
        guide = json.loads(server._tool_get_guide({"game": "duel:liars_bar"}))["guide"]
        for term in ('"play"', '"challenge"', '"accept"', "private.hand", "LYiHub/liars-bar-llm", "Apache-2.0"):
            self.assertIn(term, guide)
        for tools in (server._PLATFORM_TOOLS, server._KELIVO_PLATFORM_TOOLS):
            schema = next(t["inputSchema"] for t in tools if t["name"] == "play")
            self.assertNotIn("liars_bar", json.dumps(schema))

    def test_global_index_and_guide_opening_example(self):
        index = server._tool_list_games()
        self.assertIn("duel·双弈", index)
        self.assertNotIn("liars_bar", index)
        opened = self.rpc("new", {"game_type": "liars_bar"})
        rid = opened.get("room", opened)["room_id"]
        room = framework.get_room(rid)
        self.assertEqual(room["game_type"], "liars_bar")
        self.assertEqual(room["stake"], 0)

    def test_human_web_ordinary_room_and_authenticated_machine_play(self):
        headers = {"X-Duel-Human-Player": "101", "X-Duel-Ai-Player": "202"}
        created = self.http("POST", "/api/rooms", headers=headers, json={
            "player_id": "101", "ai_players": ["202"], "game_type": "liars_bar",
            "stake": 0, "target_player_count": 2, "mode": "random",
        })
        self.assertEqual(created.status_code, 200, created.text)
        view = created.json()["room"]
        rid = view["room_id"]
        self.assertEqual(len(view["private_state"]["hand"]), 5)
        self.rpc("state", {"room_id": rid, "wait": False})
        for _ in range(2):
            room = framework.get_room(rid)
            actor = room["current_player_id"]
            move = {"action": "play", "cards": room["board_state"]["hands"][actor][:1]}
            if actor == "101":
                moved = self.http("POST", f"/api/rooms/{rid}/move", headers=headers, json={
                    "player_id":"101", "move":move, "revision":room["revision"]})
                self.assertEqual(moved.status_code, 200, moved.text)
                self.assertEqual(moved.json()["room"]["board_state"]["claim"]["count"], 1)
                self.assertEqual(moved.json()["room"]["private_state"]["played"], [move["cards"]])
                human_cards = move["cards"]
            else:
                reply = self.rpc("move", {"room_id":rid, "move":move, "revision":room["revision"], "wait":False})
                self.assertEqual(reply["private"]["played_add"], [move["cards"]])
        refreshed = self.http("GET", f"/api/rooms/{rid}", headers=headers).json()
        self.assertEqual(refreshed["room"]["private_state"]["played"], [human_cards])
        self.assertEqual(framework.get_room(rid)["revision"], view["revision"] + 2)

    def test_invite_and_public_spectator_through_real_auth(self):
        invitation = self.rpc("invite", {"game_type": "liars_bar", "target_player_count": 2,
                                         "player_id": "forged"})
        rid = invitation.get("room", invitation)["room_id"]
        self.rpc("state", {"room_id": rid, "player_id": "202"}, token=self.outsider, expect_ok=False)
        room = framework.get_room(rid)
        self.rpc("join", {"invite_code": room["invite_code"]}, token=self.outsider, auth="bearer_token")
        self.rpc("start", {"room_id": rid})
        room = framework.get_room(rid)
        tokens = {"202": self.token, "303": self.outsider}
        self.assertEqual(set(room["board_state"]["alive"]), set(tokens))
        for pid, token in tokens.items():
            self.rpc("state", {"room_id": rid, "wait": False}, token=token)
            snapshot = self.rpc("state", {"room_id": rid, "full_state": True, "viewer": "forged"},
                                token=token)["snapshot"]
            self.assertEqual(snapshot["private_state"]["hand"], room["board_state"]["hands"][pid])
            self.assertNotIn("chambers", json.dumps(snapshot))
        pid = room["current_player_id"]
        other = next(p for p in tokens if p != pid)
        cards = room["board_state"]["hands"][pid][:2]
        args = {"room_id": rid, "revision": room["revision"], "player_id": pid, "wait": False,
                "move": {"action": "play", "cards": cards}}
        self.rpc("move", args, token=tokens[other], expect_ok=False)
        reply = self.rpc("move", args, token=tokens[pid], auth="bearer_token")
        self.assertEqual(reply["private"]["played_add"], [cards])
        self.rpc("move", args, token=tokens[pid], expect_ok=False)
        opponent = self.rpc("state", {"room_id": rid, "wait": False, "player_id": pid}, token=tokens[other])
        self.assertNotIn('"cards"', json.dumps(opponent["events"]))
        self.assertNotIn("played_add", json.dumps(opponent))
        snapshot = self.rpc("state", {"room_id":rid, "full_state":True, "viewer":pid}, token=tokens[other])["snapshot"]
        self.assertEqual(snapshot["private_state"]["played"], [])
        bound = base64.urlsafe_b64encode(json.dumps([{"id":pid, "name":"小机"}]).encode()).decode()
        headers = {"X-Duel-Human-Player": "101", "X-Duel-Bound-Ais": bound}
        url = f"/api/rooms/{rid}"
        self.assertEqual(self.http("GET", url, headers={}).status_code, 403)
        blocked = self.http("GET", url, params={"perspective": other}, headers=headers)
        self.assertEqual(blocked.status_code, 403)
        spectator = self.http("GET", url, headers=headers).json()
        self.assertTrue(spectator["room"]["spectating"])
        self.assertEqual(spectator["room"]["private_state"], {})
        explicit = self.http("GET", url, params={"perspective": pid}, headers=headers)
        self.assertEqual(explicit.status_code, 200)
        self.assertEqual(explicit.json()["room"]["private_state"], {})
        for view in (spectator, explicit.json()):
            for hidden in ('"cards"', '"played"', '"played_add"', '"previous_plays"'):
                self.assertNotIn(hidden, json.dumps(view))
        self.assertEqual(spectator["room"]["board_state"]["claim"], {"player_id": pid, "count": 2})
        self.assertNotIn('"cards"', json.dumps(spectator["timeline"]))
        self.assertEqual(self.http("POST", url + "/move", headers=headers, json={
            "revision": room["revision"] + 1, "player_id": other, "move": {"action": "challenge"}}).status_code, 403)
        current = framework.get_room(rid)
        self.rpc("move", {"room_id": rid, "revision": current["revision"], "wait": False,
                          "move": {"action": "challenge"}}, token=tokens[other])
        self.assertEqual(framework.get_room(rid)["board_state"]["reveal"]["cards"], cards)
