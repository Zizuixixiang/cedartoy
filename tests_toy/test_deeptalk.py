"""Synthetic fixtures only; production question packages are maintained separately."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from deeptalk import catalog
from deeptalk.catalog import DeeptalkError
from deeptalk.store import Store
from cedar_backend import deeptalk as service
from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import server
import game_activity

ROOT = Path(__file__).resolve().parents[1]
HUMAN = dict(id=101, username='人类甲', is_ai=False)
AI = dict(id=201, username='小机甲', is_ai=True)
OTHER_HUMAN = dict(id=102, username='人类乙', is_ai=False)
OTHER_AI = dict(id=202, username='小机乙', is_ai=True)


def write_fixtures(directory):
    directory.mkdir(exist_ok=True)
    for tid, (title, count, description) in catalog.TOPICS.items():
        topic = dict(topic_id=tid, title=title, description=description, author='TEST FIXTURE',
                     source_url='https://example.invalid/test', license='TEST', license_url='',
                     adaptation_note='仅供自动化测试，不是实际题库', sources=[{'name': 'test', 'source_url': 'https://example.invalid/test'}],
                     questions=[dict(id=f'q{i}', text=f'测试占位题 {i}', source_ref=f'test:{i}') for i in range(1, count+1)])
        (directory / f'{tid}.json').write_text(json.dumps(topic, ensure_ascii=False))


class DeeptalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'questions'
        write_fixtures(self.data)
        patcher = patch.object(catalog, 'DATA_DIR', self.data)
        patcher.start(); self.addCleanup(patcher.stop)
        self.db = self.root / 'sessions.db'
        self.accounts = self.root / 'accounts.db'
        with self.connect() as conn:
            conn.executescript('''CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT, is_ai INTEGER,
                deleted_at TEXT, last_active_at TEXT, ai_token_version INTEGER DEFAULT 0);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);''')
            conn.executemany('INSERT INTO toy_users(id,username,is_ai) VALUES (:id,:username,:is_ai)', [HUMAN,AI,OTHER_HUMAN,OTHER_AI])
            conn.executemany('INSERT INTO user_bindings VALUES (?,?)', [(101,201),(102,202)])
        self.store = Store(self.db)
        self.rid = self.store.create(HUMAN, AI, 'relations_communication', 'create_0001')['round_id']
        self.tokens = {u['id']: server._create_account_jwt(u) for u in [HUMAN,AI,OTHER_HUMAN,OTHER_AI]}
        for name, value in [('TURTLE_DB_PATH', self.accounts), ('SESSIONS_DB_PATH', self.db),
                            ('_play_announcements', lambda *_: ''), ('_mcp_forced_announcement', lambda *_: ''),
                            ('_duel_unread_request_reminder', lambda *_: ''), ('_anti_addiction_context', lambda *_: None)]:
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.accounts); conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def call(self, user, action, **params):
        return service.execute(user, action, params, db_path=self.db, db_connect=self.connect, web=not user['is_ai'])

    def answer(self, user, q=1, **extra):
        revision = self.call(user, 'status', round_id=self.rid)['revision']
        return self.call(user, 'answer', round_id=self.rid, question_id=f'q{q}', revision=revision,
                         request_id=f'{user["id"]}_{revision:08}', **({'text': f'SECRET_{user["id"]}_{q}'} | extra))

    def fill(self, user):
        for q in range(1,14):
            self.answer(user,q)

    def submit(self, user):
        return self.call(user,'submit',round_id=self.rid,revision=self.call(user,'status',round_id=self.rid)['revision'],confirm=True)

    def mcp(self, user, action, *, path=False, game='deeptalk', **params):
        token = self.tokens[user['id']] if user else None
        return server._handle_root_mcp({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'play','arguments':{
            'game':game,'action':action,'player_id':str(HUMAN['id']), 'params':params}}},
            **({'path_token':token} if path else {'bearer_token':token}))

    def http(self, user, action='view', *, post=False, **params):
        handler = object.__new__(server.CedarToyHandler)
        body = json.dumps(dict(action=action, **params)).encode()
        handler.headers = {'Content-Length':str(len(body))}
        if user: handler.headers['Authorization'] = 'Bearer ' + self.tokens[user['id']]
        handler.rfile = io.BytesIO(body); handler.wfile = io.BytesIO()
        handler.statuses=[]; handler.response_headers={}
        handler.send_response=lambda status,*_:handler.statuses.append(status)
        handler.send_header=lambda k,v:handler.response_headers.update({k:v})
        handler.end_headers=lambda:None
        handler._handle_deeptalk('POST' if post else 'GET','/api/deeptalk/action' if post else '/api/deeptalk',
                                {k:[str(v)] for k,v in dict(action=action,**params).items()})
        return handler.statuses[-1], json.loads(handler.wfile.getvalue()), handler.response_headers

    def test_real_http_dispatch_routes_to_one_page_and_handler(self):
        for method, path in (("GET", "/deeptalk?round_id=test"),
                             ("GET", "/api/deeptalk?action=context"),
                             ("POST", "/api/deeptalk/action")):
            handler = object.__new__(server.CedarToyHandler)
            handler.path = path
            handler._is_soup_path = lambda: False
            handler._handle_deeptalk = Mock()
            getattr(handler, "do_" + method)()
            handler._handle_deeptalk.assert_called_once()
            self.assertEqual(handler._handle_deeptalk.call_args.args[:2], (method, path.split("?", 1)[0]))

    def activity_events(self):
        with sqlite3.connect(self.db) as conn:
            game_activity.init_db(conn)
            return conn.execute('SELECT game, identity_id, identity_type, action FROM game_activity_events').fetchall()

    def test_all_ten_activity_routes_web_mcp_and_server_topic_attribution(self):
        aliases = list(catalog.ALIASES)
        for index, (alias, topic) in enumerate(catalog.ALIASES.items()):
            with self.subTest(alias=alias):
                before = len(self.activity_events())
                creation = dict(topic_id=topic, request_id=f'activity_create_{index}')
                status, state, _ = self.http(HUMAN, 'create', post=True, **creation)
                self.assertEqual(status, 200)
                self.assertEqual(self.http(HUMAN, 'create', post=True, **creation)[0], 200)
                rid = state['round_id']
                web_answer = dict(round_id=rid, question_id='q1', revision=0,
                                  request_id='activity_web_answer', text='PRIVATE_ANSWER',
                                  topic_id='forged_topic', player_id=str(OTHER_HUMAN['id']))
                self.assertEqual(self.http(HUMAN, 'answer', post=True, **web_answer)[0], 200)
                self.assertEqual(self.http(HUMAN, 'answer', post=True, **web_answer)[0], 200)
                # A fresh request with the same answer changes only revision/receipt.
                self.assertEqual(self.http(HUMAN, 'answer', post=True, **{
                    **web_answer, 'revision': 1, 'request_id': 'same_value_fresh_key'})[0], 200)
                ai_answer = dict(round_id=rid, question_id='q1', revision=0,
                                 request_id='activity_ai_answer', text='PRIVATE_AI_ANSWER')
                self.mcp(AI, 'answer', game=alias, path=bool(index % 2), **ai_answer)
                self.mcp(AI, 'answer', game=alias, **ai_answer)
                # Wrong discovery alias must never override the authorized round's topic.
                wrong_alias = aliases[(index + 1) % len(aliases)]
                self.mcp(AI, 'answer', game=wrong_alias, **{
                    **ai_answer, 'revision': 1, 'request_id': 'activity_ai_edit', 'text': 'PRIVATE_EDIT'})
                for action in ('topics', 'questions', 'question', 'source', 'status', 'result', 'history'):
                    self.mcp(AI, action, game=alias, round_id=rid)
                    self.http(HUMAN, action, round_id=rid, topic_id=topic)
                self.http(HUMAN, 'view', round_id=rid)
                self.http(HUMAN, 'context')
                self.assertEqual(self.http(None, 'answer', post=True, **web_answer)[0], 401)
                self.assertEqual(self.http(OTHER_HUMAN, 'answer', post=True, **web_answer)[0], 404)
                self.mcp(OTHER_AI, 'answer', game=alias, **ai_answer)
                self.mcp(AI, 'answer', game=alias, **{**ai_answer, 'request_id': 'stale_revision_key'})
                self.assertEqual(self.http(HUMAN, 'submit', post=True, round_id=rid, revision=2, confirm=True)[0], 400)
                # Populate the remaining synthetic answers through the store, outside
                # either public recorder, then exercise each real submission route.
                for user in (HUMAN, AI):
                    for number in range(2, catalog.TOPICS[topic][1] + 1):
                        rev = self.call(user, 'status', round_id=rid)['revision']
                        self.call(user, 'answer', round_id=rid, question_id=f'q{number}',
                                  revision=rev, request_id=f'fixture_fill_{number}', skip=True)
                    rev = self.call(user, 'status', round_id=rid)['revision']
                    submit = dict(round_id=rid, revision=rev, confirm=True)
                    for _ in range(2):
                        if user == HUMAN:
                            self.assertEqual(self.http(user, 'submit', post=True, **submit)[0], 200)
                        else:
                            self.mcp(user, 'submit', game=alias, **submit)
                rows = self.activity_events()[before:]
                self.assertEqual(rows, [
                    (alias, '101', 'human', 'create'), (alias, '101', 'human', 'answer'),
                    (alias, '201', 'ai', 'answer'), (alias, '201', 'ai', 'answer'),
                    (alias, '101', 'human', 'submit'), (alias, '201', 'ai', 'submit'),
                ])
                self.assertNotIn('PRIVATE', repr(rows))
                self.assertNotIn(rid, repr(rows))

    def test_deeptalk_concurrent_retry_records_only_one_committed_answer(self):
        params = dict(round_id=self.rid, question_id='q1', revision=0,
                      request_id='concurrent_answer', text='private')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.http(HUMAN, 'answer', post=True, **params), range(2)))
        self.assertEqual([result[0] for result in results], [200, 200])
        self.assertEqual(self.activity_events(), [('dt_relations', '101', 'human', 'answer')])

    def test_generic_deeptalk_mcp_ignores_client_topic_and_failed_response(self):
        self.mcp(AI, 'answer', round_id=self.rid, question_id='q1', revision=0,
                 request_id='generic_answer_key', text='private', topic_id='couple_50')
        self.assertEqual(self.activity_events(), [('dt_relations', '201', 'ai', 'answer')])
        before = self.activity_events()
        for result in ({'ok': False, 'topic_id': 'couple_50'}, {'error': 'rejected'}):
            with patch.object(server, '_play_deeptalk', return_value=result):
                self.mcp(AI, 'answer', round_id=self.rid)
        self.assertEqual(self.activity_events(), before)

    def test_discovery_and_guide_keep_questions_on_demand(self):
        listing = server._tool_list_games()
        self.assertNotIn("deeptalk·", listing)
        guide = json.loads(server._tool_get_guide({"game": "deeptalk"}))["guide"]
        self.assertNotIn("OC", guide)
        for game, tid in catalog.ALIASES.items():
            self.assertIn(tid, guide)
            self.assertIn(game, guide)
            specific = json.loads(server._tool_get_guide({"game": game}))["guide"]
            self.assertIn(catalog.TOPICS[tid][0], specific)
            self.assertIn(str(catalog.TOPICS[tid][1]) + "题", specific)
        self.assertNotIn("测试占位题", guide)
        self.assertNotIn("questions", self.call(AI, "topics"))

    def test_loader_all_ten_and_missing_malformed(self):
        self.assertEqual([t['total'] for t in catalog.topics()], [13,50,14,100,20,100,100,35,30,28])
        self.assertTrue(all(t['available'] for t in catalog.topics()))
        with self.assertRaises(DeeptalkError): catalog.load_topic('../secrets')
        (self.data/'couple_50.json').write_text('{}')
        (self.data/'romance_values_100.json').unlink()
        self.assertEqual([t['available'] for t in catalog.topics()],[True,False,True,False,True,True,True,True,True,True])
        path=self.data/'relations_communication.json'; obj=json.loads(path.read_text()); obj['questions'][1]['id']='q1'; path.write_text(json.dumps(obj))
        with self.assertRaises(DeeptalkError): catalog.load_topic('relations_communication')

    def test_blind_submission_opens_only_submitted_side_in_both_directions(self):
        for first, second in ((HUMAN, AI), (AI, HUMAN)):
            with self.subTest(first=first['id']):
                self.rid = self.store.create(HUMAN, AI, 'relations_communication', f'visibility_{first["id"]}')['round_id']
                for user in (HUMAN, AI):
                    self.fill(user)
                    self.answer(user, 13, skip=True)
                for phase in (0, 1, 2):
                    if phase: self.submit(first if phase == 1 else second)
                    for user, peer in ((HUMAN, AI), (AI, HUMAN)):
                        visible = phase == 2 or (phase == 1 and peer == first)
                        for action in ('status', 'source', 'history'):
                            self.assertNotIn('SECRET_', json.dumps(self.call(user, action, round_id=self.rid)))
                        for action in ('question', 'result'):
                            if action == 'result' and phase == 0:
                                with self.assertRaises(DeeptalkError): self.call(user, action, round_id=self.rid)
                                continue
                            if user == HUMAN:
                                status, data, headers = self.http(user, action, round_id=self.rid, limit=20)
                                self.assertEqual(status, 200)
                                self.assertEqual(headers['Cache-Control'], 'no-store')
                            else:
                                for path in (False, True):
                                    response = self.mcp(user, action, path=path, round_id=self.rid, limit=20)['result']
                                    self.assertFalse(response['isError'])
                                    data = json.loads(response['content'][0]['text'])
                                    self.assertEqual('partner' in data['questions'][0], visible)
                            self.assertEqual(data['peer_visible'], visible)
                            self.assertEqual(data['unlocked'], phase == 2)
                            self.assertEqual(len(data['questions']), 13)
                            for q in data['questions']:
                                self.assertEqual('partner' in q, visible)
                            if visible:
                                self.assertEqual(data['questions'][0]['partner']['text'], f'SECRET_{peer["id"]}_1')
                                self.assertTrue(data['questions'][-1]['partner']['skipped'])
                            else:
                                self.assertNotIn(f'SECRET_{peer["id"]}', json.dumps(data))
                        view = self.http(HUMAN, 'view', round_id=self.rid)[1]
                        self.assertEqual('partner' in view['questions'][0], phase == 2 or (phase == 1 and first == AI))
                    if phase == 1:
                        # Peer visibility never locks the unsubmitted side, even after all answers are saved.
                        self.answer(second, 1)
                        with self.assertRaises(DeeptalkError): self.answer(first, 1, text='late edit')
                        for stranger in (OTHER_HUMAN, OTHER_AI):
                            for action in ('question', 'result', 'source'):
                                with self.assertRaises(DeeptalkError): self.call(stranger, action, round_id=self.rid)

    def test_skip_edit_submit_confirmation_and_immutability(self):
        self.answer(HUMAN,skip=True)
        self.assertTrue(self.call(HUMAN,'question',round_id=self.rid)['questions'][0]['own']['skipped'])
        self.answer(HUMAN,text='回到上一题修改')
        self.assertEqual(self.call(HUMAN,'question',round_id=self.rid)['questions'][0]['own']['text'],'回到上一题修改')
        with self.assertRaises(DeeptalkError): self.submit(HUMAN)
        for q in range(2,14): self.answer(HUMAN,q,skip=True)
        with self.assertRaises(DeeptalkError): self.call(HUMAN,'submit',round_id=self.rid,revision=14,confirm='true')
        self.assertTrue(self.submit(HUMAN)['submitted'])
        self.assertTrue(self.submit(HUMAN)['submitted'])
        with self.assertRaises(DeeptalkError): self.answer(HUMAN,text='late')

    def test_retry_and_stale_write(self):
        params=dict(round_id=self.rid,question_id='q1',revision=0,request_id='retry_0001',text='draft')
        self.call(HUMAN,'answer',**params)
        self.assertEqual(self.call(HUMAN,'answer',**params)['revision'],1)
        with self.assertRaises(DeeptalkError): self.call(HUMAN,'answer',**(params|{'text':'different'}))
        with self.assertRaises(DeeptalkError): self.call(HUMAN,'answer',**(params|{'request_id':'retry_0002'}))
        self.assertEqual(self.store.create(HUMAN,AI,'relations_communication','create_0001')['round_id'],self.rid)
        with self.assertRaises(DeeptalkError): self.store.create(HUMAN,OTHER_AI,'relations_communication','create_0001')

    def test_reanswer_snapshot_and_history_are_per_round(self):
        self.answer(HUMAN,text='old answer')
        topic=self.data/'relations_communication.json'; obj=json.loads(topic.read_text()); obj['questions'][0]['text']='changed'; topic.write_text(json.dumps(obj))
        new=self.store.create(HUMAN,AI,'relations_communication','create_0002')['round_id']
        self.assertNotEqual(new,self.rid)
        self.assertEqual(self.call(HUMAN,'view',round_id=self.rid)['questions'][0]['text'],'测试占位题 1')
        self.assertEqual(self.call(HUMAN,'view',round_id=new)['questions'][0]['text'],'changed')
        history=self.call(HUMAN,'history',limit=1)
        self.assertEqual(history['total'],2); self.assertEqual(len(history['rounds']),1)
        self.assertEqual(set(history['rounds'][0]), {'round_id','title','human_name','ai_name','status','created_at','updated_at','url','sharing_mode','mode','topic_id','human_submitted','ai_submitted'})
        self.assertTrue(history['rounds'][0]['url'].startswith('/deeptalk?round_id='))
        self.assertEqual(self.call(HUMAN,'history',offset=1,limit=1)['rounds'][0]['round_id'],self.rid)
        topic.unlink()
        self.assertEqual(Store(self.db).read(HUMAN,self.rid,action='view')['questions'][0]['own']['text'],'old answer')
        self.assertEqual(self.store.source(HUMAN,self.rid)['title'],'关系与沟通')

    def test_topic_history_paginates_only_authorized_rounds_and_keeps_old_links(self):
        self.fill(HUMAN); self.submit(HUMAN)
        old = self.http(HUMAN, round_id=self.rid)[1]
        ids = [self.store.create(HUMAN, AI, 'relations_communication', f'history_{i:04}',
                                mode='chat' if i % 2 else 'blind')['round_id'] for i in range(23)]
        self.store.create(HUMAN, AI, 'couple_50', 'other_topic')
        self.store.create(OTHER_HUMAN, OTHER_AI, 'relations_communication', 'other_human')
        status, data, _ = self.http(HUMAN, 'history', topic_id='relations_communication', limit=20)
        self.assertEqual(status, 200); self.assertEqual(data['total'], 24)
        self.assertEqual([r['round_id'] for r in data['rounds']], list(reversed(ids))[:20])
        more = self.http(HUMAN, 'history', topic_id='relations_communication', offset=20)[1]
        self.assertEqual([r['round_id'] for r in more['rounds']], list(reversed(ids))[20:]+[self.rid])
        self.assertTrue(more['rounds'][-1]['human_submitted'])
        self.assertFalse(more['rounds'][-1]['ai_submitted'])
        self.assertNotIn('SECRET_', json.dumps(data)+json.dumps(more))
        self.assertEqual(self.call(OTHER_HUMAN, 'history', topic_id='relations_communication')['total'], 1)
        self.assertEqual(self.call(OTHER_AI, 'history', topic_id='relations_communication')['total'], 1)
        self.assertEqual(self.http(HUMAN, round_id=self.rid)[1], old)
        for invalid in ('unknown', "' OR 1=1 --", []):
            with self.assertRaises(DeeptalkError): self.call(AI, 'history', topic_id=invalid)

    def test_identity_and_binding_cannot_be_spoofed(self):
        for user in (OTHER_HUMAN,OTHER_AI,dict(HUMAN,is_ai=True)):
            for action in ('status','question','result','source'):
                with self.assertRaises(DeeptalkError): self.call(user,action,round_id=self.rid)
            with self.assertRaises(DeeptalkError): self.answer(user)
            with self.assertRaises(DeeptalkError): self.submit(user)
        self.assertEqual(self.call(OTHER_HUMAN,'history')['rounds'],[])
        with self.assertRaises(DeeptalkError): self.call(HUMAN,'create',ai_id=202,topic_id='couple_50',request_id='create_fake')
        with self.connect() as conn:
            conn.execute('DELETE FROM user_bindings WHERE ai_user_id=201')
            conn.execute('INSERT INTO user_bindings VALUES (102,201)')
        self.assertEqual(self.call(OTHER_HUMAN,'history')['rounds'],[])
        with self.assertRaises(DeeptalkError): self.call(OTHER_HUMAN,'view',round_id=self.rid)
        self.assertEqual(self.call(AI,'history')['total'],1)  # Original participant remains a participant.
        with self.assertRaises(DeeptalkError): self.call(HUMAN,'create',ai_id=201,topic_id='couple_50',request_id='create_fake')

    def test_mcp_auth_path_bearer_and_minimal_payload(self):
        for path in (False,True):
            response=self.mcp(AI,'questions',path=path,topic_id='romance_values_100')
            self.assertFalse(response['result']['isError'],response)
            data=json.loads(response['result']['content'][0]['text'])
            self.assertEqual(len(data['questions']),1); self.assertNotIn('slot',data)
        response=self.mcp(AI,'questions',topic_id='romance_values_100',limit=100)
        self.assertEqual(len(json.loads(response['result']['content'][0]['text'])['questions']),100)
        for user in (None,HUMAN,OTHER_AI):
            self.assertTrue(self.mcp(user,'status',round_id=self.rid)['result']['isError'])
        self.assertTrue(self.mcp(AI,'view',round_id=self.rid)['result']['isError'])
        self.assertTrue(self.mcp(AI,'create',topic_id='couple_50')['result']['isError'])
        self.assertNotIn('OC',catalog.GUIDE)
        self.assertNotIn('questions',self.call(AI,'status',round_id=self.rid))

    def test_web_auth_readonly_get_and_cross_account(self):
        for user,status in ((None,401),(AI,403),(OTHER_HUMAN,404)):
            self.assertEqual(self.http(user,round_id=self.rid)[0],status)
        self.assertEqual(self.http(HUMAN,'submit',round_id=self.rid,confirm=True)[0],404)
        status,data,_=self.http(HUMAN,'create',post=True,topic_id='couple_50',ai_id=202,request_id='create_999')
        self.assertEqual(status,409)
        status,data,_=self.http(HUMAN,'answer',post=True,round_id=self.rid,question_id='q1',text='human',revision=0,request_id='http_0001',player_id=201)
        self.assertEqual(status,200); self.assertEqual(data['progress']['human']['done'],1); self.assertEqual(data['progress']['ai']['done'],0)

    def test_home_history_never_includes_other_machine_partners(self):
        # A newly bound owner must not inherit private Deeptalk records via machine tabs.
        base={'self':{'user':OTHER_HUMAN,'saves':{}},'machines':[{'user':AI,'saves':{}}]}
        with patch.object(server,'_account_web_saves',return_value=base), patch.object(server,'_duel_history_for_user',return_value={}):
            result=server._account_web_history(self.tokens[102])
        self.assertEqual(result['self']['deeptalk_history']['rounds'],[])
        self.assertNotIn('deeptalk_history',result['machines'][0])

    def test_concurrent_retry_and_submit(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:self.store.create(HUMAN,AI,'couple_50','concurrent_create'),range(6)))
        self.assertEqual(len({r['round_id'] for r in results}),1)
        self.fill(HUMAN); self.fill(AI)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.submit,[HUMAN,AI]))
        self.assertTrue(self.store.read(HUMAN,self.rid)['unlocked'])

    def test_cross_process_persistence_without_guest_ttl(self):
        self.fill(HUMAN); self.fill(AI); self.submit(HUMAN); self.submit(AI)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE deeptalk_rounds SET created_at='2000-01-01T00:00:00+00:00',updated_at='2000-01-01T00:00:00+00:00'")
        code='from deeptalk.store import Store; import sys,json; print(json.dumps(Store(sys.argv[1]).read({"id":101,"is_ai":False},sys.argv[2],action="view")))'
        result=subprocess.run([sys.executable,'-c',code,str(self.db),self.rid],cwd=ROOT,capture_output=True,text=True,check=True)
        data=json.loads(result.stdout); self.assertTrue(data['unlocked']); self.assertEqual(len(data['questions']),13)
        self.assertEqual(data['questions'][0]['partner']['text'],'SECRET_201_1')

    def test_invalid_inputs_leave_state_unchanged(self):
        for extra in ({'skip':'true'},{'text':''},{'text':'x'*8001}):
            with self.assertRaises(DeeptalkError): self.answer(HUMAN,**extra)
        self.assertEqual(self.store.read(HUMAN,self.rid)['revision'],0)
        for params in ({'number':0},{'limit':101},{'number':True}):
            with self.assertRaises(DeeptalkError): self.call(AI,'questions',topic_id='couple_50',**params)

    def shared_round(self, mode='blind'):
        with self.connect() as conn:
            conn.execute('INSERT INTO user_bindings VALUES (101,202)')
        result = self.call(HUMAN, 'create', topic_id='relations_communication', request_id='shared_create', mode=mode)
        self.assertEqual(result['sharing_mode'], 'bound_shared')
        return result['round_id']

    def test_shared_binding_history_and_legacy_isolation(self):
        legacy = self.rid
        self.answer(AI, text='LEGACY_PRIVATE')
        self.rid = self.shared_round()
        for ai in (AI, OTHER_AI):
            for path in (False, True):
                result = self.mcp(ai, 'history', path=path)
                self.assertFalse(result['result']['isError'])
                self.assertIn(self.rid, json.dumps(result))
            self.assertEqual(self.call(ai, 'status', round_id=self.rid)['sharing_mode'], 'bound_shared')
        self.assertNotIn(legacy, json.dumps(self.call(OTHER_AI, 'history')))
        for action in ('status', 'question', 'result', 'source', 'answer', 'submit'):
            params = dict(round_id=legacy, question_id='q1', text='intrusion', revision=1,
                          request_id='intruder_001', confirm=True)
            with self.assertRaises(DeeptalkError): self.call(OTHER_AI, action, **params)
        with self.connect() as conn:
            conn.execute('DELETE FROM user_bindings WHERE human_user_id=101 AND ai_user_id=202')
        for action in ('status', 'question', 'result', 'source', 'answer', 'submit'):
            params = dict(round_id=self.rid, question_id='q1', text='intrusion', revision=0,
                          request_id='intruder_002', confirm=True, human_id=101, bound_human_ids=[101])
            with self.assertRaises(DeeptalkError): self.call(OTHER_AI, action, **params)
            self.assertTrue(self.mcp(OTHER_AI, action, **params)['result']['isError'])
        self.assertNotIn(self.rid, json.dumps(self.call(OTHER_AI, 'history')))
        self.assertEqual(self.http(OTHER_HUMAN, round_id=self.rid)[0], 404)
        self.assertNotIn(self.rid, json.dumps(self.call(OTHER_HUMAN, 'history')))
        # Binding later grants shared rounds, but never the original single-AI round.
        with self.connect() as conn: conn.execute('INSERT INTO user_bindings VALUES (101,202)')
        self.assertIn(self.rid, json.dumps(self.call(OTHER_AI, 'history')))
        with self.assertRaises(DeeptalkError): self.call(OTHER_AI, 'question', round_id=legacy)
        self.assertEqual(self.call(AI, 'question', round_id=legacy)['questions'][0]['own']['text'], 'LEGACY_PRIVATE')

    def test_shared_drafts_revision_and_one_ai_submission(self):
        self.rid = self.shared_round()
        self.fill(HUMAN)
        def edit(ai):
            try:
                return self.call(ai, 'answer', round_id=self.rid, question_id='q1', text=f'SHARED_{ai["id"]}',
                                 revision=0, request_id=f'shared_{ai["id"]}')
            except DeeptalkError as err:
                return err.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, (AI, OTHER_AI)))
        self.assertEqual(sum(r == 409 for r in results), 1)
        first = self.call(AI, 'question', round_id=self.rid)
        second = self.call(OTHER_AI, 'question', round_id=self.rid)
        self.assertEqual(first, second)
        self.assertEqual(first['revision'], 1)
        with self.assertRaises(DeeptalkError):
            self.call(OTHER_AI, 'submit', round_id=self.rid, revision=0, confirm=True)
        for q in range(2,14): self.answer(AI if q % 2 else OTHER_AI, q)
        # A shared AI submission opens only the AI side; its binding peers share that lock.
        self.submit(OTHER_AI)
        for user in (HUMAN, AI, OTHER_AI):
            for action in ('question', 'result'):
                payload = self.call(user, action, round_id=self.rid)
                self.assertEqual(payload['peer_visible'], user == HUMAN)
                self.assertFalse(payload['unlocked'])
                if user['is_ai']: self.assertNotIn('SECRET_101', json.dumps(payload))
                else: self.assertIn('SHARED_', json.dumps(payload['questions'][0]['partner']))
        self.assertIn('SHARED_', json.dumps(self.http(HUMAN, round_id=self.rid)[1]))
        self.submit(HUMAN)
        for ai in (AI, OTHER_AI):
            result = self.mcp(ai, 'result', round_id=self.rid)
            self.assertFalse(result['result']['isError'])
            self.assertIn('SECRET_101', json.dumps(result))
            with self.assertRaises(DeeptalkError): self.answer(ai, text='late edit')
        self.assertTrue(self.call(HUMAN, 'view', round_id=self.rid)['unlocked'])

    def test_shared_creation_compatibility_and_server_identity(self):
        self.assertEqual(self.http(HUMAN, 'create', post=True, ai_id=201,
                         topic_id='relations_communication', request_id='old_client_1')[0], 409)
        self.rid = self.shared_round()
        retry = self.call(HUMAN, 'create', topic_id='relations_communication', request_id='shared_create', human_id=102)
        self.assertEqual(retry['round_id'], self.rid)
        self.assertNotIn(self.rid, json.dumps(self.call(OTHER_HUMAN, 'history')))
        with self.connect() as conn: conn.execute('DELETE FROM user_bindings WHERE human_user_id=101')
        self.assertEqual(self.http(HUMAN, 'create', post=True, topic_id='relations_communication', request_id='no_bound_1')[0], 403)
        self.assertEqual(self.http(HUMAN, 'questions', topic_id='relations_communication')[0], 200)
        self.assertEqual(self.http(HUMAN, round_id=self.rid)[0], 200)
        self.assertEqual(self.call(AI, 'history')['total'], 1)  # Only its original legacy round.
        with self.assertRaises(DeeptalkError): self.call(AI, 'status', round_id=self.rid)

    def test_legacy_migration_is_idempotent_and_preserves_snapshot(self):
        self.answer(HUMAN, text='old draft')
        with sqlite3.connect(self.db) as conn:
            before = conn.execute('SELECT snapshot,human_answers,ai_answers FROM deeptalk_rounds').fetchone()
            conn.execute('ALTER TABLE deeptalk_rounds DROP COLUMN sharing_mode')
            conn.execute('ALTER TABLE deeptalk_rounds DROP COLUMN mode')
        with ThreadPoolExecutor(max_workers=4) as pool:
            reads = list(pool.map(lambda _: Store(self.db).read(HUMAN,self.rid,action='view'), range(4)))
        self.assertTrue(all(r['sharing_mode'] == 'single_ai' and r['mode'] == 'blind' for r in reads))
        self.assertEqual(reads[0]['questions'][0]['own']['text'], 'old draft')
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(before, conn.execute('SELECT snapshot,human_answers,ai_answers FROM deeptalk_rounds').fetchone())
        self.assertEqual(self.call(AI, 'history')['total'], 1)

    def test_chat_live_answers_shared_permissions_and_completion(self):
        self.rid = self.shared_round(mode='chat')
        self.assertEqual(self.call(AI, 'status', round_id=self.rid)['mode'], 'chat')
        for user in (HUMAN, AI, OTHER_AI):
            empty = self.call(user, 'result', round_id=self.rid)
            self.assertNotIn('partner', empty['questions'][0])
        # Human HTTP -> both AIs via their own MCP tokens, before any whole-form submit.
        status, data, _ = self.http(HUMAN, 'answer', post=True, round_id=self.rid, question_id='q1',
                                    text='LIVE_HUMAN', revision=0, request_id='live_human_1')
        self.assertEqual(status, 200)
        for ai in (AI, OTHER_AI):
            for path in (False, True):
                for action in ('question', 'result'):
                    response = self.mcp(ai, action, path=path, round_id=self.rid)
                    self.assertFalse(response['result']['isError'])
                    self.assertIn('LIVE_HUMAN', json.dumps(response))
        self.assertNotIn('partner', self.call(AI, 'question', round_id=self.rid, number=2)['questions'][0])
        params = dict(round_id=self.rid, question_id='q1', text='LIVE_AI', revision=0, request_id='live_ai_001')
        self.assertFalse(self.mcp(OTHER_AI, 'answer', **params)['result']['isError'])
        self.assertEqual(self.call(AI, 'answer', **params)['revision'], 1)  # shared retry
        self.assertTrue(self.mcp(AI, 'answer', **(params | {'request_id':'live_ai_002'}))['result']['isError'])
        status, view, _ = self.http(HUMAN, round_id=self.rid)
        self.assertEqual(status, 200)
        self.assertEqual(view['questions'][0]['partner']['text'], 'LIVE_AI')
        self.assertFalse(view['unlocked'])
        self.assertNotIn('partner', view['questions'][1])
        for user in (HUMAN, AI):
            self.assertNotIn('LIVE_', json.dumps(self.call(user, 'status', round_id=self.rid)))
            self.assertNotIn('LIVE_', json.dumps(self.call(user, 'history')))
        # Invalid edits never become visible; rejected mode changes cannot open blind rounds.
        self.assertEqual(self.http(HUMAN, 'answer', post=True, round_id=self.rid, question_id='q1',
                         revision=1, text='', request_id='failed_live_1')[0], 400)
        self.assertEqual(self.call(AI, 'question', round_id=self.rid)['questions'][0]['partner']['text'], 'LIVE_HUMAN')
        for user in (HUMAN, AI):
            for q in range(2, 14): self.answer(user, q, skip=True)
        self.submit(AI)
        self.assertFalse(self.call(HUMAN, 'result', round_id=self.rid)['unlocked'])
        self.submit(HUMAN)
        self.assertTrue(self.call(OTHER_AI, 'result', round_id=self.rid)['unlocked'])
        with self.connect() as conn:
            conn.execute('DELETE FROM user_bindings WHERE human_user_id=101 AND ai_user_id=202')
        for action in ('status', 'question', 'result', 'source', 'answer', 'submit'):
            self.assertTrue(self.mcp(OTHER_AI, action, **(params | {'confirm':True}))['result']['isError'])
        self.assertNotIn(self.rid, json.dumps(self.call(OTHER_AI, 'history')))
        for action in ('view', 'question', 'result', 'source'):
            self.assertEqual(self.http(OTHER_HUMAN, action, round_id=self.rid)[0], 404)

    def test_modes_are_immutable_validated_and_old_shared_rounds_stay_blind(self):
        self.rid = self.shared_round()
        self.answer(HUMAN, text='BLIND_SECRET')
        self.answer(AI, text='BLIND_AI')
        for action in ('question', 'result'):
            result = self.mcp(AI, action, round_id=self.rid, mode='chat')
            self.assertNotIn('BLIND_SECRET', json.dumps(result))
        self.assertNotIn('BLIND_AI', json.dumps(self.http(HUMAN, round_id=self.rid, mode='chat')[1]))
        with self.assertRaises(DeeptalkError):
            self.call(HUMAN, 'create', topic_id='relations_communication', request_id='shared_create', mode='chat')
        for mode in ('face_to_face', '', None, [], True):
            with self.assertRaises(DeeptalkError):
                self.call(HUMAN, 'create', topic_id='relations_communication', request_id='invalid_mode', mode=mode)
        with sqlite3.connect(self.db) as conn:
            conn.execute('ALTER TABLE deeptalk_rounds DROP COLUMN mode')
        fresh = self.call(HUMAN, 'view', round_id=self.rid)
        self.assertEqual((fresh['sharing_mode'], fresh['mode']), ('bound_shared', 'blind'))
        self.assertNotIn('partner', fresh['questions'][0])
        self.assertEqual(self.call(OTHER_AI, 'status', round_id=self.rid)['mode'], 'blind')
        with self.connect() as conn: conn.execute('DELETE FROM user_bindings WHERE human_user_id=101')
        for mode in ('blind', 'chat'):
            self.assertEqual(self.http(HUMAN, 'create', post=True, topic_id='relations_communication',
                             request_id='no_machines', mode=mode)[0], 403)

    def test_chat_concurrent_edits_share_revision(self):
        self.rid = self.shared_round(mode='chat')
        def edit(user):
            try:
                return self.call(user, 'answer', round_id=self.rid, question_id='q1', revision=0,
                                 text=user['username'], request_id=f'chat_race_{user["id"]}')
            except DeeptalkError as err:
                return err.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, (AI, OTHER_AI)))
        self.assertEqual(sum(r == 409 for r in results), 1)
        self.assertEqual(self.call(AI, 'status', round_id=self.rid)['revision'], 1)
        self.assertEqual(self.call(HUMAN, 'question', round_id=self.rid)['questions'][0]['partner'],
                         self.call(AI, 'question', round_id=self.rid)['questions'][0]['own'])

    def test_public_sources_keep_attribution_without_editorial_metadata(self):
        with patch.object(catalog, 'DATA_DIR', ROOT / 'deeptalk/data'):
            for tid in catalog.TOPICS:
                raw = self.call(AI, 'source', topic_id=tid)
                public = self.http(HUMAN, 'source', topic_id=tid)[1]
                self.assertIn('adaptation_note', raw)
                self.assertNotIn('adaptation_note', public)
                self.assertNotIn('OC', json.dumps(public, ensure_ascii=False))
                self.assertNotIn('docs/', json.dumps(public))
                for entry in public['entries']:
                    self.assertTrue(entry['author'])
                    self.assertTrue(entry['source_url'].startswith('https://'))
                    self.assertTrue(entry['license'])
                self.assertTrue(public['note'])


if __name__ == '__main__':
    unittest.main()
