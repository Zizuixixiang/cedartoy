import asyncio
import importlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import server

costs = server.api_costs
BACKEND = Path(__file__).resolve().parents[1] / 'turtle-soup/backend'
CFG = dict(id=31, name='巧克力 · NPC', model='gemini-3.1-flash-lite', purpose='npc', enabled=1,
           api_url='https://fixture.test/v1', api_key='PRIVATE_KEY', priority=0)
USAGE = dict(prompt_tokens=1000, completion_tokens=200, prompt_tokens_details={'cached_tokens': 400},
             completion_tokens_details={'reasoning_tokens': 150})


def response(usage=USAGE, status=200, content='ok'):
    data = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
    if usage is not None:
        data['usage'] = usage
    return httpx.Response(status, json=data, request=httpx.Request('POST', CFG['api_url']))


def load_judge():
    # Load the real call chain without opening the game's database.
    stub = types.ModuleType('database')
    stub.DEFAULT_SETTINGS = {}
    stub.fetch_all = AsyncMock(return_value=[])
    stub.fetch_one = AsyncMock(return_value=None)
    spec = importlib.util.spec_from_file_location('cost_test_judge', BACKEND / 'judge.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BACKEND))
    try:
        with patch.dict(sys.modules, {'database': stub, 'api_costs': costs, 'cost_test_judge': module}):
            spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(BACKEND))
    return module


class CostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'costs.db'
        costs.initialize(self.path)

    def event(self, cfg=CFG, usage=USAGE, status=200, started=None):
        event = costs.make_event(cfg, 'npc_speech', started or time.time(), response(usage, status))
        costs.write_event(event, self.path)
        return event

    def rows(self):
        with costs.connect(self.path) as db:
            return [dict(r) for r in db.execute('SELECT * FROM events ORDER BY started')]

    def test_background_writer_does_not_wait_for_database_lock(self):
        # Separate process keeps the daemon and its queue entirely inside this fixture.
        code = """
import importlib, json, sqlite3, sys, time
from pathlib import Path
c = importlib.import_module('turtle-soup.backend.api_costs')
c.DB_PATH = Path(sys.argv[1])
c.initialize()
db = sqlite3.connect(c.DB_PATH)
db.execute('BEGIN IMMEDIATE')
started = time.monotonic()
c.record({'id': 9, 'name': 'test', 'model': 'fixture'}, 'judge', time.time())
elapsed = time.monotonic() - started
assert elapsed < 0.1, elapsed
time.sleep(0.15)
assert c._pending.unfinished_tasks == 1
db.rollback()
db.close()
deadline = time.monotonic() + 5
while c._pending.unfinished_tasks and time.monotonic() < deadline:
    time.sleep(0.01)
assert c._pending.unfinished_tasks == 0
with c.connect() as db:
    assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
print(json.dumps({'enqueue_seconds_with_db_locked': elapsed}))
"""
        result = subprocess.run([sys.executable, '-c', code, str(self.path)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(json.loads(result.stdout)['enqueue_seconds_with_db_locked'], 0.1)

    def test_all_nine_defaults_and_unpriced_new_model(self):
        configs = []
        for ids, name, model in [((11, 17, 24), '小鸡农场', '[反重力][自营][次][假流] gemini-3.1-flash-lite'),
                                  ((28, 29, 30), '沉默', '[anti量]gemini-3.8-flash'),
                                  ((31, 32, 33), '巧克力', 'gemini-3.1-flash-lite')]:
            for cid in ids:
                configs.append(dict(CFG, id=cid, name=name, model=model))
        for cfg in configs:
            self.event(cfg)
        report = costs.report('today', configs, self.path)
        self.assertTrue(all(c['price'] for c in report['channels']))
        self.assertEqual(len(report['channels']), 9)
        configs[0]['model'] = 'new-model'
        self.event(configs[0])
        self.assertIsNone(next(c for c in costs.report('today', configs, self.path)['prices']
                               if c['model'] == 'new-model')['price'])

    def test_catalog_all_history_and_manual_rules_independent_of_range(self):
        now = datetime(2026, 10, 24, 12, tzinfo=costs.BEIJING)
        old = now.replace(month=8).timestamp()
        self.event(started=old)
        self.event(dict(CFG, model='old-unpriced'), status=500, started=old + 1)
        # Different purposes and retries deduplicate by config ID + exact model.
        event = costs.make_event(CFG, 'tarot', old + 2, response())
        costs.write_event(event, self.path)
        self.event(dict(CFG, id=90, name='已删除渠道', model='retired'), started=old)
        self.event(dict(CFG, id=32), started=now.timestamp())
        configs = [dict(CFG, model='current-model', enabled=0), dict(CFG, id=32),
                   dict(CFG, id=80, name='新渠道', model='never-called')]
        costs.save_price(dict(config_id=31, model='manual-only', mode='request', per_request='0'), configs, self.path)
        expected = {(31, CFG['model']), (31, 'old-unpriced'), (31, 'manual-only'),
                    (32, CFG['model']), (90, 'retired')}
        before = self.rows()
        for range_name in ('today', '7d', '30d'):
            report = costs.report(range_name, configs, self.path, now)
            self.assertEqual({(r['config_id'], r['model']) for r in report['prices']}, expected)
            self.assertEqual([(r['config_id'], r['requests']) for r in report['channels']], [(32, 1)])
            self.assertEqual(report['total']['requests'], 1)
            self.assertEqual(len(report['configs']), 3)
            self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertEqual(self.rows(), before)

    def test_model_channel_has_three_business_costs_from_recorded_events(self):
        # Same exact channel/model may have calls in all three business areas.
        # The sub-totals must be stored-price snapshots, not a reprice.
        for pool in ('judge', 'npc_speech', 'tarot'):
            costs.write_event(costs.make_event(CFG, pool, time.time(), response()), self.path)
        other = dict(CFG, model='older-unpriced-model')
        costs.write_event(costs.make_event(other, 'tarot', time.time(), response()), self.path)
        report = costs.report('today', [CFG, other], self.path)
        channels = {(r['config_id'], r['model']): r for r in report['channels']}
        first = channels[(CFG['id'], CFG['model'])]
        self.assertEqual(first['requests'], 3)
        self.assertEqual([r['purpose'] for r in first['purposes']], ['soup', 'npc', 'tarot'])
        self.assertEqual([r['requests'] for r in first['purposes']], [1, 1, 1])
        self.assertEqual([r['cost'] for r in first['purposes']], ['0.000184'] * 3)
        self.assertEqual(first['cost'], '0.000552')
        self.assertEqual(sum(r['requests'] for r in first['purposes']), first['requests'])
        self.assertEqual(sum(costs.Decimal(r['cost']) for r in first['purposes']), costs.Decimal(first['cost']))
        self.assertEqual([r['input_tokens'] for r in first['purposes']], [1000, 1000, 1000])
        self.assertEqual([r['output_tokens'] for r in first['purposes']], [200, 200, 200])
        second = channels[(other['id'], other['model'])]
        self.assertEqual([r['requests'] for r in second['purposes']], [0, 0, 1])
        self.assertEqual([r['unknown'] for r in second['purposes']], [0, 0, 1])
        self.assertEqual(second['cost'], '0')
        self.assertEqual(second['unknown'], 1)
        self.assertEqual([r['cost'] for r in second['purposes']], ['0', '0', '0'])
        self.assertEqual(report['total']['requests'], 4)

    def test_new_channel_multiple_models_and_deleted_channel_validation(self):
        cfg = dict(CFG, id=80, name='新渠道', model='model-a')
        body = dict(config_id=80, model='model-a', mode='request', per_request='0.01')
        costs.save_price(body, [cfg], self.path)
        costs.save_price(dict(body, model='model-b', per_request='0.02'), [cfg], self.path)
        costs.save_price(dict(body, per_request='0.03'), [cfg], self.path)
        report = costs.report('today', [cfg], self.path)
        self.assertEqual(report['channels'], [])
        self.assertEqual([(r['model'], r['price']['per_request']) for r in report['prices']],
                         [('model-a', '0.03'), ('model-b', '0.02')])
        # A previously saved exact rule remains editable even without calls.
        costs.save_price(dict(body, per_request='0.04'), [], self.path)
        with self.assertRaises(ValueError):
            costs.save_price(dict(body, model='invented'), [], self.path)
        self.event(cfg, status=500)
        before = self.rows()
        # Recorded attempts authorize the deleted ID, never arbitrary IDs.
        costs.save_price(dict(body, model='model-c', per_request='0.05'), [], self.path)
        with self.assertRaises(ValueError):
            costs.save_price(dict(body, config_id=999), [], self.path)
        report = costs.report('today', [], self.path)
        self.assertEqual(len(report['prices']), 3)
        self.assertTrue(all(r['deleted'] for r in report['prices']))
        self.assertTrue(all(r['name'] == '新渠道' for r in report['prices']))
        self.assertEqual(self.rows(), before)

    def test_unset_price_is_not_free_and_saving_does_not_reprice_events(self):
        cfg = dict(CFG, id=80, name='新渠道', model='unpriced')
        self.event(cfg)
        before = self.rows()
        report = costs.report('today', [cfg], self.path)
        self.assertIsNone(report['prices'][0]['price'])
        self.assertEqual(report['total']['cost'], '0')
        self.assertEqual(report['total']['unknown'], 1)
        self.assertIsNone(before[0]['cost'])
        costs.save_price(dict(config_id=80, model='unpriced', mode='request', per_request='0'), [cfg], self.path)
        self.assertEqual(self.rows(), before)
        self.event(cfg)
        self.assertEqual([(r['cost'], r['cost_state']) for r in self.rows()], [(None, 'unknown'), ('0', 'known')])
        self.assertEqual(costs.report('today', [cfg], self.path)['prices'][0]['price']['per_request'], '0')

    def test_token_cache_reasoning_and_precision(self):
        self.event()
        row = self.rows()[0]
        self.assertEqual(row['cost'], '0.000184')
        self.assertEqual(row['output_tokens'], 200)
        self.assertEqual(row['reasoning_tokens'], 150)
        self.assertEqual(row['cache_tokens'], 400)
        self.assertNotIn('PRIVATE_KEY', json.dumps(row))
        self.assertEqual(costs.usage_counts({'prompt_tokens': 5, 'completion_tokens': 4,
                         'prompt_cache_hit_tokens': 2, 'thinking_tokens': 3}), (5, 4, 2, 3))

    def test_request_and_no_usage_and_failed(self):
        cfg = dict(CFG, id=11, name='小鸡农场', model='[反重力][自营][次][假流] gemini-3.1-flash-lite')
        self.event(cfg)
        self.event(cfg, usage=None)
        self.event(cfg, status=500)
        self.event(usage=None)
        self.assertEqual([(r['cost'], r['cost_state']) for r in self.rows()],
                         [('0.001', 'known'), ('0.001', 'estimated'), (None, 'unknown'), (None, 'unknown')])

    def test_http200_invalid_answer_is_failed_but_keeps_billable_usage(self):
        cfg = dict(CFG, id=11, name='小鸡农场', model='[反重力][自营][次][假流] gemini-3.1-flash-lite')
        rejected = response(content='Sorry, I cannot process this request.')
        rejected.extensions['completion_invalid'] = True
        event = costs.make_event(cfg, 'judge', time.time(), rejected)
        costs.write_event(event, self.path)
        row = self.rows()[0]
        self.assertEqual((row['status'], row['http_status']), ('failed', 200))
        self.assertEqual((row['input_tokens'], row['output_tokens']), (1000, 200))
        self.assertEqual((row['cost'], row['cost_state']), ('0.001', 'known'))
        totals = costs.report('today', [cfg], self.path)['total']
        self.assertEqual((totals['requests'], totals['success'], totals['failed']), (1, 0, 1))

    def test_model_switch_and_provider_identity(self):
        self.event()
        self.event(dict(CFG, model='gemini-new'))
        self.event(dict(CFG, id=999))
        long_model = 'x' * 201
        self.event(dict(CFG, model=long_model))
        self.assertEqual(self.rows()[-1]['model'], long_model)
        self.assertEqual([r['cost_state'] for r in self.rows()], ['known', 'unknown', 'unknown', 'unknown'])
        report = costs.report('today', [dict(CFG, model='gemini-new')], self.path)
        self.assertIsNone(next(c for c in report['channels'] if c['current'])['price'])

    def test_snapshot_delayed_write_and_user_rule_not_overwritten(self):
        earlier = time.time() - 10
        body = dict(config_id=31, model=CFG['model'], mode='request', per_request='0.009')
        costs.save_price(body, [CFG], self.path)
        self.event(started=earlier)
        self.event()
        costs.save_price(dict(body, per_request='0.02'), [CFG], self.path)
        report = costs.report('today', [CFG], self.path)
        self.assertEqual([r['cost'] for r in self.rows()], ['0.000184', '0.009'])
        self.assertEqual(report['total']['cost'], '0.009184')
        self.assertEqual(report['channels'][0]['price']['per_request'], '0.02')
        with costs.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prices WHERE source='default'").fetchone()[0], 1)

    def test_validation(self):
        valid = dict(config_id=31, model=CFG['model'], mode='tokens', input='0', output='0.000000000001', cache='0.01')
        costs.save_price(valid, [CFG], self.path)
        for change in [{'input': '-1'}, {'input': 'NaN'}, {'input': 'Infinity'}, {'input': True},
                       {'input': '1e3'}, {'input': '1.1234567890123'}, {'model': ''}, {'model': ' bad'},
                       {'model': 'a\nb'}, {'model': 'x'*201}, {'config_id': True}, {'config_id': 999}, {'mode': 'free'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                costs.save_price(dict(valid, **change), [CFG], self.path)
        for body in (None, [], 'x'):
            with self.assertRaises(ValueError): costs.validate_rule(body)

    def test_range_beijing_and_aggregation(self):
        now = datetime(2026, 10, 24, 12, tzinfo=costs.BEIJING)
        for day in (24, 23, 18, 17, 1):
            self.event(started=now.replace(day=day).timestamp())
        for range_name, count in [('today', 1), ('7d', 3), ('30d', 5)]:
            report = costs.report(range_name, [CFG], self.path, now)
            self.assertEqual(report['total']['requests'], count)
            self.assertEqual(report['purposes'][1]['requests'], count)
        with self.assertRaises(ValueError): costs.report('all', [CFG], self.path)

    def test_malformed_usage_and_http_failure_stays_unknown(self):
        for usage in ([], {'prompt_tokens': -1}, dict(USAGE, prompt_tokens_details={'cached_tokens': 1001})):
            self.event(usage=usage)
        self.assertTrue(all(r['cost'] is None for r in self.rows()))
        self.event(status=429)
        self.assertEqual(self.rows()[-1]['status'], 'failed')

    def test_idempotence_wal_and_concurrent_writes(self):
        costs.initialize(self.path)
        event = costs.make_event(CFG, 'tarot', time.time(), response())
        costs.write_event(event, self.path)
        costs.write_event(event, self.path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.event(), range(12)))
        with costs.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 13)


class CallChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.judge = load_judge()

    async def test_retry_and_fallback_each_attempt_once(self):
        j = self.judge
        backup = dict(CFG, id=32, api_url='https://backup.test/v1', priority=1)
        last = dict(CFG, id=33, api_url='https://last.test/v1', priority=2)
        events = []
        responses = [response(status=500), response(content='bad'), response(content='good')]
        async def send(*args, **kwargs): return responses.pop(0)
        with patch.object(j, '_configs', AsyncMock(return_value=[CFG, backup, last])), \
             patch.object(httpx.AsyncClient, 'post', send), \
             patch.object(costs, 'record', side_effect=lambda *args: events.append(costs.make_event(*args))):
            result = await j._chat_validated([{'role': 'user', 'content': 'PRIVATE_PROMPT'}], lambda s: s == 'good')
        self.assertEqual(result, 'good')
        self.assertEqual([e['config_id'] for e in events], [31, 32, 33])
        self.assertEqual([e['status'] for e in events], ['failed', 'failed', 'success'])
        self.assertEqual([e['purpose'] for e in events], ['soup']*3)
        self.assertNotIn('PRIVATE', json.dumps(events))

    async def test_npc_tarot_test_purposes_and_no_usage(self):
        j = self.judge
        events = []
        with patch.object(j, '_configs', AsyncMock(return_value=[CFG])), \
             patch.object(httpx.AsyncClient, 'post', AsyncMock(return_value=response(None))), \
             patch.object(costs, 'record', side_effect=lambda *args: events.append(costs.make_event(*args))):
            await j.npc_speech_chat([{'role': 'user', 'content': 'x'}])
            await j.tarot_reading_chat([{'role': 'user', 'content': 'x'}])
            await j.test_config(CFG)
        self.assertEqual([e['purpose'] for e in events], ['npc', 'tarot', 'test'])
        self.assertTrue(all(e['counts'][:2] == (None, None) for e in events))

    async def test_hint_failure_and_fallback_remain_soup_attempts(self):
        j = self.judge
        events = []
        responses = [response(status=500), response(content='【提示】请留意故事发生时周围的环境')]
        backup = dict(CFG, id=32, api_url='https://backup.test/v1', priority=1)
        with patch.object(j, '_configs', AsyncMock(return_value=[CFG, backup])), \
             patch.object(j, '_get_judge_prompt_hint', AsyncMock(return_value='fixture')), \
             patch.object(httpx.AsyncClient, 'post', AsyncMock(side_effect=responses)), \
             patch.object(costs, 'record', side_effect=lambda *args: events.append(costs.make_event(*args))):
            result = await j.generate_hint('fixture', 'fixture', [])
        self.assertEqual(result, '请留意故事发生时周围的环境')
        self.assertEqual([e['status'] for e in events], ['failed', 'success'])
        self.assertEqual([e['purpose'] for e in events], ['soup', 'soup'])
        self.assertEqual([e['config_id'] for e in events], [31, 32])

    async def test_timeout_cancel_and_collector_failure_isolated(self):
        j = self.judge
        for failure in (httpx.ReadTimeout('PRIVATE'), asyncio.CancelledError()):
            events = []
            with patch.object(httpx.AsyncClient, 'post', AsyncMock(side_effect=failure)), \
                 patch.object(costs, 'record', side_effect=lambda *args: events.append(costs.make_event(*args))):
                with self.assertRaises(type(failure)):
                    await j._post_chat_completion(CFG, {}, 1)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['status'], 'failed')
        with patch.object(httpx.AsyncClient, 'post', AsyncMock(return_value=response())), \
             patch.object(costs, 'record', side_effect=RuntimeError('storage')):
            self.assertEqual((await j._post_chat_completion(CFG, {}, 1)).status_code, 200)

    async def test_quota_rejection_is_not_http_attempt(self):
        j = self.judge
        with patch.object(j, 'is_official_deepseek_endpoint', return_value=True), \
             patch.object(j, 'reserve_deepseek_call', side_effect=j.ProviderDailyCallsUnavailable('cap')), \
             patch.object(costs, 'record') as record:
            with self.assertRaises(j.ProviderDailyCallsUnavailable):
                await j._post_chat_completion(CFG, {}, 1)
            record.assert_not_called()


class AdminCostTests(unittest.TestCase):
    def test_http_routes_dispatch(self):
        for method, path in [('GET', '/api/admin/api-costs?range=7d'), ('POST', '/api/admin/api-costs/prices')]:
            handler = object.__new__(server.CedarToyHandler)
            handler.path = path
            handler.headers = {}
            handler.client_address = ("127.0.0.1", 12345)
            handler._is_soup_path = lambda: False
            calls = []
            handler._handle_admin_api_costs = lambda *args, **kwargs: calls.append((args, kwargs))
            getattr(handler, 'do_' + method)()
            self.assertEqual(len(calls), 1)
            if method == 'GET': self.assertEqual(calls[0][0][0], {'range': ['7d']})
            else: self.assertEqual(calls[0][1], {'save': True})

    def test_real_auth_for_read_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = Path(tmp) / 'account.db'
            with sqlite3.connect(account) as db:
                db.executescript('''CREATE TABLE toy_users(id INTEGER PRIMARY KEY,username TEXT,is_ai INTEGER,
                    is_admin INTEGER,ai_token_version INTEGER DEFAULT 0,deleted_at TEXT,last_active_at TEXT);
                    INSERT INTO toy_users(id,username,is_ai,is_admin) VALUES (1,'admin',0,1),(2,'reader',0,0);
                    CREATE TABLE judge_api_configs(id INTEGER,name TEXT,model TEXT,purpose TEXT,enabled INTEGER);
                    INSERT INTO judge_api_configs VALUES (31,'巧克力 · NPC','gemini-3.1-flash-lite','npc',1);''')
            def call(token, save=False, body=None, range_name='today'):
                handler = object.__new__(server.CedarToyHandler)
                handler.headers = {'Authorization': 'Bearer '+token} if token else {}
                handler._read_json_body = lambda: body
                sent = []
                handler._send_json = lambda payload, status=200, **kw: sent.append((status, payload, kw))
                handler._handle_admin_api_costs({'range': [range_name]}, save=save)
                return sent[0]
            with patch.object(server, 'TURTLE_DB_PATH', account), patch.object(costs, 'DB_PATH', Path(tmp)/'cost.db'):
                reader = server._create_account_jwt(dict(id=2, username='reader', is_ai=0, is_admin=0))
                admin = server._create_account_jwt(dict(id=1, username='admin', is_ai=0, is_admin=1))
                for save in (False, True):
                    self.assertEqual(call('', save)[0], 401)
                    self.assertEqual(call(reader, save)[0], 403)
                self.assertEqual(call(admin)[0], 200)
                self.assertEqual(call(admin)[2]['extra_headers']['Cache-Control'], 'no-store')
                self.assertEqual(call(admin, range_name='all')[0], 400)
                self.assertEqual(call(admin, True, dict(config_id=31, model='new-model', mode='request', per_request='0.001'))[0], 200)
                self.assertEqual(call(admin, True, dict(config_id=31, model='another-model', mode='request', per_request='0.002'))[0], 200)
                self.assertEqual({r['model'] for r in call(admin)[1]['prices']}, {'new-model', 'another-model'})
                self.assertEqual(call(admin)[1]['channels'], [])
                with sqlite3.connect(account) as db:
                    self.assertEqual(db.execute('SELECT model FROM judge_api_configs').fetchone()[0], CFG['model'])


if __name__ == '__main__':
    unittest.main()
