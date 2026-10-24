"""Offline coverage of the Chenmo-only completion adapter; no production DB/key."""
import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

BACKEND = Path(__file__).resolve().parents[1] / 'turtle-soup/backend'
MODEL = '[正向量3]deepseek-v4.1-flash'
CFG = dict(id=28, name='沉默', api_url='https://api.chenmoai.com/v1',
           api_key='fixture-only', model=MODEL, purpose='both', enabled=1, priority=0)
MESSAGES = [{'role': 'user', 'content': 'fixture'}]
USAGE = dict(prompt_tokens=1200, completion_tokens=80,
             prompt_cache_hit_tokens=900, prompt_cache_miss_tokens=300,
             completion_tokens_details={'reasoning_tokens': 20})


def load_judge():
    database = types.ModuleType('database')
    database.DEFAULT_SETTINGS = {}
    database.fetch_all = AsyncMock(return_value=[])
    database.fetch_one = AsyncMock(return_value=None)
    spec = importlib.util.spec_from_file_location('chenmo_test_judge', BACKEND / 'judge.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BACKEND))
    try:
        with patch.dict(sys.modules, {'database': database, 'chenmo_test_judge': module}):
            spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(BACKEND))
    return module


def event(value):
    return 'data: ' + json.dumps(value, ensure_ascii=False) + '\n\n'


def delta(content=None, finish=None, **extra):
    return {'choices': [{'index': 0, 'delta': {'content': content, **extra},
                         'finish_reason': finish}]}


def good_stream():
    return (': keepalive\n\n' + event(delta(None, role='assistant'))
            + event(delta(None, reasoning_content='private reasoning'))
            + event(delta('测试')) + event(delta('成功'))
            + event(delta(finish='stop'))
            + event({'choices': [], 'usage': USAGE}) + 'data: [DONE]\n\n')


class WireChunks(httpx.AsyncByteStream):
    def __init__(self, body, failure=None):
        self.body = body.encode('utf-8')
        self.failure = failure
        self.closed = False

    async def __aiter__(self):
        # Split JSON fields, delimiters and multi-byte Chinese characters.
        for offset in range(0, len(self.body), 2):
            yield self.body[offset:offset + 2]
        if self.failure:
            raise self.failure

    async def aclose(self):
        self.closed = True


