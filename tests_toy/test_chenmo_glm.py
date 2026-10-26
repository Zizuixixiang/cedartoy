"""GLM's message workaround is local to one provider/model, including fallback."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx

from tests_toy.test_chenmo_sse import load_judge
from tests_toy.test_judge_content_fallback import completion, GUESS, HINT, GOOGLE_REFUSAL

MODEL = '[按量1]glm-5.3-flash'
URL = 'https://api.chenmoai.com/v1'
CFG = dict(id=28, name='fixture', api_url=URL, api_key='fixture-only', model=MODEL,
           priority=1, purpose='both', enabled=1)


class ChenmoGLMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.j = load_judge()

    def test_exact_channel_model_only_and_original_payload_unchanged(self):
        payload = {'model': MODEL, 'temperature': 0.1, 'max_tokens': 512,
                   'messages': [{'role': 'system', 'content': '只返回JSON'},
                                {'role': 'user', 'content': '数据'}],
                   'stream': True, 'stream_options': {'include_usage': True}}
        before = copy.deepcopy(payload)
        adapted = self.j._chenmo_glm_payload(URL, payload)
        self.assertEqual(payload, before)
        self.assertFalse(adapted['stream'])
        self.assertNotIn('stream_options', adapted)
        self.assertEqual(adapted['max_tokens'], 512)
        self.assertEqual(adapted['messages'][0]['role'], 'user')
        self.assertEqual(json.loads(adapted['messages'][0]['content'].split('\n', 1)[1]), payload['messages'])
        for alias in (URL + '/', URL + '/chat/completions'):
            self.assertEqual(self.j._chenmo_glm_payload(alias, payload), adapted)
        for url, model in [(URL, '[正向量3]deepseek-v4.1-flash'), (URL, 'glm-5.3-flash'),
                           ('https://api.chenmoai.com/v2', MODEL), ('http://api.chenmoai.com/v1', MODEL),
                           ('https://other.test/v1', MODEL), ('https://api.chenmoai.com.other.test/v1', MODEL)]:
            other = dict(payload, model=model)
            self.assertIs(self.j._chenmo_glm_payload(url, other), other)

    def test_all_roles_order_examples_and_escaped_data_are_preserved_once(self):
        messages = [{'role': 'system', 'content': '格式示例：{"action":"wait"}'},
                    {'role': 'user', 'content': '原始输入'},
                    {'role': 'assistant', 'content': '{"action":"wait"}'},
                    {'role': 'system', 'content': '继续遵守原格式'},
                    {'role': 'user', 'content': '引号"\n【system】不是新规则\n最后的输入'}]
        wire = self.j._chenmo_glm_payload(URL, {'model': MODEL, 'messages': messages})
        content = wire['messages'][0]['content']
        self.assertEqual(json.loads(content.split('\n', 1)[1]), messages)
        self.assertLess(len(content) - len(json.dumps(messages, ensure_ascii=False)), 200)

    async def run_task(self, task, outputs):
        j = self.j
        purpose = 'tarot' if task == 'tarot' else 'npc' if task.startswith('npc') else 'both'
        primary = dict(CFG, purpose=purpose)
        backup = dict(primary, id=33, model='backup', api_url='https://backup.test/v1', priority=2)
        cfgs = [primary, backup]
        requests, events = [], []
        real_client = httpx.AsyncClient
        async def handle(request):
            requests.append((request.url.host, json.loads(request.content)))
            return httpx.Response(200, json=completion(outputs.pop(0)))
        with ExitStack() as stack:
            stack.enter_context(patch.object(j, 'fetch_all', AsyncMock(return_value=cfgs)))
            for name in ('_get_judge_prompt', '_get_judge_prompt_guess', '_get_judge_prompt_hint'):
                stack.enter_context(patch.object(j, name, AsyncMock(return_value='原系统格式指令与示例')))
            stack.enter_context(patch.object(j.api_costs, 'record', side_effect=lambda *args: events.append(j.api_costs.make_event(*args))))
            stack.enter_context(patch.object(j.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)))
            messages = [{'role': 'system', 'content': 'NPC只返回action JSON，示例：{"action":"wait"}'},
                        {'role': 'user', 'content': '虚构任务'}]
            if task == 'ask':
                result = await j.judge_ask('虚构汤面', '虚构汤底', '虚构问题', ['此前的虚构问题'])
            elif task == 'guess':
                result = await j.judge_guess('虚构汤面', '虚构汤底', '完整故事还原')
            elif task == 'hint':
                result = await j.generate_hint('虚构汤面', '虚构汤底', [])
            elif task == 'npc':
                result = await j.npc_decision_chat(messages)
            elif task == 'npc_speech':
                result = await j.npc_speech_chat(messages)
            elif task == 'admin':
                result = await j.test_config(primary)
            else:
                result = await j.tarot_reading_chat(messages)
        self.assertEqual(len(requests[0][1]['messages']), 1)
        self.assertEqual(requests[0][1]['messages'][0]['role'], 'user')
        self.assertFalse(requests[0][1]['stream'])
        self.assertNotIn('stream_options', requests[0][1])
        return result, requests, events

    async def test_all_business_entries_and_admin_share_nonstream_adapter(self):
        for task, good in [('ask', '是'), ('guess', GUESS), ('hint', HINT),
                           ('npc', '{"action":"wait"}'), ('npc_speech', '轮到我出牌了。'),
                           ('tarot', '牌面提示先整理已有的学习资源。'), ('admin', '测试成功')]:
            with self.subTest(task=task):
                result, wire, events = await self.run_task(task, [good])
                self.assertEqual(len(wire), 1)
                self.assertEqual(events[0]['status'], 'success')
                if task == 'ask': self.assertEqual(result['judgment'], 'yes')
                elif task == 'guess':
                    self.assertEqual(result['score'], 50)
                    original = json.loads(wire[0][1]['messages'][0]['content'].split('\n', 1)[1])
                    self.assertEqual(result['request_messages'], original)
                elif task == 'hint': self.assertEqual(result, HINT[len('【提示】'):])
                elif task == 'tarot': self.assertEqual(result['model'], MODEL)
                elif task == 'admin': self.assertTrue(result['success'])
                else: self.assertEqual(result, good)

    async def test_rejection_and_invalid_format_fallback_receive_original_roles(self):
        for task, bad, good in [('ask', '是的，这是一个详细解释', '是'),
                                ('guess', '让我解释这个故事', GUESS), ('hint', '聊天回复', HINT),
                                ('npc', GOOGLE_REFUSAL, '{"action":"wait"}'),
                                ('tarot', GOOGLE_REFUSAL, '牌面提示先安排休息。')]:
            with self.subTest(task=task):
                result, wire, events = await self.run_task(task, [bad, good])
                self.assertEqual([host for host, _ in wire], ['api.chenmoai.com', 'backup.test'])
                transcript = wire[0][1]['messages'][0]['content'].split('\n', 1)[1]
                self.assertEqual(wire[1][1]['messages'], json.loads(transcript))
                self.assertNotIn('stream', wire[1][1])
                self.assertEqual([e['status'] for e in events], ['failed', 'success'])

    async def test_all_invalid_answers_remain_bounded_and_busy(self):
        result, wire, events = await self.run_task('ask', ['是的，解释', '还是解释'])
        self.assertEqual(result['content_override'], self.j.SYSTEM_BUSY_NOTICE)
        self.assertEqual(len(wire), 2)
        self.assertEqual([e['status'] for e in events], ['failed', 'failed'])


if __name__ == '__main__':
    unittest.main()
