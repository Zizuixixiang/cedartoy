"""Filtered picker isolation, read-only summaries and reproducible scan benchmark.

All accounts/files are temporary. HTTP summaries are stubbed, never production.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
import server

GAMES = ('workkk', 'moonlit', 'ai_life', 'detroit', 'camping_plaza')
FIELDS = {
    'workkk': {'day': 7, 'balance': 80},
    'moonlit': {'saved': True},
    'ai_life': {'kind': 'current_decision', 'action_count': 9, 'turn': 4,
                'status': 'playing', 'draft_round': 2, 'score': 10},
    'detroit': {'name': '测试存档', 'chapter': 3},
    'camping_plaza': {'day': 6, 'player_name': '测试营地', 'balance': 120},
}


def summary_targets():
    targets = {name.removesuffix('_adapter'): (value, 'save_summary')
               for name, value in vars(server).items()
               if name.endswith('_adapter') and callable(getattr(value, 'save_summary', None))}
    targets.update({game: (server, '_' + game + '_save_summary')
                    for game in ('workkk', 'garden_cat', 'camping_plaza')})
    return targets


class FilteredSavePickerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='filtered-picker-')))
        self.stack.enter_context(patch.object(server, 'TURTLE_DB_PATH', self.root / 'accounts.db'))
        self.stack.enter_context(patch.object(server, 'TOY_SECRET', 'isolated-picker-test'))
        self.stack.enter_context(patch.object(server, 'SESSIONS_DB_PATH', self.root / 'sessions.db'))
        self.stack.enter_context(patch.object(server, 'VENDOR_SAVE_ROOT', self.root / 'saves'))
        for adapter in (server.moonlit_adapter, server.ai_life_adapter, server.detroit_adapter):
            self.stack.enter_context(patch.object(adapter, 'SAVE_ROOT', self.root / 'saves'))
        self.stack.enter_context(patch.dict(os.environ, {'DETROIT_MAPPING_SECRET': 'test-mapping-secret'}))
        with server._db_connect() as db:
            db.executescript('''
                CREATE TABLE toy_users(id INTEGER PRIMARY KEY, username TEXT, is_ai INTEGER,
                    deleted_at TEXT, last_active_at TEXT);
                CREATE TABLE user_bindings(human_user_id INTEGER, ai_user_id INTEGER, created_at TEXT);
                INSERT INTO toy_users(id,username,is_ai) VALUES
                    (1,'人类',0),(2,'其他人类',0),(101,'小机甲',1),(202,'小机乙',1),(999,'未绑定',1),(303,'已删除',1);
                UPDATE toy_users SET deleted_at='deleted' WHERE id=303;
                INSERT INTO user_bindings VALUES (1,101,'2'),(1,202,'1'),(1,303,'0'),(2,999,'1');
            ''')
        self.token = self.token_for(1)
        self.migration = self.stack.enter_context(patch.object(
            server, '_auto_migrate_legacy_account_saves', side_effect=AssertionError('migration')))
        self.stack.enter_context(patch.object(server.nowhere_adapter.POOL, 'acquire', side_effect=AssertionError('worker')))

    def token_for(self, uid, **extra):
        return server._jwt_encode({'user_id': uid, 'is_ai': uid >= 100,
                                   'exp': int(time.time()) + 600, **extra})

    def call(self, query='', token=None):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = '/api/auth/saves' + query
        handler.headers = {'Authorization': 'Bearer ' + (self.token if token is None else token)}
        result = {}
        handler._send_json = lambda body, status=200, **kw: result.update(body=body, status=status, **kw)
        handler._handle_api_auth_saves()
        return result

    def guards(self, stack, game, side_effect):
        mocks = {}
        for name, (owner, attr) in summary_targets().items():
            effect = side_effect if name == game else AssertionError('other summary: ' + name)
            mocks[name] = stack.enter_context(patch.object(owner, attr, side_effect=effect))
        stack.enter_context(patch.object(server, '_account_saves_for_user', side_effect=AssertionError('full scan')))
        return mocks

    def test_all_five_only_target_bound_five_slots_and_all_fields(self):
        players = [str(uid) if slot == 1 else f'{uid}:{slot}' for uid in (101, 202) for slot in range(1, 6)]
        for game in GAMES:
            with self.subTest(game=game), ExitStack() as stack:
                mocks = self.guards(stack, game, lambda player, **kw: dict(FIELDS[game], slot=99))
                result = self.call('?game=' + game + '&player_id=999&ai_user_id=999&slot=99')
                self.assertEqual(result['status'], 200)
                self.assertEqual(result['extra_headers']['Cache-Control'], 'no-cache, no-store')
                self.assertCountEqual([c.args[0] for c in mocks[game].call_args_list], players)
                self.assertTrue(all(m.call_count == 0 for name, m in mocks.items() if name != game))
                self.assertEqual([m['user']['id'] for m in result['body']['machines']], [101, 202])
                for machine in result['body']['machines']:
                    self.assertEqual(machine['saves'], {game: {'slots': [dict(FIELDS[game], slot=s) for s in range(1, 6)]}})
                self.migration.assert_not_called()

    def test_no_single_and_multiple_saves_and_unbound_human(self):
        for game in GAMES:
            for existing in (set(), {'101:5'}, {'101', '101:3', '202:2'}):
                with self.subTest(game=game, existing=existing), ExitStack() as stack:
                    mocks = self.guards(stack, game, lambda player, **kw: FIELDS[game] if player in existing else None)
                    result = self.call('?game=' + game)
                    self.assertEqual(result['status'], 200)
                    found = {server._account_slot_player_id(m['user']['id'], s['slot'])
                             for m in result['body']['machines'] for s in m['saves'].get(game, {}).get('slots', [])}
                    self.assertEqual(found, existing)
                    mocks[game].reset_mock()
                    with server._db_connect() as db:
                        db.execute("INSERT OR IGNORE INTO toy_users(id, username, is_ai) VALUES(3,'无绑定',0)")
                        db.commit()
                    self.assertEqual(self.call('?game=' + game, self.token_for(3))['body'], {'machines': []})
                    mocks[game].assert_not_called()

    def test_real_auth_expired_missing_ai_and_unbound_cannot_read_others(self):
        for game in GAMES:
            with self.subTest(game=game), ExitStack() as stack:
                mocks = self.guards(stack, game, lambda player, **kw: FIELDS[game])
                for token in ('', 'expired', self.token_for(1, exp=1), self.token_for(101), self.token_for(303)):
                    self.assertEqual(self.call('?game=' + game, token)['status'], 401)
                mocks[game].assert_not_called()
                result = self.call('?game=' + game + '&ai_user_id=101', self.token_for(2))
                self.assertEqual([m['user']['id'] for m in result['body']['machines']], [999])
                self.assertEqual({c.args[0].split(':')[0] for c in mocks[game].call_args_list}, {'999'})

    def test_strict_allowlist_and_legacy_route_unchanged(self):
        with patch.object(server, '_account_web_saves', return_value={'legacy': True}) as old:
            for query in ('?game=', '?game=eco', '?game=nowhere', '?game=__import__',
                          '?game=../workkk', '?game=workkk&game=moonlit'):
                self.assertEqual(self.call(query)['status'], 400)
            old.assert_not_called()
            self.assertEqual(self.call()['body'], {'legacy': True})
            old.assert_called_once_with(self.token)

    def put(self, game, player, filename, body):
        path = self.root / 'saves' / game / player / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding='utf-8')

    def test_real_local_summaries_read_only_no_engine_and_no_new_directories(self):
        ai = server.ai_life_adapter
        self.put('workkk', '101:5', 'game_state.json', {'day_count': 7, 'salary_balance': 80})
        self.put('moonlit', '101:5', server.moonlit_adapter.SAVE_NAME, {})
        self.put('ai_life', '101:5', ai.SAVE_NAME, {'format': ai.SAVE_FORMAT, 'version': ai.SAVE_VERSION,
                 'upstream_commit': ai.UPSTREAM_COMMIT, 'summary': FIELDS['ai_life']})
        server.detroit_adapter._write_state_unlocked('101:5', {'save_id': 'fixture', 'summary': FIELDS['detroit']})
        def snapshot():
            return {str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() else None)
                    for p in (self.root / 'saves').rglob('*')}
        before = snapshot()
        for game in GAMES[:-1]:
            owner, attr = summary_targets()[game]
            real = getattr(owner, attr)
            with self.subTest(game=game), ExitStack() as stack:
                mocks = self.guards(stack, game, real)
                for adapter in (server.ai_life_adapter, server.moonlit_adapter, server.detroit_adapter):
                    stack.enter_context(patch.object(adapter, 'play', side_effect=AssertionError('engine')))
                result = self.call('?game=' + game)
                self.assertEqual(result['status'], 200)
                slots = result['body']['machines'][0]['saves'][game]['slots']
                self.assertEqual(len(slots), 1)
                self.assertEqual(slots[0]['slot'], 5)
                for key, value in FIELDS[game].items():
                    self.assertEqual(slots[0][key], value)
                self.assertEqual(mocks[game].call_count, 10)
                self.assertEqual(snapshot(), before)

    def test_camping_http_timeout_partial_failure_and_all_failure_not_empty(self):
        db = self.root / 'camping.db'
        db.touch()
        with patch.object(server, 'CAMPING_PLAZA_DB_PATH', db), ExitStack() as stack:
            real = server._camping_plaza_save_summary
            self.guards(stack, 'camping_plaza', real)
            other_machine_read = threading.Event()
            def response(url, *, json, timeout):
                self.assertEqual(url, server.CAMPING_PLAZA_BASE + '/internal/saves/summary')
                self.assertEqual(timeout, 2)
                if json['player_id'] == '101':
                    self.assertTrue(other_machine_read.wait(1), 'slow slot blocked another machine')
                    raise httpx.ReadTimeout('fixture timeout')
                if json['player_id'] == '202:5':
                    other_machine_read.set()
                    return httpx.Response(200, json={'summary': FIELDS['camping_plaza']})
                return httpx.Response(404, json={})
            with patch.object(server.httpx, 'post', side_effect=response) as post:
                result = self.call('?game=camping_plaza')
                self.assertEqual(result['status'], 200)
                self.assertEqual(post.call_count, 10)
                self.assertEqual(result['body']['errors'][0]['ai_user_id'], 101)
                self.assertEqual(result['body']['machines'][0]['saves'], {})
                self.assertEqual(result['body']['machines'][1]['saves']['camping_plaza']['slots'],
                                 [dict(FIELDS['camping_plaza'], slot=5)])
            with patch.object(server.httpx, 'post', side_effect=httpx.ConnectError('offline')):
                result = self.call('?game=camping_plaza')
                self.assertNotEqual(result['status'], 200)
                self.assertIn('error', result['body'])

    def test_benchmark_same_fixture_legacy_and_filtered(self):
        """1ms artificial IO per summary: compare traversal, not live latency."""
        with ExitStack() as stack:
            stack.enter_context(patch.object(server, '_public_user', side_effect=lambda u: {'id': u['id']}))
            stack.enter_context(patch.object(server, '_turtle_soup_stats', return_value={}))
            mocks = {}
            for game, (owner, attr) in summary_targets().items():
                def read(player, game=game, **kw):
                    time.sleep(.001)
                    return FIELDS.get(game, {'saved': True})
                mocks[game] = stack.enter_context(patch.object(owner, attr, side_effect=read))
            report = []
            for game in ('legacy', *GAMES):
                for mock in mocks.values(): mock.reset_mock()
                start = time.perf_counter()
                result = self.call('' if game == 'legacy' else '?game=' + game)
                elapsed = (time.perf_counter() - start) * 1000
                self.assertEqual(result['status'], 200)
                counts = {name: m.call_count for name, m in mocks.items() if m.call_count}
                self.assertEqual(sum(counts.values()), 300 if game == 'legacy' else 10)
                if game != 'legacy': self.assertEqual(counts, {game: 10})
                report.append({'game': game, 'calls': sum(counts.values()), 'elapsed_ms': round(elapsed, 2)})
            print('\nFiltered picker benchmark (2 machines, 5 slots, 1ms/summary): ' + json.dumps(report))


if __name__ == '__main__':
    unittest.main()