class ChenmoSSETests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.judge = load_judge()
        self.events = []
        recorder = patch.object(self.judge.api_costs, 'record', side_effect=self.record)
        recorder.start()
        self.addCleanup(recorder.stop)

    def record(self, *args):
        self.events.append(self.judge.api_costs.make_event(*args))

    async def send(self, body, *, cfg=None, content_type='text/event-stream', status=200,
                   failure=None, payload=None):
        cfg = cfg or CFG
        wire = WireChunks(body, failure)
        requests = []
        async def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(status, stream=wire,
                                  headers={'Content-Type': content_type, 'Retry-After': '60'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await self.judge._send_chat_completion(
                client, cfg['api_url'], cfg['api_key'],
                payload if payload is not None else {'model': cfg['model'], 'messages': MESSAGES},
                cfg=cfg)
        self.assertTrue(wire.closed)
        return result, requests[0]

    async def test_split_utf8_crlf_usage_and_original_payload(self):
        payload = dict(model=MODEL, messages=MESSAGES, max_tokens=512)
        response, sent = await self.send(good_stream().replace('\n', '\r\n'), payload=payload)
        raw = response.json()
        self.assertEqual(raw['choices'][0]['message']['content'], '测试成功')
        self.assertEqual(raw['usage'], USAGE)
        self.assertEqual(self.events[0]['counts'], (1200, 80, 900, 20))
        self.assertEqual(self.events[0]['status'], 'success')
        self.assertEqual(sent['stream_options'], {'include_usage': True})
        self.assertIs(sent['stream'], True)
        self.assertNotIn('stream', payload)
        self.assertEqual(sent['max_tokens'], 512)
        self.assertEqual(response.headers['content-type'], 'application/json')
        self.assertEqual(response.headers['retry-after'], '60')
        self.assertNotIn('private reasoning', response.text)
        self.assertIsNone(self.judge.api_costs.default_rule(CFG))

    async def test_multiline_event_and_usage_snapshots_are_not_added(self):
        body = ('event: message\ndata: {"choices":\ndata: '
                '[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\n')
        for usage in [dict(USAGE, prompt_tokens_details={'cached_tokens': 700}),
                      {'prompt_tokens_details': {'cached_tokens': 900}}, USAGE]:
            body += event({'usage': usage, 'choices': []})
        response, _ = await self.send(body + 'data: [DONE]')
        self.assertEqual(response.json()['usage']['prompt_tokens_details']['cached_tokens'], 900)
        self.assertEqual(self.events[0]['counts'], (1200, 80, 900, 20))

    async def test_valid_json_fallback_and_missing_usage(self):
        raw = {'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]}
        response, _ = await self.send(json.dumps(raw), content_type='application/json')
        self.assertEqual(response.json(), raw)
        self.assertEqual(self.events[0]['counts'][:2], (None, None))
        response, _ = await self.send(event(delta('OK', 'stop')))
        self.assertEqual(response.json()['choices'][0]['message']['content'], 'OK')

    async def test_invalid_empty_and_error_streams_are_failed_attempts(self):
        bodies = ['', ': heartbeat\n\n', event({'usage': USAGE}) + 'data: [DONE]\n\n',
                  event(delta(' ', 'stop')), event(delta('partial')) + 'data: [DONE]\n\n',
                  event(delta('partial', 'length')), event(delta('blocked', 'content_filter')),
                  'data: {broken}\n\n', event({'choices': {}}), event({'choices': [None]}),
                  event(delta(['invalid'], 'stop')), event({'usage': []}),
                  event(delta('partial')) + event({'error': {'message': 'secret upstream detail'}}),
                  good_stream().replace('data: [DONE]', 'event: error\ndata: provider failed'),
                  event({'choices': [{'delta': None}]}),
                  event(delta('OK', 'stop')) + event(delta('extra')),
                  event(delta(None, 'stop', reasoning_content='reasoning only')),
                  good_stream() + event({'error': 'late failure'})]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises((RuntimeError, ValueError)):
                await self.send(body)
            self.assertEqual(self.events[-1]['status'], 'failed')
        self.assertEqual(len(self.events), len(bodies))

    async def test_json_errors_and_empty_choices_cannot_pass_admin(self):
        for raw in ({'error': 'bad'}, {'usage': USAGE}, {'choices': []}, [],
                    {'choices': [{'message': {'content': ''}, 'finish_reason': 'stop'}]}):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                await self.send(json.dumps(raw), content_type='application/json')
            self.assertEqual(self.events[-1]['status'], 'failed')

    async def test_http_errors_timeout_and_cancellation_stay_failed(self):
        response, _ = await self.send('upstream error', status=429)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '60')
        self.assertEqual(self.events[-1]['status'], 'failed')
        for failure in (httpx.ReadTimeout('fixture timeout'), asyncio.CancelledError()):
            with self.assertRaises(type(failure)):
                await self.send(event(delta('partial')), failure=failure)
            self.assertEqual(self.events[-1]['status'], 'failed')

    async def test_other_models_providers_and_old_model_remain_unchanged(self):
        for cfg in [dict(CFG, model='[anti量]gemini-3.8-flash'),
                    dict(CFG, model='deepseek-v4.1-flash'),
                    dict(CFG, api_url='https://other.test/v1'),
                    dict(CFG, api_url='https://api.chenmoai.com.other.test/v1')]:
            with self.subTest(cfg=cfg):
                response, sent = await self.send('unchanged', cfg=cfg)
                self.assertNotIn('stream', sent)
                self.assertNotIn('stream_options', sent)
                self.assertEqual(response.text, 'unchanged')
        for cid in (28, 29, 30, 0):
            response, sent = await self.send(good_stream(), cfg=dict(CFG, id=cid))
            self.assertTrue(sent['stream'])

    async def test_real_pool_entries_and_admin_test_share_adapter(self):
        j = self.judge
        real_client = httpx.AsyncClient
        requests = []
        async def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, content=good_stream(), headers={'content-type': 'text/event-stream'})
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        configs = [CFG, dict(CFG, id=29, purpose='npc'), dict(CFG, id=30, purpose='tarot')]
        with patch.object(j, 'fetch_all', AsyncMock(return_value=configs)), \
             patch.object(j.httpx, 'AsyncClient', side_effect=client):
            self.assertEqual(await j._chat(MESSAGES), '测试成功')
            self.assertEqual(await j._chat(MESSAGES, pool='hint'), '测试成功')
            self.assertEqual(await j.npc_decision_chat(MESSAGES), '测试成功')
            self.assertEqual(await j.npc_speech_chat(MESSAGES), '测试成功')
            self.assertEqual(await j.tarot_reading_chat(MESSAGES), {'content': '测试成功', 'model': MODEL})
            result = await j.test_config(dict(CFG, id=0))
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['reply'], '测试成功')
        self.assertTrue(all(r['stream'] for r in requests))
        self.assertEqual([e['purpose'] for e in self.events], ['soup', 'soup', 'npc', 'npc', 'tarot', 'test'])

    async def test_bad_stream_falls_back_and_admin_fails(self):
        j = self.judge
        real_client = httpx.AsyncClient
        async def handler(request):
            if request.url.host == 'api.chenmoai.com':
                return httpx.Response(200, content=event({'usage': USAGE}),
                                      headers={'content-type': 'text/event-stream'})
            return httpx.Response(200, json={'choices': [{'message': {'content': 'backup'}, 'finish_reason': 'stop'}]})
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        backup = dict(CFG, id=33, name='backup', api_url='https://backup.test/v1', priority=1)
        with patch.object(j, 'fetch_all', AsyncMock(return_value=[CFG, backup])), \
             patch.object(j.httpx, 'AsyncClient', side_effect=client):
            self.assertEqual(await j._chat(MESSAGES), 'backup')
            result = await j.test_config(CFG)
        self.assertFalse(result['success'])
        self.assertEqual(j.get_config_runtime_status(28)['consecutive_failures'], 1)
        self.assertEqual([e['status'] for e in self.events], ['failed', 'success', 'failed'])


if __name__ == '__main__':
    unittest.main()
