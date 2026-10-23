"""HTTP routing isolation; fake accounts and upstream writes live only in memory."""
import io
import json
import sqlite3
import unittest
import urllib.parse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import Mock, patch

import server


class GamePageSlotTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE toy_users(id INTEGER, username TEXT, is_ai INTEGER, deleted_at TEXT);
            CREATE TABLE user_bindings(human_user_id INTEGER, ai_user_id INTEGER);
            INSERT INTO toy_users VALUES(101,'A',1,NULL),(202,'B',1,NULL),(303,'unbound',1,NULL);
            INSERT INTO user_bindings VALUES(1,101),(1,202);
        ''')
        self.stack.enter_context(patch.object(server, '_db_connect', return_value=self.db))
        self.stack.enter_context(patch.object(server, '_current_account', side_effect=self.account))
        self.stack.enter_context(patch.object(server, '_record_web_game_activity'))
        self.stack.enter_context(patch.object(server, '_game_maintenance', return_value=None))
        self.stack.enter_context(patch.object(server.detroit_adapter, 'fetch_public', return_value=(200, 'text/html', b'<html>fixture</html>')))
        self.api = self.stack.enter_context(patch.object(server.detroit_adapter, 'browser_api', side_effect=self.detroit_api))
        self.saves = {}
        self.targets = []

    @staticmethod
    def account(token):
        if token != 'fixture':
            raise server._McpError(-32001, 'expired')
        return {'id': 1, 'username': 'fixture human', 'is_ai': False}

    def detroit_api(self, player, method, endpoint, *, query, payload):
        self.targets.append(('detroit', player, method))
        if method == 'POST':
            self.saves[('detroit', player)] = payload['marker']
        return 200, {'content-type': 'application/json'}, json.dumps({'marker': self.saves.get(('detroit', player))}).encode()

    def handler(self, path, cookie, method='GET', body=None):
        h = object.__new__(server.CedarToyHandler)
        raw = json.dumps(body or {}).encode()
        h.path, h.command = path, method
        h.headers = {'Cookie': cookie, 'Content-Length': str(len(raw)), 'Content-Type': 'application/json'}
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.send_response, h.send_header, h.end_headers = Mock(), Mock(), Mock()
        h._send_json = Mock()
        h._send_detroit_bytes = Mock()
        def camping_proxy(method, path, query, **kwargs):
            player = kwargs['target']['player']
            self.targets.append(('camping_plaza', player, method))
            if method == 'POST':
                self.saves[('camping_plaza', player)] = json.loads(h.rfile.read())['marker']
        h._proxy_to_camping_plaza = Mock(side_effect=camping_proxy)
        return h

    def call(self, game, player, *, method='GET', marker=None, cookie_player='202:5', token='fixture'):
        prefix = '/detroit' if game == 'detroit' else '/camping-plaza'
        endpoint = '/api/session?id=cedartoy-slot' if game == 'detroit' else '/api/state?session_id=cedartoy'
        if method == 'POST':
            endpoint = '/api/action?session_id=attacker'
        query = '' if player is None else '&' + urllib.parse.urlencode({'player': player})
        h = self.handler(prefix + endpoint + query,
                         f'{game}_token={token}; {game}_player={urllib.parse.quote(cookie_player)}',
                         method, {'marker': marker, 'player_id': '303', 'session_id': 'attacker'})
        if game == 'detroit':
            # POST dispatch intentionally passes no params: exercise parsing self.path.
            h._handle_detroit_api(method, prefix + endpoint.split('?')[0])
        else:
            h._handle_camping_plaza_proxy(method)
        return h

    def test_tabs_keep_read_write_targets_across_other_tab_switches(self):
        for game in ('detroit', 'camping_plaza'):
            for a, b in (('101:5', '101'), ('101:5', '202:2')):
                for player, marker, other in ((a, 'A', b), (b, 'B', a)):
                    h = self.call(game, player, method='POST', marker=marker, cookie_player=other)
                    h._send_json.assert_not_called()
                    self.assertEqual(self.targets[-1], (game, player, 'POST'))
                for player, expected, other in ((a, 'A', b), (b, 'B', a)):
                    self.call(game, player, cookie_player=other)
                    self.assertEqual(self.targets[-1], (game, player, 'GET'))
                    self.assertEqual(self.saves[(game, player)], expected)

    def test_missing_unbound_invalid_duplicate_and_expired_requests_never_forward(self):
        for game in ('detroit', 'camping_plaza'):
            for method in ('GET', 'POST'):
                for player in (None, '', '303', '101:0', '101:6', '101:x', '101:2:3', 'guest:any'):
                    with self.subTest(game=game, method=method, player=player):
                        count = len(self.targets)
                        h = self.call(game, player, method=method)
                        self.assertEqual(h._send_json.call_args.kwargs['status'], 403)
                        self.assertEqual(len(self.targets), count)
                h = self.call(game, '101:5', method=method, token='expired')
                self.assertEqual(h._send_json.call_args.kwargs['status'], 401)
                endpoint = '/api/action' if method == 'POST' else '/api/state'
                h = self.handler('/' + game.replace('_', '-') + endpoint + '?player=101&player=202', f'{game}_token=fixture')
                if game == 'detroit': h._handle_detroit_api(method, '/detroit/api/session')
                else: h._handle_camping_plaza_proxy(method)
                self.assertEqual(h._send_json.call_args.kwargs['status'], 403)

    def test_detroit_refresh_and_entry_redirect_keep_player_without_cookie_target(self):
        for player in ('101', '101:5', '202:2'):
            h = self.handler('/detroit/', 'detroit_token=fixture; detroit_player=303')
            h._handle_detroit_get('/detroit/', {'token': ['fixture'], 'player': [player]})
            location = dict(h.send_header.call_args_list[i].args for i in range(len(h.send_header.call_args_list)))['Location']
            self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(location).query), {'player': [player]})
            refreshed = self.handler(location, 'detroit_token=fixture; detroit_player=202')
            refreshed._handle_detroit_get('/detroit/', {'player': [player]})
            refreshed._send_json.assert_not_called()
            self.assertEqual(refreshed._send_detroit_bytes.call_args.args[0], 200)
        legacy = self.handler('/detroit/', 'detroit_token=fixture; detroit_player=101')
        legacy._handle_detroit_get('/detroit/', {})
        self.assertEqual(legacy._send_json.call_args.kwargs['status'], 400)


class GamePageSlotPersistenceTests(unittest.TestCase):
    def test_three_processes_preserve_each_player_and_slot(self):
        """Real camping snapshots and Detroit mappings; remote service is mocked."""
        program = r'''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
