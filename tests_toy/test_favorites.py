"""Favorites use real credentials and isolated account storage, never production data."""
import io
import json
import re
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
from cedar_backend import favorites
from tests_toy import test_account_rename


class FavoritesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_account_rename.AccountRenameTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.human = self.fixture.human_token
        self.ai = self.fixture.machine_token
        self.other = self.fixture._token_for(self.fixture.other_machine_id)
        self.human2 = self.fixture._token_for(self.fixture.other_human_id)
        # Disable unrelated request decorations, not authentication or dispatch.
        for name in ('_mcp_forced_announcement', '_feedback_unread_reminder'):
            patcher = patch.object(server, name, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, action='favorites', game=None, token=None, **extra):
        return json.loads(server._tool_list_games(
            path_token=token or self.ai, arguments={'action': action, 'game': game, **extra}))

    def ids(self, result):
        return [g['id'] for g in result['self']['games']]

    def http(self, path, *, method='POST', token='', body=None):
        raw = json.dumps(body or {}).encode()
        handler = object.__new__(server.CedarToyHandler)
        handler.path, handler.client_address = path, ('127.0.0.1', 12345)
        handler.headers = {'Content-Length': str(len(raw)), 'Authorization': 'Bearer ' + token}
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        getattr(handler, 'do_' + method)()
        return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue())

    def test_independent_idempotent_ownership_and_direct_bindings(self):
        for _ in range(2):
            self.call('favorite_add', 'duel', token=self.human)
            self.call('favorite_add', 'eco', user_id=self.fixture.human_id, human=True)
        self.assertEqual(self.ids(self.call()), ['eco'])
        self.assertEqual(self.call()['humans'], [{'username': 'HumanOne', 'games': [{'id': 'duel', 'name': '双弈'}]}])
        self.assertNotIn('humans', self.call(token=self.human))
        self.assertNotIn('humans', self.call(token=self.other))
        with self.fixture._connect() as conn:
            conn.executemany('INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)', [
                (self.fixture.other_human_id, self.fixture.machine_id),
                (self.fixture.human_id, self.fixture.other_machine_id)])
        self.call('favorite_add', 'mbti', token=self.human2)
        self.call('favorite_add', 'nowhere', token=self.other)
        result = self.call()
        self.assertEqual([h['username'] for h in result['humans']], ['HumanOne', 'OtherHuman'])
        self.assertNotIn('nowhere', json.dumps(result))
        for _ in range(2):
            self.assertEqual(self.ids(self.call('favorite_remove', 'duel')), ['eco'])
        self.assertEqual(self.ids(self.call(token=self.human)), ['duel'])
        with self.fixture._connect() as conn:
            conn.execute('DELETE FROM user_bindings WHERE ai_user_id = ?', (self.fixture.machine_id,))
            conn.execute('UPDATE toy_users SET username = ? WHERE id = ?', ('Renamed', self.fixture.machine_id))
        self.assertEqual(self.call()['self']['username'], 'Renamed')
        self.assertNotIn('humans', self.call())
        self.assertEqual(self.ids(self.call('favorite_remove', 'eco')), [])

    def test_real_path_and_bearer_tokens_and_anonymous_rejection(self):
        for path, token in (('/' + self.ai, ''), ('/mcp', self.ai), ('/' + self.ai, self.human)):
            status, payload = self.http(path, token=token, body={'jsonrpc': '2.0', 'id': 1,
                'method': 'tools/call', 'params': {'name': 'list_games', 'arguments': {
                    'action': 'favorite_add', 'game': 'duel', 'user_id': self.fixture.human_id}}})
            self.assertEqual(status, 200)
            self.assertFalse(payload['result']['isError'], payload)
            self.assertEqual(self.ids(json.loads(payload['result']['content'][0]['text'])), ['duel'])
        self.assertEqual(self.ids(self.call(token=self.human)), [])
        for token in ('', 'bad-token'):
            for action in ('favorites', 'favorite_add', 'favorite_remove'):
                with self.assertRaises(server._McpError):
                    server._tool_list_games(path_token=token, arguments={
                        'action': action, 'game': 'duel', 'token': self.ai, 'user_id': self.fixture.machine_id})
        with self.fixture._connect() as conn:
            conn.execute('UPDATE ai_access_tokens SET revoked_at_epoch = 1 WHERE user_id = ?', (self.fixture.machine_id,))
        with self.assertRaises(server._McpError):
            self.call('favorite_add', 'eco')

    def test_web_persistence_identity_and_validation(self):
        for _ in range(2):
            status, data = self.http('/api/auth/favorites', token=self.human,
                body={'action': 'favorite_add', 'game': 'turtle_soup', 'user_id': self.fixture.machine_id})
            self.assertEqual(status, 200)
        status, data = self.http('/api/auth/favorites', method='GET', token=self.human)
        self.assertEqual(self.ids(data), ['turtle_soup'])
        # Fresh SQLite connection sees the committed collection.
        with sqlite3.connect(self.fixture.db_path) as conn:
            self.assertEqual(conn.execute('SELECT user_id, game_id FROM account_game_favorites').fetchall(),
                             [(self.fixture.human_id, 'turtle_soup')])
        for token in ('', self.ai):
            self.assertEqual(self.http('/api/auth/favorites', method='GET', token=token)[0], 401)
        for game in ('missing', 'soup', 'admin', '', None, [], {'id': 'duel'}):
            with self.assertRaises(server._McpError):
                self.call('favorite_add', game)
        for action in ('hide', '', None, []):
            with self.assertRaises(server._McpError):
                self.call(action, 'duel')
        self.assertEqual(self.http('/api/auth/favorites', token=self.human,
            body={'action': 'favorite_remove', 'game': 'turtle_soup'})[1]['self']['games'], [])

    def test_schema_idempotence_cascade_and_catalog_coverage(self):
        self.call('favorite_add', 'duel', token=self.other)
        with self.fixture._connect() as conn:
            favorites.init_schema(conn)
            favorites.init_schema(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM account_game_favorites').fetchone()[0], 1)
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('DELETE FROM ai_access_tokens WHERE user_id = ?', (self.fixture.other_machine_id,))
            conn.execute('DELETE FROM toy_users WHERE id = ?', (self.fixture.other_machine_id,))
            self.assertEqual(conn.execute('SELECT count(*) FROM account_game_favorites').fetchone()[0], 0)
        home = Path('index.html').read_text()
        catalog = home[home.index('    const games = ['):home.index('    let selected = "soup";')]
        ids = json.loads(subprocess.check_output(['node', '-e', catalog +
            ';console.log(JSON.stringify(games.filter(g=>!g.adminOnly).map(g=>g.id)))'], text=True))
        ids += re.findall(r"(?:^|[ |:])([a-z][a-z0-9_]*)[·（]", server._tool_list_games(), re.MULTILINE)
        for game in ids:
            self.assertIn({'soup': 'turtle_soup', 'attribute': 'bdsmtest'}.get(game, game), favorites.GAME_NAMES)

    def test_default_index_and_guide_are_on_demand(self):
        before = server._tool_list_games()
        self.call('favorite_add', 'duel')
        self.assertEqual(server._tool_list_games(path_token=self.ai), before)
        self.assertNotIn('HumanOne', before)
        for mode in ('standard', 'legacy'):
            schema = next(t for t in server._root_tools(schema_mode=mode) if t['name'] == 'list_games')
            self.assertEqual(schema['inputSchema']['properties'], {'action': {'type': 'string'}, 'game': {'type': 'string'}})
        guide = json.loads(server._tool_get_guide({'game': 'list_games'}))['guide']
        for text in ('list_games()', 'favorite_add', 'favorite_remove', '直接绑定', 'Bearer'):
            self.assertIn(text, guide)
