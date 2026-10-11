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
        revision = self.store.read(user, self.rid)['revision']
        return self.call(user, 'answer', round_id=self.rid, question_id=f'q{q}', revision=revision,
                         request_id=f'{user["id"]}_{revision:08}', **({'text': f'SECRET_{user["id"]}_{q}'} | extra))

    def fill(self, user):
        for q in range(1,14):
            self.answer(user,q)

    def submit(self, user):
        return self.call(user,'submit',round_id=self.rid,revision=self.store.read(user,self.rid)['revision'],confirm=True)

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

    def test_every_read_path_hides_peer_before_both_submit(self):
        for user in (HUMAN, AI): self.fill(user)
        for phase in (0,1):
            if phase: self.submit(HUMAN)
            for user,peer in ((HUMAN,AI),(AI,HUMAN)):
                for action in ('status','question','history','source'):
                    payload=self.call(user,action,round_id=self.rid,number=1)
                    self.assertNotIn(f'SECRET_{peer["id"]}',json.dumps(payload))
                with self.assertRaises(DeeptalkError): self.call(user,'result',round_id=self.rid)
                if not user['is_ai']:
                    status,data,headers=self.http(user,round_id=self.rid)
                    self.assertEqual(status,200); self.assertEqual(headers['Cache-Control'],'no-store')
                    self.assertNotIn('SECRET_201',json.dumps(data)); self.assertNotIn('partner',json.dumps(data))
                else:
                    payload=self.mcp(user,'question',round_id=self.rid)
                    self.assertFalse(payload['result']['isError']); self.assertNotIn('SECRET_101',json.dumps(payload))
                    self.assertTrue(self.mcp(user,'result',round_id=self.rid)['result']['isError'])
        self.submit(AI)
        result=self.call(HUMAN,'view',round_id=self.rid)
        self.assertTrue(result['unlocked']); self.assertEqual(len(result['questions']),13)
        self.assertEqual(result['questions'][0]['partner']['text'],'SECRET_201_1')
        result=self.call(AI,'result',round_id=self.rid)
        self.assertEqual(result['questions'][0]['partner']['text'],'SECRET_101_1')

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
        self.assertEqual(set(history['rounds'][0]), {'round_id','title','human_name','ai_name','status','created_at','updated_at','url'})
        self.assertTrue(history['rounds'][0]['url'].startswith('/deeptalk?round_id='))
        self.assertEqual(self.call(HUMAN,'history',offset=1,limit=1)['rounds'][0]['round_id'],self.rid)
        topic.unlink()
        self.assertEqual(Store(self.db).read(HUMAN,self.rid,action='view')['questions'][0]['own']['text'],'old answer')
        self.assertEqual(self.store.source(HUMAN,self.rid)['title'],'关系与沟通')

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
        self.assertEqual(status,403)
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


if __name__ == '__main__':
    unittest.main()
