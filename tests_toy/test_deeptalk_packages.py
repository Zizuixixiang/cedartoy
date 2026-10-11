"""Release acceptance: real packages through authenticated HTTP and root MCP."""
import hashlib
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from deeptalk import catalog
from tests_toy import test_deeptalk as fixtures
from tests_toy.test_deeptalk import ROOT, HUMAN, AI, OTHER_HUMAN, OTHER_AI
import server

EXPECTED = {
    'relations_communication': 13, 'couple_50': 50, 'relationship_scenarios': 14,
    'romance_values_100': 100, 'work_values_20': 20, 'favorites_100': 100,
    'getting_to_know_100': 100, 'life_reflection_35': 35, 'real_life_dilemmas_30': 30,
    'learning_goals_problem_solving': 28,
}


class RealPackageTests(unittest.TestCase):
    connect = fixtures.DeeptalkTests.connect
    http = fixtures.DeeptalkTests.http
    mcp = fixtures.DeeptalkTests.mcp

    def setUp(self):
        fixtures.DeeptalkTests.setUp(self)
        real = patch.object(catalog, 'DATA_DIR', ROOT / 'deeptalk/data')
        real.start()
        self.addCleanup(real.stop)

    def request(self, user, action, **params):
        if not user['is_ai']:
            status, data, headers = self.http(user, action, post=action in {'create','answer','submit'}, **params)
            self.assertEqual(status, 200, data)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            return data
        response = self.mcp(user, action, game=getattr(self, 'alias', 'deeptalk'),
                            path=bool(params.pop('_path', False)), **params)['result']
        self.assertFalse(response['isError'], response)
        return json.loads(response['content'][0]['text'])

    def test_original_nine_packages_keep_release_bytes(self):
        expected_hashes = {
            'relations_communication': 'f5c2335732144528fe1346a3d1601eb269b90a31b13086179dc6eae6782a357e',
            'couple_50': '21836375dd10f08dc565d11594a9f758669ac2ffa1a88895108ad9f340396c62',
            'relationship_scenarios': '44fcfe03ee0be1ce3fe13a622f426b3d7beafb667724ab4aff58fc9a32aa8739',
            'romance_values_100': '94ed8b2fbdcfe55dc7a19d84077c310d9b8865d719aba562b3deb67cb3bf4def',
            'work_values_20': '6bf3d0bf8f560aff5f49b509296aaa39157ddf3f95452a030ef4b96b775e192b',
            'favorites_100': '1e47c94a40f06a6d039e1416729e8a3a3b4940c5c223f004495a9c32d3b98443',
            'getting_to_know_100': 'dfea6cd24ea154dab2ebf5c1e1554f94aba4c139a176cbd71ccda67a92db6320',
            'life_reflection_35': '41ba568b04b8cb36bde874842797234dbf509dacf0c2839aef614c5ff10232bd',
            'real_life_dilemmas_30': '018cb8ffe3c15d78466a20807e1cfbb25276df87fa8d3c82397c435a7dcdd061',
        }
        self.assertEqual(len(expected_hashes), 9)
        for tid, digest in expected_hashes.items():
            with self.subTest(topic=tid):
                self.assertEqual(hashlib.sha256((catalog.DATA_DIR / f'{tid}.json').read_bytes()).hexdigest(), digest)

    def test_learning_source_groups_and_unscored_boundary(self):
        topic = catalog.load_topic('learning_goals_problem_solving')
        self.assertEqual(topic['license'], 'CC BY 4.0')
        self.assertEqual(topic['author'], 'Alli M. Tucker-Stanojković (North Orange Continuing Education)')
        self.assertEqual([len(s['question_ids']) for s in topic['sources']], [9, 6, 9, 4])
        self.assertEqual([q['id'] for q in topic['questions']],
                         [qid for s in topic['sources'] for qid in s['question_ids']])
        for q in topic['questions']:
            self.assertEqual(set(q), {'id', 'text', 'source_ref'})
            self.assertIn(topic['source_url'], q['source_ref'])
        self.assertTrue(self.mcp(AI, 'shared_result', game='dt_learning_goals', human_id=HUMAN['id'])['result']['isError'])

    def test_catalog_exactly_ten_and_compact_discovery(self):
        self.assertEqual(set(p.stem for p in catalog.DATA_DIR.glob('*.json')), set(EXPECTED))
        topics = self.request(AI, 'topics')['topics']
        self.assertEqual({t['topic_id']: t['total'] for t in topics}, EXPECTED)
        self.assertEqual(sum(t['total'] for t in topics), 490)
        self.assertTrue(all(t['available'] for t in topics))
        self.assertNotIn('OC', catalog.GUIDE)
        for tid in EXPECTED:
            for path in (False, True):
                one = self.request(AI, 'questions', topic_id=tid, _path=path)
                self.assertEqual(len(one['questions']), 1)
                self.assertEqual(one['questions'][0]['text'], catalog.load_topic(tid)['questions'][0]['text'])

    def test_independent_guides_keep_compact_complete_shared_operations(self):
        # Development-only measurement; production must not import tiktoken.
        import tiktoken
        encoding = tiktoken.get_encoding('cl100k_base')
        shared_operations = None
        for game in catalog.ALIASES:
            with self.subTest(game=game):
                guide = json.loads(server._tool_get_guide({'game': game}))['guide']
                self.assertLessEqual(len(encoding.encode(guide)), 550)
                self.assertNotIn('get_guide', guide)
                self.assertEqual(guide.count('整套题库文本：'), 1)
                self.assertIn(f'play(game="{game}",action=...,params={{...}})', guide)
                for action, fields in {
                    'questions': ('number', 'limit'), 'status': ('round_id', 'revision'),
                    'question': ('round_id', 'number'),
                    'answer': ('round_id', 'question_id', 'revision', 'request_id', 'text', 'skip=true'),
                    'submit': ('round_id', 'revision', 'confirm=true'),
                    'result': ('round_id', 'number', 'limit'),
                }.items():
                    match = re.search(r'\b' + action + r':([^；。\n]+)', guide, re.ASCII)
                    self.assertIsNotNone(match, action)
                    instructions = match.group(1)
                    for field in fields:
                        self.assertIn(field, instructions, action)
                for required in ('/deeptalk', 'history', 'source', '从1起', '8–100位[A-Za-z0-9_-]',
                                 '重试复用', '双方提交才解锁', '当前绑定小机共用草稿/revision', '解绑失权', '旧轮次限原双方', '冲突先重读合并', '不计分，可跳题', '无标准答案', 'result只对照双方回答', '无评分或测评报告'):
                    self.assertIn(required, guide)
                operations = guide[guide.index('play(game='):].replace(game, '<game>')
                if shared_operations is None:
                    shared_operations = operations
                self.assertEqual(operations, shared_operations)

    def test_ten_full_rounds_resume_seal_authorize_and_repeat(self):
        for n, (tid, total) in enumerate(EXPECTED.items()):
            with self.subTest(topic=tid):
                self.alias = next(game for game, topic in catalog.ALIASES.items() if topic == tid)
                created = self.request(HUMAN, 'create', topic_id=tid, request_id=f'real_create_{tid}')
                rid = created['round_id']
                self.assertEqual(created['total'], total)
                questions = catalog.load_topic(tid)['questions']
                for user in (HUMAN, AI):
                    for i, q in enumerate(questions):
                        params = dict(round_id=rid, question_id=q['id'], revision=i,
                                      request_id=f'real_answer_{user["id"]}_{i}')
                        params.update({'skip': True} if i == total-1 else {'text': f'PRIVATE_{user["id"]}_{tid}_{i}'})
                        self.request(user, 'answer', **params)
                    resumed = self.request(user, 'question', round_id=rid)
                    self.assertEqual(resumed['revision'], total)
                    self.assertEqual(resumed['questions'][0]['text'], questions[0]['text'])
                    self.assertEqual(resumed['questions'][0]['own']['text'], f'PRIVATE_{user["id"]}_{tid}_0')
                first, second = (HUMAN, AI) if n % 2 == 0 else (AI, HUMAN)
                for phase in (0, 1):
                    if phase:
                        self.request(first, 'submit', round_id=rid, revision=total, confirm=True)
                    for user, peer in ((HUMAN, AI), (AI, HUMAN)):
                        for action in ('status','question','source','history'):
                            data = self.request(user, action, round_id=rid)
                            self.assertNotIn(f'PRIVATE_{peer["id"]}', json.dumps(data))
                        if user == HUMAN:
                            status, view, _ = self.http(user, 'view', round_id=rid)
                            self.assertEqual(status, 200)
                            self.assertNotIn('partner', json.dumps(view))
                            self.assertEqual(self.http(user, 'result', round_id=rid)[0], 409)
                        else:
                            self.assertTrue(self.mcp(user, 'result', game=self.alias, round_id=rid)['result']['isError'])
                self.request(second, 'submit', round_id=rid, revision=total, confirm=True)
                view = self.request(HUMAN, 'view', round_id=rid)
                self.assertEqual(len(view['questions']), total)
                self.assertTrue(view['unlocked'])
                self.assertTrue(view['questions'][-1]['partner']['skipped'])
                for start in range(1, total+1, 20):
                    result = self.request(AI, 'result', round_id=rid, number=start, limit=20)
                    self.assertEqual(result['questions'][0]['partner'], view['questions'][start-1]['own'])
                for action in ('view','source','result'):
                    self.assertEqual(self.http(OTHER_HUMAN, action, round_id=rid)[0], 404)
                for action in ('status','question','source','result'):
                    self.assertTrue(self.mcp(OTHER_AI, action, game=self.alias, round_id=rid)['result']['isError'])
                for user in (OTHER_HUMAN, OTHER_AI):
                    write = dict(round_id=rid, question_id=questions[0]['id'], text='unauthorized', revision=0, request_id='cross_account')
                    if user == OTHER_HUMAN:
                        self.assertEqual(self.http(user, 'answer', post=True, **write)[0], 404)
                        self.assertEqual(self.http(user, 'submit', post=True, round_id=rid, revision=total, confirm=True)[0], 404)
                    else:
                        self.assertTrue(self.mcp(user, 'answer', game=self.alias, **write)['result']['isError'])
                        self.assertTrue(self.mcp(user, 'submit', game=self.alias, round_id=rid, revision=total, confirm=True)['result']['isError'])
                self.assertEqual(self.http(HUMAN, 'answer', post=True, round_id=rid, question_id=questions[0]['id'], text='late', revision=total, request_id='late_answer')[0], 409)
                repeat = self.request(HUMAN, 'create', topic_id=tid, request_id=f'repeat_create_{tid}')
                self.assertNotEqual(repeat['round_id'], rid)
                self.assertEqual(self.request(HUMAN, 'view', round_id=rid), view)
                history = self.request(HUMAN, 'history', limit=100)
                summary = next(r for r in history['rounds'] if r['round_id'] == rid)
                self.assertEqual(summary['url'], f'/deeptalk?round_id={rid}')
                self.assertEqual(summary['status'], 'complete')
                self.assertNotIn('PRIVATE_', json.dumps(history))
                self.assertNotIn('questions', json.dumps(history))
                for outsider in (OTHER_HUMAN, OTHER_AI):
                    self.assertNotIn(rid, json.dumps(self.request(outsider, 'history')))
                # Deeptalk never writes scored results or public answer statistics.
                with sqlite3.connect(self.db) as conn:
                    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertNotIn('test_results', tables)

    def test_ten_aliases_discover_guide_read_compact_and_keep_auth(self):
        listing = server._tool_list_games().split('深聊问答:\n', 1)[1].split('\n测试:', 1)[0]
        self.assertEqual(len(listing.splitlines()), 10)
        self.assertNotRegex(listing, r'https?://|license|作者|OC|source_ref|deeptalk·')
        self.assertEqual(set(catalog.ALIASES.values()), set(EXPECTED))
        for game, tid in catalog.ALIASES.items():
            with self.subTest(game=game):
                topic = catalog.load_topic(tid)
                self.assertEqual(listing.count(game + '·'), 1)
                guide_response = server._handle_root_mcp({'jsonrpc':'2.0','id':1,'method':'tools/call',
                    'params':{'name':'get_guide','arguments':{'game':game}}})['result']
                self.assertFalse(guide_response['isError'])
                guide = json.loads(guide_response['content'][0]['text'])['guide']
                for field in ('title','author','license','source_url','license_url'):
                    self.assertIn(topic[field], guide)
                self.assertIn(str(EXPECTED[tid]) + '题', guide)
                self.assertNotIn('OC', guide)
                for other in catalog.ALIASES:
                    if other != game: self.assertNotIn(other, guide)
                for path in (False, True):
                    response = self.mcp(AI, 'questions', game=game, path=path, number=1)['result']
                    self.assertFalse(response['isError'], response)
                    data = json.loads(response['content'][0]['text'])
                    self.assertEqual(data['topic_id'], tid)
                    self.assertEqual(data['questions'], [dict(number=1, id=topic['questions'][0]['id'], text=topic['questions'][0]['text'])])
                for selected_game in (game, 'deeptalk'):
                    response = self.mcp(AI, 'questions', game=selected_game, topic_id=tid, number=1, limit=100)['result']
                    self.assertFalse(response['isError'], response)
                    data = json.loads(response['content'][0]['text'])
                    self.assertEqual(len(data['questions']), EXPECTED[tid])
                    for i, q in enumerate(data['questions'], 1):
                        self.assertEqual(set(q), {'number','id','text'})
                        self.assertEqual(q['number'], i)
                        self.assertEqual(q['text'], topic['questions'][i-1]['text'])
                source = self.mcp(AI, 'source', game=game)['result']
                self.assertEqual(json.loads(source['content'][0]['text']), {k:v for k,v in topic.items() if k != 'questions'})
                self.assertTrue(all('source_ref' in q for q in topic['questions']))
                other_tid = next(t for t in EXPECTED if t != tid)
                self.assertTrue(self.mcp(AI, 'questions', game=game, topic_id=other_tid)['result']['isError'])
                self.assertTrue(self.mcp(AI, 'questions', game=game, number=0)['result']['isError'])
                for user in (None, HUMAN):
                    self.assertTrue(self.mcp(user, 'questions', game=game)['result']['isError'])
                self.assertTrue(self.mcp(OTHER_AI, 'status', game=game, round_id=self.rid)['result']['isError'])
