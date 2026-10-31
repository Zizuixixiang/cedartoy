"""The public proxy must rederive spectator identity and bindings every request."""
import base64
import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import server


class DuelSpectatorProxyTests(unittest.TestCase):
    def test_binding_is_read_from_current_account_database_each_time(self):
        with tempfile.TemporaryDirectory(prefix='duel-spectator-account-') as tmp:
            path = Path(tmp) / 'accounts.db'
            def connect():
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                return conn
            with connect() as conn:
                conn.executescript('''
                    CREATE TABLE toy_users(id INTEGER,username TEXT,is_ai INTEGER,
                        avatar_type TEXT,avatar_value TEXT,deleted_at TEXT);
                    CREATE TABLE user_bindings(human_user_id INTEGER,ai_user_id INTEGER);
                    INSERT INTO toy_users VALUES (101,'自家',1,NULL,NULL,NULL),
                        (102,'别家',1,NULL,NULL,NULL),(103,'已删除',1,NULL,NULL,'deleted');
                    INSERT INTO user_bindings VALUES (7,101),(8,102),(7,103);
                ''')
            handler = object.__new__(server.CedarToyHandler)
            with patch.object(server, '_current_account', return_value={'id': 7, 'username': '主人', 'is_ai': 0}), \
                    patch.object(server, '_db_connect', side_effect=connect):
                first = handler._duel_human_context('authenticated-token')
                self.assertEqual([m['id'] for m in first['machines']], ['101'])
                with connect() as conn:
                    conn.execute('DELETE FROM user_bindings WHERE ai_user_id=101')
                self.assertEqual(handler._duel_human_context('authenticated-token')['machines'], [])

    def test_list_and_poll_strip_forged_headers_and_forward_only_requested_perspective(self):
        target = {'human_player': '7', 'human_name': '主人', 'human_avatar': None,
                  'machines': [{'id': '101', 'name': '自家'}]}
        for path in ('/api/spectating/rooms', '/api/rooms/ABCDEFGH'):
            self.assertTrue(server._duel_proxy_allowed('GET', path))
            handler = object.__new__(server.CedarToyHandler)
            handler.command = 'GET'
            handler.client_address = ('127.0.0.1', 12345)
            handler.headers = {'X-Duel-Human-Player': 'forged', 'X-Duel-Bound-Ais': 'forged',
                               'X-Duel-Ai-Player': '999'}
            handler.rfile, handler.wfile = BytesIO(), BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            response = Mock(status=200, reason='OK')
            response.read.return_value = b'{"ok": true}'
            response.getheaders.return_value = [('Content-Type', 'application/json')]
            response.getheader.return_value = 'application/json'
            with patch.object(server.http.client, 'HTTPConnection') as connection:
                connection.return_value.getresponse.return_value = response
                handler._proxy_to_duel('GET', path, 'player_id=999&opponent_id=999&perspective=101', target=target)
            call = connection.return_value.request.call_args
            params = parse_qs(urlsplit(call.args[1]).query)
            self.assertEqual(params, {'player_id': ['7'], 'perspective': ['101']})
            headers = call.kwargs['headers']
            self.assertEqual(headers['X-Duel-Human-Player'], '7')
            self.assertEqual(json.loads(base64.urlsafe_b64decode(headers['X-Duel-Bound-Ais'] + '==')), target['machines'])
            self.assertNotIn('X-Duel-Ai-Player', headers)
        self.assertFalse(server._duel_proxy_allowed('POST', '/api/spectating/rooms'))
