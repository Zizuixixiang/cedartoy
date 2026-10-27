"""Private satellite boundary; no production database or service access."""
import io
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import autochess_adapter as adapter
import server
import game_activity

CONFIG = {'AUTOCHESS_ENABLED': '1', 'CHESS_PLATFORM_SECRET': 'a' * 64,
          'AUTOCHESS_ORIGIN': 'http://127.0.0.1:8791'}


class AutochessBoundaryTests(unittest.TestCase):
    def test_closed_by_default_and_only_numeric_loopback_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(adapter.enabled())
            with self.assertRaises(server._McpError):
                adapter.configuration()
        for origin in ('https://example.com', 'http://localhost:8791',
                       'http://127.0.0.1:8791@evil.test', 'http://127.0.0.1:8791/path',
                       'http://127.0.0.1:8791?redirect=x', 'http://127.0.0.1:bad'):
            with self.subTest(origin=origin), patch.dict(os.environ, {**CONFIG, 'AUTOCHESS_ORIGIN': origin}):
                with self.assertRaises(server._McpError):
                    adapter.configuration()
        for secret in ('', 'short', 'replace-with-a-long-random-value', 'A'*64):
            with patch.dict(os.environ, {**CONFIG, 'CHESS_PLATFORM_SECRET': secret}):
                with self.assertRaises(server._McpError):
                    adapter.configuration()

    def test_credentials_and_forged_ids_never_cross_the_transport(self):
        response = Mock(status_code=200)
        response.json.return_value = {'roomId': 'abcdef123456'}
        with patch.dict(os.environ, CONFIG), patch.object(adapter.httpx, 'Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.post.return_value = response
            result = adapter.play({'action': 'create', 'requestId': 'test-create1',
                                   'token': 'user-secret', 'password': 'password-secret',
                                   'player_id': '999', 'opponent_id': '888'}, player_id='42:2')
            self.assertEqual(result['roomId'], 'abcdef123456')
            factory.assert_called_once_with(trust_env=False, follow_redirects=False, timeout=5)
            kwargs = client.post.call_args.kwargs
            self.assertEqual(kwargs['headers']['X-Player-Id'], '42:2')
            self.assertEqual(kwargs['json'], {'action': 'create', 'params': {'requestId': 'test-create1'}})
            for forbidden in ('user-secret', 'password-secret', '999', '888'):
                self.assertNotIn(forbidden, json.dumps(kwargs))
            response.status_code = 302
            with self.assertRaises(server._McpError):
                adapter.play({'action': 'read', 'roomId': 'abcdef123456'}, player_id='42')

    def test_wait_and_nested_credentials_rejected_before_transport(self):
        for args in ({'waitMs': 25000}, {'wait_ms': 1}, {'waitMs': -1}):
            with self.assertRaises(server._McpError):
                adapter.payload('read', args)
        with self.assertRaises(server._McpError):
            adapter.payload('act', {'plan': {'nested': [{'accountToken': 'secret'}]}})
        with self.assertRaises(server._McpError):
            adapter.payload('read', {'roomId': 'one', 'room_id': 'two'})
        for action in ('login', 'register', 'match', 'admin', 'export'):
            with self.assertRaises(server._McpError):
                adapter.payload(action, {})

    def test_root_canonical_account_slots_and_guest_namespaces(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(server.sqlite3, 'connect', side_effect=AssertionError('unexpected database access')))
            stack.enter_context(patch.object(server, '_current_account', return_value={'id':42,'username':'machine','is_ai':1}))
            stack.enter_context(patch.object(server, '_auto_migrate_legacy_account_saves'))
            stack.enter_context(patch.object(server, '_anti_addiction_context', return_value=None))
            stack.enter_context(patch.object(server, '_anti_addiction_preflight', return_value=None))
            stack.enter_context(patch.object(server, '_reject_claimed_guest'))
            stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda response, **kwargs: response))
            play=stack.enter_context(patch.object(adapter, 'play', return_value={'ok':True}))
            for token, slot, reported, expected in [('fixture',1,'victim','42'),('fixture',3,'victim','42:3'),(None,1,'guest:fixture','guest:fixture')]:
                server._tool_play_inner({'game':'autochess','action':'read','player_id':reported,
                    'params':{'slot':slot,'player_id':reported,'roomId':'abcdef123456'}}, path_token=token)
                self.assertEqual(play.call_args.kwargs['player_id'], expected)

    def test_path_token_and_bearer_reach_canonical_identity_through_http_dispatch(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(server.sqlite3, 'connect', side_effect=AssertionError('unexpected database access')))
            account=stack.enter_context(patch.object(server, '_current_account', return_value={'id':42,'username':'fixture','is_ai':1}))
            for name in ('_auto_migrate_legacy_account_saves', '_reject_claimed_guest'):
                stack.enter_context(patch.object(server, name))
            for name in ('_anti_addiction_context', '_anti_addiction_preflight'):
                stack.enter_context(patch.object(server, name, return_value=None))
            for name in ('_feedback_unread_reminder','_duel_unread_request_reminder','_mcp_forced_announcement'):
                stack.enter_context(patch.object(server, name, return_value=''))
            stack.enter_context(patch.object(server,'_authenticated_ai_player_id',return_value='42'))
            stack.enter_context(patch.object(server,'_request_rate_limit_identity',return_value='fixture'))
            stack.enter_context(patch.object(server,'_check_request_rate_limit',return_value=True))
            stack.enter_context(patch.object(server,'_finalize_play_response',side_effect=lambda response, **kwargs: response))
            play=stack.enter_context(patch.object(adapter,'play',return_value={'ok':True}))
            for path,bearer,expected in [('/fixture-path','','fixture-path'),('/mcp','fixture-bearer','fixture-bearer'),('/fixture-path','fixture-bearer','fixture-path')]:
                body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'play','arguments':{'game':'autochess','action':'read','params':{'roomId':'abcdef123456','player_id':'victim','slot':2}}}}
                raw=json.dumps(body).encode()
                handler=server.CedarToyHandler.__new__(server.CedarToyHandler)
                handler.path=path;handler.client_address=('127.0.0.1',12345)
                handler.headers={'Content-Length':str(len(raw))}
                if bearer: handler.headers['Authorization']='Bearer '+bearer
                handler.rfile=io.BytesIO(raw);handler.wfile=io.BytesIO()
                handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock()
                handler.do_POST()
                response=json.loads(handler.wfile.getvalue())
                self.assertFalse(response['result'].get('isError'),response)
                self.assertEqual(account.call_args.args[0],expected)
                self.assertEqual(play.call_args.kwargs['player_id'],'42:2')

    def test_activity_excludes_reads_and_retry_responses(self):
        self.assertIsNone(game_activity.business_action('autochess','read'))
        self.assertIsNone(game_activity.business_action('autochess','act',{'kind':'say'}))
        self.assertEqual(game_activity.business_action('autochess','start'),'start')
        self.assertFalse(game_activity.response_succeeded({'duplicate':True,'roomId':'fixture'}))

    def test_discovery_is_opt_in_guide_is_attributed_and_schema_unchanged(self):
        before = json.dumps(server._root_tools(),ensure_ascii=False)
        with patch.dict(os.environ, {'AUTOCHESS_ENABLED':'0'}):
            self.assertNotIn('autochess·',server._tool_list_games())
        with patch.dict(os.environ, {'AUTOCHESS_ENABLED':'1'}):
            self.assertIn('autochess·私人自走棋·江川雪',server._tool_list_games())
        self.assertEqual(before,json.dumps(server._root_tools(),ensure_ascii=False))
        guide=json.loads(server._tool_get_guide({'game':'autochess'}))['guide']
        for value in ('江川雪','63657956652','https://github.com/Cecilia-coding/AutoChess-MCP','MIT','start','self.revision'):
            self.assertIn(value,guide)


if __name__ == '__main__':
    unittest.main()
