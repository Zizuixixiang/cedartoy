"""Real HTTP/SQLite regression, isolated from live services and model providers."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
import database
import mcp_app
import sse
from auth_utils import create_token
from routers import auth, game, notes, rooms
from room_access import LOCKED_ROOM_MESSAGE


class LockedRoomsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soup-lock-test-")
        self.old_db = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "soup.db"
        await database.init_db()
        db = await database.get_db()
        await db.executescript("""
            CREATE TABLE toy_users (
                id INTEGER PRIMARY KEY, username TEXT, is_ai INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0, deleted_at TEXT,
                deletion_requested_at_epoch INTEGER, ai_token_version INTEGER DEFAULT 0,
                last_active_at TEXT, created_at TEXT
            );
            CREATE TABLE user_bindings (
                id INTEGER PRIMARY KEY, human_user_id INTEGER, ai_user_id INTEGER,
                created_at TEXT
            );
            CREATE TABLE ai_access_tokens (
                token_hash TEXT, user_id INTEGER, generation INTEGER,
                format_version INTEGER, revoked_at_epoch INTEGER
            );
            INSERT INTO toy_users(id, username, is_ai) VALUES
                (101, 'owner', 0), (102, 'own_ai', 1), (103, 'own_ai_two', 1),
                (201, 'other', 0), (202, 'other_ai', 1), (203, 'unbound_ai', 1);
            INSERT INTO user_bindings(human_user_id, ai_user_id) VALUES
                (101, 102), (101, 103), (201, 202);
        """)
        await db.commit()
        await db.close()
        app = FastAPI()
        for router in [auth.router, rooms.router, game.router, notes.router, sse.router]:
            app.include_router(router, prefix="/soup/api")
        app.include_router(mcp_app.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://soup.test")
        self.platform = {}
        self.tokens = {}
        self.players = {}
        for uid in [101, 102, 103, 201, 202, 203]:
            if uid in [102, 103, 202, 203]:
                token = 'ctai_v1_' + str(uid) * 15
                await database.execute(
                    'INSERT INTO ai_access_tokens VALUES (?, ?, 0, 1, NULL)',
                    (mcp_app._opaque_ai_token_hash(token), uid),
                )
            else:
                token = mcp_app._jwt_encode({'user_id': uid, 'is_ai': False})
            self.platform[uid] = token
            res = await self.client.post('/soup/api/auth/guest', json={'user_id': uid}, headers={'Authorization': f'Bearer {token}'})
            self.assertEqual(res.status_code, 200, res.text)
            self.tokens[uid] = res.json()['token']
            self.players[uid] = res.json()['player']
        guest = await self.client.post('/soup/api/auth/guest', json={})
        self.tokens[0] = guest.json()['token']
        self.players[0] = guest.json()['player']

    async def asyncTearDown(self):
        await self.client.aclose()
        database.DB_PATH = self.old_db
        self.tmp.cleanup()

    def headers(self, uid):
        return {'Authorization': f'Bearer {self.tokens[uid]}'}

    async def create(self, uid=101, **body):
        res = await self.client.post('/soup/api/rooms/create', headers=self.headers(uid), json={'mode': 'random', **body})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()['room_id']

    async def mcp(self, action, rid=None, uid=None, **kwargs):
        body = {'game': 'turtle_soup', 'action': action, **kwargs}
        if rid: body['room_id'] = rid
        if uid: body['path_token'] = self.platform[uid]
        return await self.client.post('/mcp/play', json=body)

    async def test_public_default_and_anonymous_behavior(self):
        rid = await self.create(0)
        for uid in self.tokens:
            res = await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(uid))
            self.assertEqual(res.status_code, 200)
            self.assertFalse(res.json()['is_locked'])
        for action in ['join', 'status', 'note_list']:
            self.assertEqual((await self.mcp(action, rid)).status_code, 200)

    async def test_locked_persists_lists_and_bound_accounts_enter(self):
        rid = await self.create(is_locked=True)
        await database.init_db()  # repeat startup; each request opens a new connection
        for uid in [101, 102, 103]:
            res = await self.client.get(f'/soup/api/rooms/%23{rid}', headers=self.headers(uid))
            self.assertEqual(res.status_code, 200, res.text)
            self.assertTrue(res.json()['is_locked'])
            self.assertNotIn('lock_owner_user_id', res.json())
            self.assertEqual((await self.mcp('join', rid, uid)).status_code, 200)
            self.assertEqual((await self.mcp('status', rid, uid)).status_code, 200)
        rows = (await self.client.get('/soup/api/rooms/', headers=self.headers(201))).json()
        self.assertTrue(next(r for r in rows if r['id'] == rid)['is_locked'])
        self.assertTrue((await self.mcp('list_rooms', uid=102)).json()[0]['is_locked'])
        # Consistent SQLite snapshot preserves lock state and integrity.
        with sqlite3.connect(database.DB_PATH) as source, sqlite3.connect(Path(self.tmp.name) / 'snapshot.db') as dest:
            source.backup(dest)
            self.assertEqual(dest.execute('SELECT is_locked, lock_owner_user_id FROM rooms WHERE id=?', (rid,)).fetchone(), (1, 101))
            self.assertEqual(dest.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    async def test_list_ownership_is_request_scoped_to_exact_player(self):
        created = {}
        for uid in [101, 102, 201, 0]:
            created[await self.create(uid, is_locked=uid in [101, 102])] = self.players[uid]['id']
        # A second soup identity for the same platform account is still not self.
        other_player = await database.execute(
            "INSERT INTO players(username, user_id) VALUES ('same-account-other-player', 101)"
        )
        await database.execute(
            "INSERT INTO rooms(id, surface, answer, status, created_by) VALUES ('OTHERPID', 'surface', 'secret', 'playing', ?)",
            (other_player,),
        )
        created['OTHERPID'] = other_player
        for uid in [101, 102, 103, 201, 0, 101]:
            with self.subTest(uid=uid):
                res = await self.client.get('/soup/api/rooms/', headers=self.headers(uid))
                self.assertEqual(res.status_code, 200, res.text)
                rows = res.json()
                self.assertEqual({r['id'] for r in rows}, set(created))
                for row in rows:
                    self.assertIs(type(row['is_mine']), int)
                    self.assertEqual(row['is_mine'], int(created[row['id']] == self.players[uid]['id']))
                    self.assertNotIn('answer', row)
                    self.assertNotIn('lock_owner_user_id', row)
        columns = await database.fetch_all('PRAGMA table_info(rooms)')
        self.assertNotIn('is_mine', {column['name'] for column in columns})

    async def test_outsiders_denied_on_every_room_route_without_writes(self):
        rid = await self.create(is_locked=True)
        nid = await database.execute('INSERT INTO room_notes(room_id, player_id, content) VALUES (?, ?, ?)', (rid, self.players[201]['id'], 'old note'))
        before = await database.fetch_one('SELECT COUNT(*) AS n FROM game_logs')
        for uid in [0, 201, 202, 203]:
            for method, path, body in [
                ('GET', f'/rooms/{rid}', None), ('GET', f'/sse/{rid}', None),
                ('POST', '/game/ask', {'room_id': rid, 'content': 'question'}),
                ('POST', '/game/guess', {'room_id': rid, 'content': 'answer'}),
                ('POST', '/game/reveal-answer', {'room_id': rid, 'confirm_reveal': True}),
                ('POST', '/game/hint/request', {'room_id': rid}),
                ('POST', '/game/hint/respond', {'room_id': rid, 'log_id': 1, 'accept': True}),
                ('POST', f'/notes/{rid}', {'content': 'note'}),
                ('PUT', f'/notes/{nid}', {'content': 'note'}),
                ('DELETE', f'/notes/{nid}', None),
                ('POST', f'/rooms/{rid}/close', {}),
            ]:
                res = await self.client.request(method, '/soup/api' + path, headers=self.headers(uid), **({'json': body} if body is not None else {}))
                self.assertEqual(res.status_code, 403, (uid, path, res.text))
                self.assertEqual(res.json()['detail'], LOCKED_ROOM_MESSAGE)
            for action in ['join', 'status', 'note_list', 'ask', 'guess', 'hint', 'reveal_answer', 'note_add', 'close_room']:
                res = await self.mcp(action, rid, uid or None, content='test', confirm_reveal=True)
                self.assertEqual(res.status_code, 403, (uid, action, res.text))
        self.assertEqual(before, await database.fetch_one('SELECT COUNT(*) AS n FROM game_logs'))
        self.assertEqual((await database.fetch_one('SELECT COUNT(*) AS n FROM room_presence'))['n'], 0)

    async def test_unbind_revokes_existing_sse_and_future_access(self):
        rid = await self.create(is_locked=True)
        player = await mcp_app._mcp_player(self.platform[102])
        response = await sse.room_events(rid, player)
        stream = response.body_iterator
        self.assertIn('connected', await anext(stream))
        await database.execute('DELETE FROM user_bindings WHERE ai_user_id=102')
        await sse.broadcast(rid, 'new_log', {'content': 'private'})
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        self.assertFalse(sse._connections[rid])
        self.assertEqual((await self.mcp('join', rid, 102)).status_code, 403)
        self.assertEqual((await self.mcp('join', rid, 101)).status_code, 200)

    async def test_admin_readonly_get_has_no_presence_or_other_writes(self):
        rid = await self.create(is_locked=True)
        await database.execute('UPDATE toy_users SET is_admin=1 WHERE id=201')
        await database.execute("UPDATE rooms SET answer='unrevealed-secret' WHERE id=?", (rid,))
        await database.execute(
            "INSERT INTO game_logs(room_id, player_id, type, content) VALUES (?, ?, 'ask', 'existing question')",
            (rid, self.players[101]['id']),
        )
        await database.execute(
            "INSERT INTO room_notes(room_id, player_id, content) VALUES (?, ?, 'private note')",
            (rid, self.players[101]['id']),
        )
        # Even an old personal reveal does not expand the admin viewing scope.
        await database.execute('INSERT INTO room_answer_reveals(room_id, player_id) VALUES (?, ?)', (rid, self.players[201]['id']))
        await database.execute("UPDATE players SET last_active_at='2000-01-01' WHERE user_id=201")
        tables = ['rooms', 'game_logs', 'room_notes', 'room_answer_reveals', 'room_presence', 'players']
        before = {table: await database.fetch_all(f'SELECT * FROM {table}') for table in tables}
        for _ in range(2):
            res = await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(201))
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertIs(data['admin_readonly'], True)
            self.assertEqual(data['active_players'], 0)
            self.assertEqual(data['ask_count'], 1)
            self.assertEqual(data['logs'][-1]['content'], 'existing question')
            for key in ['answer', 'revealed_answer', 'answer_revealed', 'notes', 'lock_owner_user_id']:
                self.assertNotIn(key, data)
            self.assertNotIn('unrevealed-secret', res.text)
            self.assertNotIn('private note', res.text)
        self.assertEqual(before, {table: await database.fetch_all(f'SELECT * FROM {table}') for table in tables})
        # A real member's SSE sees neither an arrival nor any other viewing event.
        member = await mcp_app._mcp_player(self.platform[102])
        stream = (await sse.room_events(rid, member)).body_iterator
        try:
            await anext(stream)
            presence_before = await database.fetch_all('SELECT * FROM room_presence')
            res = await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(201))
            self.assertEqual(res.json()['active_players'], 1)
            self.assertEqual(await database.fetch_all('SELECT * FROM room_presence'), presence_before)
            self.assertEqual(len(sse._connections[rid]), 1)
            self.assertTrue(all(queue.empty() for queue in sse._connections[rid]))
            listed = (await self.client.get('/soup/api/rooms/', headers=self.headers(101))).json()
            self.assertEqual(next(row for row in listed if row['id'] == rid)['active_players'], 1)
        finally:
            await stream.aclose()

    async def test_admin_readonly_writes_and_sse_still_denied(self):
        rid = await self.create(is_locked=True)
        await database.execute('UPDATE toy_users SET is_admin=1 WHERE id=201')
        await database.execute('UPDATE players SET is_admin=1 WHERE user_id=201')
        nid = await database.execute(
            "INSERT INTO room_notes(room_id, player_id, content) VALUES (?, ?, 'old note')",
            (rid, self.players[201]['id']),
        )
        tables = ['rooms', 'game_logs', 'room_notes', 'room_answer_reveals', 'room_presence']
        before = {table: await database.fetch_all(f'SELECT * FROM {table}') for table in tables}
        stats_before = await database.fetch_all('SELECT id, ask_count, win_count, game_count FROM players')
        for method, path, body in [
            ('GET', f'/sse/{rid}', None),
            ('POST', '/game/ask', {'room_id': rid, 'content': 'question'}),
            ('POST', '/game/guess', {'room_id': rid, 'content': 'guess'}),
            ('POST', '/game/hint/request', {'room_id': rid}),
            ('POST', '/game/hint/respond', {'room_id': rid, 'log_id': 1, 'accept': True}),
            ('POST', '/game/hint/respond', {'room_id': rid, 'log_id': 1, 'accept': False}),
            ('POST', '/game/reveal-answer', {'room_id': rid, 'confirm_reveal': True}),
            ('POST', f'/notes/{rid}', {'content': 'note'}),
            ('PUT', f'/notes/{nid}', {'content': 'changed'}),
            ('DELETE', f'/notes/{nid}', None),
            ('POST', f'/rooms/{rid}/close', {}),
        ]:
            res = await self.client.request(method, '/soup/api' + path, headers=self.headers(201), **({'json': body} if body is not None else {}))
            self.assertEqual(res.status_code, 403, (path, res.text))
        for action in ['join', 'status', 'note_list', 'ask', 'guess', 'hint', 'reveal_answer', 'note_add', 'close_room']:
            res = await self.mcp(action, rid, 201, content='test', confirm_reveal=True)
            self.assertEqual(res.status_code, 403, (action, res.text))
        self.assertEqual(before, {table: await database.fetch_all(f'SELECT * FROM {table}') for table in tables})
        self.assertEqual(stats_before, await database.fetch_all('SELECT id, ask_count, win_count, game_count FROM players'))
        self.assertFalse(sse._connections.get(rid))

    async def test_admin_readonly_requires_verified_live_platform_admin(self):
        rid = await self.create(is_locked=True)
        await database.execute('UPDATE players SET is_admin=1 WHERE user_id=201')
        player = await database.fetch_one('SELECT * FROM players WHERE user_id=201')
        self.tokens[201] = create_token(player, verified_user_id=201)  # stale JWT admin bit
        self.assertEqual((await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(201))).status_code, 403)
        await database.execute('UPDATE toy_users SET is_admin=1 WHERE id=201')
        for verified in [None, 101]:
            token = create_token(player, verified_user_id=verified)
            res = await self.client.get(f'/soup/api/rooms/{rid}', headers={'Authorization': f'Bearer {token}'})
            self.assertEqual(res.status_code, 403)
        self.assertEqual((await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(201))).status_code, 200)
        for column, value in [('is_admin', 0), ('deleted_at', 'deleted'), ('deletion_requested_at_epoch', 1)]:
            await database.execute(f'UPDATE toy_users SET {column}=? WHERE id=201', (value,))
            self.assertEqual((await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(201))).status_code, 403)
            await database.execute(f'UPDATE toy_users SET {column}=? WHERE id=201', (1 if column == 'is_admin' else None,))
        self.assertEqual((await self.client.get('/soup/api/rooms/MISSING', headers=self.headers(201))).status_code, 404)
        self.assertEqual((await self.client.get(f'/soup/api/rooms/{rid}')).status_code, 401)

    async def test_admin_household_and_public_rooms_keep_member_mode(self):
        rid = await self.create(is_locked=True)
        public_rid = await self.create(201)
        for uid, room_id in [(101, rid), (102, rid), (201, public_rid)]:
            await database.execute('UPDATE toy_users SET is_admin=1 WHERE id=?', (uid,))
            res = await self.client.get(f'/soup/api/rooms/{room_id}', headers=self.headers(uid))
            self.assertEqual(res.status_code, 200, res.text)
            self.assertIs(res.json()['admin_readonly'], False)
            self.assertNotIn('answer', res.json())
            self.assertIn('notes', res.json())
            res = await self.client.post(f'/soup/api/notes/{room_id}', headers=self.headers(uid), json={'content': 'member note'})
            self.assertEqual(res.status_code, 200, res.text)
            player = await database.fetch_one('SELECT * FROM players WHERE id=?', (self.players[uid]['id'],))
            player['verified_user_id'] = uid
            stream = (await sse.room_events(room_id, player)).body_iterator
            try:
                self.assertIn('connected', await anext(stream))
                self.assertIsNotNone(await database.fetch_one('SELECT 1 FROM room_presence WHERE room_id=? AND player_id=?', (room_id, player['id'])))
            finally:
                await stream.aclose()

    async def test_ai_creator_uses_same_household_and_live_account_state(self):
        rid = await self.create(102, is_locked=True)
        for uid in [101, 102, 103]:
            self.assertEqual((await self.mcp('join', rid, uid)).status_code, 200)
        await database.execute("UPDATE toy_users SET deleted_at='deleted' WHERE id=102")
        self.assertEqual((await self.mcp('join', rid, 101)).status_code, 403)

    async def test_unbound_verified_owner_can_create_and_enter(self):
        rid = await self.create(203, is_locked=True)
        self.assertEqual((await self.mcp('join', rid, 203)).status_code, 200)
        self.assertEqual((await self.mcp('join', rid, 101)).status_code, 403)

    async def test_unverified_guest_and_spoofed_ids_cannot_create_or_enter(self):
        rid = await self.create(is_locked=True)
        legacy_token = create_token(await database.fetch_one('SELECT * FROM players WHERE user_id=101'))
        for token in [self.tokens[0], legacy_token]:
            headers = {'Authorization': f'Bearer {token}'}
            self.assertEqual((await self.client.post('/soup/api/rooms/create', headers=headers, json={'is_locked': True})).status_code, 403)
            self.assertEqual((await self.client.get(f'/soup/api/rooms/{rid}', headers=headers)).status_code, 403)
        for token, expected in [(None, 401), (self.platform[201], 403), (self.tokens[101], 401)]:
            res = await self.client.post('/soup/api/auth/guest', json={'user_id': 101}, headers={'Authorization': f'Bearer {token}'} if token else {})
            self.assertEqual(res.status_code, expected)
        res = await self.client.post('/soup/api/rooms/create', headers=self.headers(0), json={'is_locked': True, 'user_id': 101, 'verified_user_id': 101})
        self.assertEqual(res.status_code, 403)

    async def test_all_create_modes_and_mcp_persist_lock(self):
        for mode in ['random', 'custom', 'generated']:
            with patch('routers.rooms.scan_text', return_value=None):
                rid = await self.create(is_locked=True, mode=mode, surface='surface', answer='answer')
            self.assertEqual((await database.fetch_one('SELECT is_locked FROM rooms WHERE id=?', (rid,)))['is_locked'], 1)
            await self.client.post(f'/soup/api/rooms/{rid}/close', headers=self.headers(101))
        res = await self.mcp('create_random', uid=102, is_locked=True)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()['is_locked'])

    async def test_mcp_create_modes_lock_defaults_and_access(self):
        for action in ['create_random', 'create_custom']:
            for lock_params in [{}, {'is_locked': False}, {'is_locked': True}]:
                with self.subTest(action=action, params=lock_params):
                    with patch('routers.rooms.scan_text', return_value=None):
                        res = await self.mcp(action, uid=102, surface='surface', answer='answer', **lock_params)
                    self.assertEqual(res.status_code, 200, res.text)
                    row = res.json()
                    locked = lock_params.get('is_locked', False)
                    self.assertEqual(row['is_locked'], int(locked))
                    self.assertNotIn('lock_owner_user_id', row)
                    stored = await database.fetch_one('SELECT * FROM rooms WHERE id=?', (row['id'],))
                    self.assertEqual(stored['is_locked'], int(locked))
                    self.assertEqual(stored['created_by'], self.players[102]['id'])
                    for uid in [102, 101, 103, 202]:
                        for read_action in ['join', 'status']:
                            entered = await self.mcp(read_action, row['id'], uid)
                            self.assertEqual(entered.status_code, 403 if locked and uid == 202 else 200, entered.text)
                    closed = await self.mcp('close_room', row['id'], 102)
                    self.assertEqual(closed.status_code, 200, closed.text)

    async def test_mcp_list_requires_valid_identity_without_creating_guests(self):
        before = await database.fetch_one('SELECT COUNT(*) AS n FROM players')
        for token in [None, '', 'invalid-token']:
            res = await self.mcp('list_rooms', path_token=token)
            self.assertEqual(res.status_code, 401, res.text)
        self.assertEqual(await database.fetch_one('SELECT COUNT(*) AS n FROM players'), before)

    async def test_mcp_list_mine_sorting_and_identity_isolation(self):
        alternate = await database.execute(
            "INSERT INTO players(username, user_id) VALUES ('alternate-ai-player', 102)"
        )
        fixtures = [
            ('MINEOLD', self.players[102]['id'], 'waiting', 1, '2026-01-01'),
            ('MINENEW', self.players[102]['id'], 'playing', 0, '2026-01-02'),
            ('HUMAN', self.players[101]['id'], 'playing', 1, '2026-01-03'),
            ('BOUND', self.players[103]['id'], 'waiting', 0, '2026-01-04'),
            ('OTHER', self.players[202]['id'], 'playing', 1, '2026-01-05'),
            ('ALTPID', alternate, 'playing', 0, '2026-01-06'),
            ('FINISHED', self.players[102]['id'], 'finished', 1, '2026-01-07'),
        ]
        for rid, pid, status, locked, created in fixtures:
            await database.execute(
                "INSERT INTO rooms(id, created_by, status, is_locked, created_at, surface, answer) VALUES (?, ?, ?, ?, ?, 'surface', 'secret')",
                (rid, pid, status, locked, created),
            )
            # INSERT triggers set local time; assign distinct fixture times afterward.
            await database.execute('UPDATE rooms SET created_at=? WHERE id=?', (created, rid))
        for uid in [102, 202, 103, 203, 102]:
            with self.subTest(uid=uid):
                # Self-reported identity must not affect ownership.
                res = await self.mcp('list_rooms', uid=uid, player_id=alternate, user_id=101)
                self.assertEqual(res.status_code, 200, res.text)
                rows = res.json()
                expected = sorted(
                    [f for f in fixtures if f[2] != 'finished'],
                    key=lambda f: (f[1] == self.players[uid]['id'], f[4]), reverse=True,
                )
                self.assertEqual([row['id'] for row in rows], [f[0] for f in expected])
                for row, fixture in zip(rows, expected):
                    self.assertEqual(row['is_mine'], int(fixture[1] == self.players[uid]['id']))
                    self.assertIn(type(row['is_mine']), (int, bool))
                    self.assertEqual(row['is_locked'], fixture[3])
                    self.assertNotIn('lock_owner_user_id', row)
                    self.assertNotIn('answer', row)

    async def test_my_rooms_identity_live_bindings_and_public_fields(self):
        for uid in [101, 102, 103, 201, 202, 203]:
            await database.execute(
                "INSERT INTO rooms(id, title, surface, answer, status, created_by, is_locked, lock_owner_user_id) VALUES (?, 'title', 'surface', 'secret', 'playing', ?, 1, ?)",
                (f'ROOM{uid}', self.players[uid]['id'], uid),
            )

        async def check(uid, expected):
            res = await self.mcp('my_rooms', uid=uid, player_id=self.players[103]['id'], user_id=103)
            self.assertEqual(res.status_code, 200, res.text)
            rows = res.json()
            self.assertEqual({r['id'] for r in rows}, {f'ROOM{i}' for i in expected})
            for row in rows:
                creator = int(row['id'][4:])
                self.assertEqual(set(row), {
                    'id', 'title', 'surface', 'status', 'is_locked', 'creator_name',
                    'creator_type', 'created_at', 'last_active_at',
                })
                self.assertEqual(row['creator_type'], 'self' if creator == uid else 'human')
                self.assertEqual(row['creator_name'], self.players[creator]['username'])
                self.assertEqual(row['is_locked'], 1)

        for uid, expected in [(102, [102, 101]), (103, [103, 101]), (202, [202, 201]), (203, [203]), (102, [102, 101])]:
            await check(uid, expected)
        await database.execute('INSERT INTO user_bindings(human_user_id, ai_user_id) VALUES (201, 102)')
        await check(102, [102, 101, 201])
        await check(202, [202, 201])
        await database.execute('DELETE FROM user_bindings WHERE human_user_id=101 AND ai_user_id=102')
        await check(102, [102, 201])
        await check(103, [103, 101])
        # Invalid human accounts cease to count even if a binding row remains.
        for column, value in [('deleted_at', 'deleted'), ('deletion_requested_at_epoch', 1), ('is_ai', 1)]:
            await database.execute(f'UPDATE toy_users SET {column}=? WHERE id=201', (value,))
            await check(102, [102])
            await database.execute(f'UPDATE toy_users SET {column}=? WHERE id=201', (0 if column == 'is_ai' else None,))
        await check(102, [102, 201])
        await database.execute('DELETE FROM user_bindings WHERE ai_user_id=102')
        await check(102, [102])

    async def test_my_rooms_finished_and_activity_order_without_creator_priority(self):
        fixtures = [
            ('HUMACTIVE', 101, 'playing', '2026-01-01', '2026-01-09'),
            ('SELFCREATED', 102, 'waiting', '2026-01-08', None),
            ('HUMCREATED', 101, 'waiting', '2026-01-07', None),
            ('SELFACTIVE', 102, 'playing', '2026-01-02', '2026-01-06'),
            ('SELFFIN', 102, 'finished', '2026-01-10', '2026-01-12'),
            ('HUMFIN', 101, 'finished', '2026-01-11', None),
        ]
        for rid, uid, status, created, active in reversed(fixtures):
            await database.execute(
                "INSERT INTO rooms(id, title, surface, answer, status, created_by) VALUES (?, 'title', 'surface', 'secret', ?, ?)",
                (rid, status, self.players[uid]['id']),
            )
            await database.execute('UPDATE rooms SET created_at=? WHERE id=?', (created, rid))
            if active:
                for visitor, timestamp in [(101, '2026-01-01'), (102, active)]:
                    await database.execute('INSERT INTO room_presence(room_id, player_id) VALUES (?, ?)', (rid, self.players[visitor]['id']))
                    await database.execute(
                        'UPDATE room_presence SET last_active_at=? WHERE room_id=? AND player_id=?',
                        (timestamp, rid, self.players[visitor]['id']),
                    )
        for params in [{}, {'include_finished': False}, {'include_finished': True}]:
            res = await self.mcp('my_rooms', uid=102, **params)
            self.assertEqual(res.status_code, 200, res.text)
            expected = fixtures if params.get('include_finished') else fixtures[:4]
            self.assertEqual([r['id'] for r in res.json()], [f[0] for f in expected])
            self.assertEqual([r['last_active_at'] for r in res.json()], [f[4] for f in expected])
        self.assertEqual((await self.mcp('my_rooms', uid=203)).json(), [])

    async def test_my_rooms_requires_valid_identity(self):
        before = await database.fetch_one('SELECT COUNT(*) AS n FROM players')
        for token in [None, '', 'invalid-token']:
            res = await self.mcp('my_rooms', path_token=token)
            self.assertEqual(res.status_code, 401, res.text)
        for column, value in [('deleted_at', 'deleted'), ('deletion_requested_at_epoch', 1)]:
            await database.execute(f'UPDATE toy_users SET {column}=? WHERE id=102', (value,))
            self.assertEqual((await self.mcp('my_rooms', uid=102)).status_code, 401)
            await database.execute(f'UPDATE toy_users SET {column}=NULL WHERE id=102')
        self.assertEqual(await database.fetch_one('SELECT COUNT(*) AS n FROM players'), before)

    async def test_migration_on_consistent_legacy_copy_is_idempotent(self):
        rid = await self.create()
        legacy = Path(self.tmp.name) / 'legacy.db'
        with sqlite3.connect(database.DB_PATH) as src, sqlite3.connect(legacy) as dst:
            src.backup(dst)
            dst.execute('ALTER TABLE rooms DROP COLUMN is_locked')
            dst.execute('ALTER TABLE rooms DROP COLUMN lock_owner_user_id')
        database.DB_PATH = legacy
        await database.init_db()
        await database.init_db()
        row = await database.fetch_one('SELECT * FROM rooms WHERE id=?', (rid,))
        self.assertEqual(row['is_locked'], 0)
        self.assertIsNone(row['lock_owner_user_id'])
        self.assertEqual((await database.fetch_one('PRAGMA integrity_check'))['integrity_check'], 'ok')
        self.assertEqual(await database.fetch_all('PRAGMA foreign_key_check'), [])


if __name__ == '__main__':
    unittest.main()
