import base64
import json
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
import server


class DuelInvitePlatformTests(unittest.TestCase):
    def test_invite_proxy_preserves_selection_but_replaces_binding_headers(self):
        target = {"human_player": "human:7", "human_name": "主人", "human_avatar": None,
                  "machines": [{"id": "42:3", "name": "自家小机"}]}
        payload = {"game_type": "uno", "target_player_count": 4, "ai_players": ["42:3"],
                   "player_id": "forged", "opponent_id": "forged"}
        raw = json.dumps(payload).encode()
        handler = object.__new__(server.CedarToyHandler)
        handler.command = "POST"
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                           "X-Duel-Human-Player": "forged", "x-duel-bound-ais": "forged",
                           "X-Duel-Ai-Player": "forged"}
        handler.rfile, handler.wfile = BytesIO(raw), BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        response = Mock(status=200, reason="OK")
        response.read.return_value = b'{"ok": true}'
        response.getheaders.return_value = [("Content-Type", "application/json")]
        response.getheader.return_value = "application/json"
        with patch.object(server.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value = response
            handler._proxy_to_duel("POST", "/api/invites", "", target=target)
        call = connection.return_value.request.call_args
        body = json.loads(call.kwargs["body"])
        self.assertEqual(body["ai_players"], ["42:3"])
        self.assertNotIn("opponent_id", body)
        self.assertNotEqual(body.get("player_id"), "forged")
        headers = call.kwargs["headers"]
        self.assertEqual(headers["X-Duel-Human-Player"], target["human_player"])
        self.assertEqual(json.loads(base64.urlsafe_b64decode(headers["X-Duel-Bound-Ais"] + "==")), target["machines"])
        self.assertNotIn("x-duel-bound-ais", headers)
        self.assertNotIn("X-Duel-Ai-Player", headers)

    def test_invite_root_mcp_accepts_path_and_bearer_authentication(self):
        arguments = {"game": "duel", "action": "invite", "params": {"game_type": "uno", "target_player_count": 4}}
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "play", "arguments": arguments}}
        for auth in ({"path_token": "path-auth"}, {"bearer_token": "bearer-auth"}):
            with self.subTest(auth=next(iter(auth))), \
                    patch.object(server, "_authenticated_ai_player_id", return_value="42"), \
                    patch.object(server, "_mcp_forced_announcement", return_value=""), \
                    patch.object(server, "_tool_play", return_value='{"ok": true}') as play:
                response = server._handle_root_mcp(payload, **auth)
                self.assertNotIn("error", response)
                play.assert_called_once_with(arguments, path_token=next(iter(auth.values())))

    def test_invite_fields_are_canonical_and_do_not_resolve_binding(self):
        account={"id":42,"username":"可信小机","is_ai":1}
        with (patch.object(server,"_current_account",return_value=account),
              patch.object(server,"_auto_migrate_legacy_account_saves"),
              patch.object(server,"_anti_addiction_context",return_value=None),
              patch.object(server,"_duel_bound_human_player_id",side_effect=AssertionError("must not need binding")),
              patch.object(server,"_request_duel_backend",return_value={"ok":True}) as backend,
              patch.object(server,"_stamp_save_owner"),
              patch.object(server,"_anti_addiction_record_success",return_value=""),
              patch.object(server,"_play_announcements",return_value="")):
            for action,params in [("invite",{"game_type":"tictactoe","target_player_count":2}), ("invite",{"game_type":"uno","target_player_count":4,"stake":0,"timeout_takeover_seconds":180}), ("join",{"invite_code":"ABCDEF123456"})]:
                server._tool_play_inner({"game":"duel","action":action,"params":{**params,"player_id":"victim","opponent_id":"other","participant_ids":["victim"],"ai_players":["victim"],"display_name":"冒名"}},path_token="authenticated")
                self.assertEqual(backend.call_args.args[0],{"action":action,"player_id":"42","display_name":"可信小机",**params})

    def test_guide_distinguishes_human_household_from_ai_invites(self):
        for meaning in (
            "邀请房主要用于邀请其他家庭；自家人类/小机互玩可直接使用普通开房。",
            "AI invite 只入自己", "join(invite_code)", "房满后房主 start",
            "已有受邀者可 start(fill_with_npcs=true)", "stake", "timeout 默认关闭",
            "90/180", "reclaim",
        ):
            self.assertIn(meaning, server.DUEL_GUIDE)
        self.assertNotIn("邀请码不限家庭", server.DUEL_GUIDE)
        self.assertNotIn("邀请码不限制家庭", server.DUEL_GUIDE)
        invite_section = server.DUEL_GUIDE.split("邀请房主要", 1)[1].split("聊天：", 1)[0]
        self.assertEqual(len(invite_section.strip().splitlines()), 3)

    def test_invite_schema_routes_and_independent_chat(self):
        for tools in (server._ROOT_PLATFORM_TOOLS,server._KELIVO_PLATFORM_TOOLS):
            props=next(t for t in tools if t["name"]=="play")["inputSchema"]["properties"]["params"]["properties"]
            self.assertTrue({"invite_code","timeout_takeover","timeout_takeover_seconds"} <= props.keys())
            self.assertNotIn("reply_to", props)
            self.assertIn("小机请使用邀请码",props["invite_code"]["description"])
            self.assertEqual(props["timeout_takeover_seconds"]["type"], "integer")
            self.assertNotIn("enum", props["timeout_takeover_seconds"])
        for method,path in [("POST","/api/invites"),("POST","/api/invites/join"),("GET","/api/invites/ABCDEF123456"),("POST","/api/rooms/ABCDEFGH/start"),("POST","/api/rooms/ABCDEFGH/reclaim")]:
            self.assertTrue(server._duel_proxy_allowed(method,path))
        self.assertFalse(server._duel_proxy_allowed("GET","/api/invites/../../secret"))
        chat=server._prepare_duel_payload({"action":"chat","player_id":"fake","room_id":"ABCDEFGH","message":"hello","reply_to":3,"move":{"row":1}},trusted_player_id="42")
        self.assertEqual(chat,{"action":"chat","player_id":"42","room_id":"ABCDEFGH","message":"hello"})
        self.assertIn("chat(room_id,message)",server.DUEL_GUIDE)
        self.assertIn("participants.handle 的 @handle 可定向提醒目标小机", server.DUEL_GUIDE)
        for removed in ("reply_to", "chat_refs"):
            self.assertNotIn(removed, server.DUEL_GUIDE)

    def test_mcp_human_invitation_link_is_absolute(self):
        relative = "/duel/?invite=ABCDEF123456"
        with patch.object(server, "_request_duel_backend", return_value={"ok": True, "invite_link": relative, "room": {"invite_link": relative}}):
            result = server._play_duel({"action": "invite", "game_type": "uno", "target_player_count": 4}, trusted_player_id="42")
        self.assertEqual(result["invite_link"], "https://toy.cedarstar.org" + relative)
        self.assertEqual(result["room"]["invite_link"], result["invite_link"])
