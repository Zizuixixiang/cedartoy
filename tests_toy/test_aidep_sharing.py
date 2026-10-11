"""Consent boundaries exercised with real tokens and temporary databases only."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import account_deletion
import research_scales
from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs

install_missing_optional_game_stubs()
import server


def seed_accounts():
    """Temporary fixture shared with the local browser check; never production."""
    with server._db_connect() as conn:
        conn.executescript('''
            CREATE TABLE toy_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
                is_ai INTEGER DEFAULT 0, deleted_at TEXT, last_active_at TEXT);
            CREATE TABLE user_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                human_user_id INTEGER, ai_user_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(human_user_id, ai_user_id));
            INSERT INTO toy_users(id, username, is_ai) VALUES
                (1, '人类甲', 0), (2, '人类乙', 0), (3, '人类丙', 0),
                (11, '小机甲', 1), (12, '小机乙', 1), (13, '无关小机', 1);
            INSERT INTO user_bindings(human_user_id, ai_user_id) VALUES
                (1, 11), (1, 12), (2, 11);
        ''')
        server._init_account_security_schema(conn)
        account_deletion.init_schema(conn)
        return {u['id']: server._issue_initial_account_token_in_transaction(conn, dict(u))
                for u in conn.execute('SELECT * FROM toy_users').fetchall()}


class AidepSharingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='cedar-aidep-'))
        self.accounts = Path(self.temp) / 'accounts.db'
        self.sessions = Path(self.temp) / 'sessions.db'
        self.stack.enter_context(patch.object(server, 'TURTLE_DB_PATH', self.accounts))
        self.stack.enter_context(patch.object(server, 'SESSIONS_DB_PATH', self.sessions))
        self.stack.enter_context(patch.object(research_scales, 'DB_PATH', str(self.sessions)))
        self.tokens = seed_accounts()
        self.engine = research_scales.SCALES['aidep']

    def web(self, action, body=None, user=1):
        return server._human_test_action('aidep', action, self.tokens[user], body or {})

    def complete(self, user=1):
        state = self.web('start', {'mode': 'full_fast'}, user)
        return self.web('answer_batch', {'session_id': state['session_id'], 'answers': [1, 2, 3, 4, 5]*4+[2, 3]}, user)

    def grant(self, ai=11, user=1):
        settings = self.web('sharing', user=user)
        return self.web('sharing', {'enabled': True, 'ai_id': ai, 'session_id': settings['session_id'],
                                   'result_version': settings['result_version']}, user)

    def read(self, ai=11, human=1, **spoof):
        return json.loads(server._tool_play({'game': 'aidep', 'action': 'shared_result',
            'params': {'human_id': human, **spoof}}, path_token=self.tokens[ai]))

    def denied(self, function, code=-32003):
        with self.assertRaises(server._McpError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)

    def test_default_private_and_only_scores_after_consent(self):
        self.complete()
        self.denied(self.read)
        state = self.web('sharing')
        self.assertFalse(state['enabled'])
        self.assertIsNone(state['ai_id'])
        self.assertEqual([b['ai_id'] for b in state['bindings']], [11, 12])
        self.grant()
        result = self.read()
        self.assertEqual(set(result), {'ok', 'game', 'human_id', 'scores', 'dimensions', 'explanation', 'notice'})
        self.assertEqual(result['scores'], self.engine.score([1, 2, 3, 4, 5]*4+[2, 3]))
        self.assertEqual(set(result['scores']), {'overall', 'emotional', 'functional', 'cognitive', 'control'})
        text = json.dumps(result, ensure_ascii=False)
        for secret in ['answers', 'questions', 'session_id', 'result_detail', 'result_version', '人类甲']:
            self.assertNotIn(secret, text)
        for q in self.engine.items:
            self.assertNotIn(q['text'], text)
        self.denied(lambda: self.read(12))
        self.denied(lambda: self.read(13))

    def test_revoke_and_explicit_target_switch(self):
        self.complete()
        self.grant()
        self.grant(12)
        self.denied(self.read)
        self.assertTrue(self.read(12)['ok'])
        self.assertEqual(self.web('sharing')['ai_id'], 12)
        self.web('sharing', {'enabled': False})
        self.denied(lambda: self.read(12))
        self.assertFalse(self.web('sharing')['enabled'])

    def test_shared_reader_requires_real_ai_transport_token(self):
        self.complete()
        self.grant()
        args = {'game': 'aidep', 'action': 'shared_result', 'token': self.tokens[11],
                'player_id': '11', 'params': {'human_id': 1, 'token': self.tokens[11], 'ai_id': 11}}
        for token in (None, '', 'invalid'):
            self.denied(lambda: server._tool_play(args, path_token=token), -32001)
        self.denied(lambda: server._tool_play(args, path_token=self.tokens[1]))
        self.denied(lambda: server._tool_play(args, path_token=self.tokens[13]))
        # Deserialized legacy params still use the same strict authorization.
        args['params'] = json.dumps({'human_id': 1})
        self.assertTrue(json.loads(server._tool_play(args, path_token=self.tokens[11]))['ok'])

    def test_grant_validation_cannot_enable_by_coercion_or_stale_version(self):
        self.complete()
        settings = self.web('sharing')
        for enabled in ('true', 1, [], None):
            self.denied(lambda: self.web('sharing', {**settings, 'enabled': enabled, 'ai_id': 11}), -32602)
        for ai_id in (True, '11', 11.0, [11], None, 1, 13):
            self.denied(lambda: self.web('sharing', {**settings, 'enabled': True, 'ai_id': ai_id}))
        for key in ('session_id', 'result_version'):
            self.denied(lambda: self.web('sharing', {**settings, 'enabled': True, 'ai_id': 11, key: 'stale'}), -32602)
        self.assertFalse(self.web('sharing')['enabled'])
        self.denied(self.read)

    def test_retest_and_stale_page_cannot_share_new_result(self):
        result = self.complete()
        old = self.grant()
        state = self.web('start', {'mode': 'full_fast', 'restart': True, 'confirm': True, 'session_id': result['session_id']})
        self.denied(self.read)
        self.denied(lambda: self.web('sharing'), -32004)
        self.web('answer_batch', {'session_id': state['session_id'], 'answers': [5]*22})
        self.denied(self.read)
        self.assertFalse(self.web('sharing')['enabled'])
        self.denied(lambda: self.web('sharing', {**old, 'enabled': True, 'ai_id': 11}), -32602)
        self.grant()
        self.assertEqual(self.read()['scores']['overall'], 5)

    def test_unbind_rebind_and_account_deletion(self):
        self.complete()
        self.grant()
        with server._db_connect() as conn:
            conn.execute('DELETE FROM user_bindings WHERE human_user_id=1 AND ai_user_id=11')
        self.denied(self.read)
        with server._db_connect() as conn:
            conn.execute('INSERT INTO user_bindings(human_user_id, ai_user_id) VALUES(1,11)')
        self.denied(self.read)
        self.grant()
        for field, value in [('deletion_requested_at_epoch', 1), ('deleted_at', 'deleted')]:
            with self.subTest(field=field):
                with server._db_connect() as conn:
                    conn.execute(f'UPDATE toy_users SET {field}=? WHERE id=1', (value,))
                self.denied(self.read)
                with server._db_connect() as conn:
                    conn.execute(f'UPDATE toy_users SET {field}=NULL WHERE id=1')
        with server._db_connect() as conn:
            conn.execute('DELETE FROM toy_users WHERE id=1')
        self.denied(self.read)

    def test_deleted_ai_and_missing_corrupt_or_replaced_result(self):
        self.complete()
        self.grant()
        with server._db_connect() as conn:
            conn.execute("UPDATE toy_users SET deleted_at='deleted' WHERE id=11")
        self.denied(self.read, -32001)
        self.assertFalse(self.web('sharing')['enabled'])
        with server._db_connect() as conn:
            conn.execute('UPDATE toy_users SET deleted_at=NULL WHERE id=11')
        with sqlite3.connect(self.sessions) as conn:
            raw = conn.execute("SELECT result_detail FROM test_results WHERE player_id='1'").fetchone()[0]
            detail = json.loads(raw)
            detail['answers'] = [5]*22  # Same session id is insufficient to transfer consent.
            conn.execute("UPDATE test_results SET result_detail=? WHERE player_id='1'", (json.dumps(detail),))
        self.denied(self.read)
        for detail in ('{}', 'null', 'broken', json.dumps({'answers': [], 'scores': {}, 'session_id': 'x'})):
            with sqlite3.connect(self.sessions) as conn:
                conn.execute("UPDATE test_results SET result_detail=? WHERE player_id='1'", (detail,))
            self.denied(self.read)
        with sqlite3.connect(self.sessions) as conn:
            conn.execute('DELETE FROM test_results')
        self.denied(self.read)

    def test_multiple_humans_and_identity_spoofing(self):
        self.complete(1)
        self.complete(2)
        self.grant(user=1)
        self.denied(lambda: self.read(human=2))
        self.grant(user=2)
        self.assertEqual(self.read(human=2)['human_id'], 2)
        for human in (3, 999999, 2**80, '人类甲', '1', True, [], None):
            self.denied(lambda: self.read(human=human))
        self.denied(lambda: self.read(12, player_id='11', ai_id=11, token=self.tokens[11]))
        result = self.read(player_id='2', ai_id=12, action='answer', game='mbti', answers=[1]*22)
        self.assertEqual(result['human_id'], 1)
        self.denied(lambda: self.grant(13))
        self.denied(lambda: self.web('sharing', {'enabled': True, 'ai_id': 11}, user=11))
        self.denied(lambda: server._human_test_action('aidep', 'sharing', '', {'player_id': '1'}), -32001)
        settings = self.web('sharing')
        self.web('sharing', {**settings, 'enabled': False, 'human_id': 2, 'player_id': '2'})
        self.denied(self.read)
        self.assertTrue(self.read(human=2)['ok'])

    def http(self, path, body=None, token=''):
        handler = object.__new__(server.CedarToyHandler)
        handler.path, handler.client_address = path, ('127.0.0.1', 12345)
        raw = json.dumps(body).encode() if body is not None else b''
        handler.headers = {'Content-Length': str(len(raw)), 'Content-Type': 'application/json'}
        if token:
            handler.headers['Authorization'] = 'Bearer ' + token
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        (handler.do_POST if body is not None else handler.do_GET)()
        headers = dict(c.args for c in handler.send_header.call_args_list)
        return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue()), headers

    def test_http_settings_auth_no_store_and_persistence(self):
        self.complete()
        for token, status in [('', 401), ('invalid', 401), (self.tokens[11], 403), (self.tokens[1], 200)]:
            response = self.http('/api/aidep/sharing', token=token)
            self.assertEqual(response[0], status)
            self.assertIn('no-store', response[2]['Cache-Control'])
        settings = self.http('/api/aidep/sharing?human_id=2', token=self.tokens[1])[1]
        payload = {**settings, 'enabled': True, 'ai_id': 12, 'human_id': 2, 'player_id': '2'}
        self.assertEqual(self.http('/api/aidep/sharing', payload, self.tokens[1])[0], 200)
        restored = self.http('/api/aidep/sharing', token=self.tokens[1])[1]
        self.assertTrue(restored['enabled'])
        self.assertEqual(restored['ai_id'], 12)
        self.assertIn('no-store', self.http('/api/aidep/result', token=self.tokens[1])[2]['Cache-Control'])
        env = {**os.environ, 'SESSIONS_DB': str(self.sessions), 'TURTLE_SOUP_DB': str(self.accounts)}
        code = "from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs; install_missing_optional_game_stubs(); import json,server,sys; t=json.loads(sys.stdin.read()); print(json.dumps(server._human_test_action('aidep','sharing',t,{})))"
        done = subprocess.run([sys.executable, '-c', code], input=json.dumps(self.tokens[1]), text=True,
                              capture_output=True, env=env, timeout=30, check=True)
        self.assertEqual(json.loads(done.stdout)['ai_id'], 12)

    def test_root_mcp_bearer_path_and_all_old_actions_closed(self):
        self.complete()
        self.grant()
        # Notification delivery is unrelated; authentication and play dispatch remain real.
        with patch.object(server, '_duel_unread_request_reminder', return_value=''), \
             patch.object(server, '_mcp_forced_announcement', return_value=''):
            for path, token in [('/mcp', self.tokens[11]), ('/'+self.tokens[11], '')]:
                payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'play',
                    'arguments': {'game': 'aidep', 'action': 'shared_result', 'params': {'human_id': 1}}}}
                response = self.http(path, payload, token)
                self.assertEqual(response[0], 200)
                self.assertFalse(response[1]['result']['isError'], response)
                self.assertIn('no-store', response[2]['Cache-Control'])
                self.assertEqual(json.loads(response[1]['result']['content'][0]['text'])['human_id'], 1)
                for action in ('start', 'answer', 'answer_batch', 'status', 'get_result', 'result', 'compare'):
                    payload['params']['arguments']['action'] = action
                    response = self.http(path, payload, token)
                    self.assertTrue(response[1]['result']['isError'])
                    self.assertIn('未知游戏', response[1]['result']['content'][0]['text'])
                    self.assertEqual(self.http('/api/aidep/'+action, {'mode': 'full'}, self.tokens[11])[0],
                                     404 if action == 'compare' else 403)
        self.assertNotIn('aidep', server._tool_list_games())


if __name__ == '__main__':
    unittest.main()