import camping_plaza_adapter as camping
import detroit_adapter as detroit
root = Path(os.environ['SLOT_FIXTURE_ROOT'])
detroit.SAVE_ROOT = root / 'vendor_saves'
players = ['101:5', '101', '202:2']
names = ['Alpha', 'Beta', 'Gamma']
step = sys.argv[1]
for i, (player, name) in enumerate(zip(players, names)):
    token = camping._PLAYER_ID.set(player)
    try:
        if step == 'create':
            camping.upstream.mcp_state()
            detroit._write_state_unlocked(player, {'version': 1, 'owner_cookie': 'fixture-'+name,
                'mcp_url': 'https://example.invalid/unused', 'save_id': 'save_abcdefghij'+str(i)})
        elif step == 'write':
            camping.upstream.set_player_name(camping.upstream.SetPlayerNameRequest(name=name, session_id='other-slot'))
            state = detroit._read_state_unlocked(player)
            result = {'save': {'id': state['save_id'], 'name': name}}
            class Response:
                status_code = 200
                headers = {'content-type': 'application/json'}
                content = json.dumps(result).encode()
                def json(self): return result
            with patch.object(detroit, '_ensure_owner_and_connection_unlocked', side_effect=lambda p,s:s), patch.object(detroit, '_browser_call_unlocked', return_value=Response()) as upstream:
                detroit.browser_api(player, 'POST', 'action', payload={'id': detroit.PUBLIC_SAVE_ID})
                assert upstream.call_args.args[0]['owner_cookie'] == 'fixture-'+name
                assert upstream.call_args.kwargs['payload']['id'] == state['save_id']
        else:
            assert camping.upstream.get_state(session_id='other-slot')['player_name'] == name
            state = detroit._read_state_unlocked(player)
            assert state['owner_cookie'] == 'fixture-'+name
            assert state['summary']['name'] == name
            assert state['save_id'] == 'save_abcdefghij'+str(i)
    finally:
        camping._PLAYER_ID.reset(token)
print(step+' passed: three separate identities')
'''
        with tempfile.TemporaryDirectory(prefix='page-slot-persistence-') as temp:
            env = {**os.environ, 'SLOT_FIXTURE_ROOT': temp,
                   'CAMPING_PLAZA_DB_PATH': str(Path(temp) / 'camping.db'),
                   'TOY_SECRET': 'isolated-page-slot-persistence'}
            for step in ('create', 'write', 'read'):
                result = subprocess.run([sys.executable, '-c', program, step],
                                        cwd=Path(__file__).resolve().parents[1], env=env,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
