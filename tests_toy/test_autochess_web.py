"""Real loopback satellite + Cedar HTTP/auth; all account/game data is temporary."""
import asyncio
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import httpx
import server
import duel_async_gateway as edge
from autochess_adapter import gateway

ROOT=Path(__file__).resolve().parents[1]
NODE=os.environ.get('AUTOCHESS_TEST_NODE') or shutil.which('node') or ''
NODE_OK=bool(NODE and tuple(int(x) for x in subprocess.check_output([NODE,'--version'],text=True).strip().lstrip('v').split('.')[:2]) >= (22,13))

class Fixture:
    def __enter__(self):
        self.stack=ExitStack()
        try:
            return self.start()
        except BaseException:
            self.stack.close()
            raise

    def start(self):
        if not NODE_OK:
            raise RuntimeError("Set AUTOCHESS_TEST_NODE to Node >=22.13")
        self.temp=self.stack.enter_context(tempfile.TemporaryDirectory(prefix='cedar-autochess-test-'))
        self.base=Path(self.temp)
        self.stack.enter_context(patch.object(server,'TOY_SECRET',secrets.token_hex(32)))
        self.stack.enter_context(patch.object(server,'TURTLE_DB_PATH',self.base/'accounts.db'))
        self.stack.enter_context(patch.object(server,'SESSIONS_DB_PATH',self.base/'sessions.db'))
        self.stack.enter_context(patch.object(server,'DUEL_GATEWAY_SHARED_SECRET','test-gateway-'+secrets.token_hex(24)))
        # Bypass unrelated migration/announcements only; actual auth, binding, slots, MCP dispatch remain real.
        for name in ('_auto_migrate_legacy_account_saves','_reject_claimed_guest'):
            self.stack.enter_context(patch.object(server,name,return_value=[]))
        for name in ('_anti_addiction_context','_anti_addiction_preflight'):
            self.stack.enter_context(patch.object(server,name,return_value=None))
        self.stack.enter_context(patch.object(server,'_finalize_play_response',side_effect=lambda response,**kwargs:response))
        self.stack.enter_context(patch.object(server,'_check_request_rate_limit',return_value=True))
        with server._db_connect() as conn:
            conn.executescript('''CREATE TABLE toy_users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
                is_ai INTEGER, is_admin INTEGER DEFAULT 0, avatar_type TEXT, avatar_value TEXT,
                deleted_at TEXT, last_active_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE user_bindings(human_user_id INTEGER,ai_user_id INTEGER);
                INSERT INTO toy_users(id,username,is_ai,is_admin) VALUES(101,'临时棋手',0,0),(202,'临时小机',1,0),(303,'临时朋友',0,1),(404,'其他小机',1,0);
                INSERT INTO user_bindings VALUES(101,202);''')
            server._init_account_security_schema(conn)
            self.tokens={str(row['id']):server._issue_initial_account_token_in_transaction(conn,dict(row)) for row in conn.execute('SELECT * FROM toy_users').fetchall()}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.secret=secrets.token_hex(32)
        env={'AUTOCHESS_ENABLED':'1','AUTOCHESS_ORIGIN':f'http://127.0.0.1:{port}','CHESS_PLATFORM_SECRET':self.secret,'CHESS_ROOM_DATA':str(self.base/'rooms'),'CHESS_ROOM_PORT':str(port),'CHESS_SLIM':'1','AUTOCHESS_AUTHOR_USER_IDS':'101'}
        self.stack.enter_context(patch.dict(os.environ,env))
        launch_env={**os.environ,'PATH':str(Path(NODE).parent)+':'+os.environ['PATH']}
        self.log=self.stack.enter_context(open(self.base/'node.log','w+'))
        self.node=subprocess.Popen(['python3',str(ROOT/'scripts/run_autochess.py')],env=launch_env,stdout=self.log,stderr=self.log)
        self.stack.callback(self.stop_node)
        self.http=self.stack.enter_context(httpx.Client(trust_env=False,timeout=8))
        for _ in range(600):
            try:
                if self.http.get(env['AUTOCHESS_ORIGIN']+'/health',headers={'X-Autochess-Platform-Secret':self.secret}).status_code==200:break
            except httpx.HTTPError:pass
            if self.node.poll() is not None:
                self.log.seek(0);raise RuntimeError(self.log.read())
            time.sleep(.05)
        else:raise RuntimeError('satellite did not start')
        class QuietHandler(server.CedarToyHandler):
            def log_message(self,*args):pass
        self.web=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        self.thread=threading.Thread(target=self.web.serve_forever,daemon=True);self.thread.start()
        self.stack.callback(self.stop_web)
        self.origin=f'http://127.0.0.1:{self.web.server_port}'
        return self
    def stop_node(self):
        self.node.terminate();self.node.wait(timeout=5)
    def stop_web(self):
        self.web.shutdown();self.web.server_close();self.thread.join()
    def __exit__(self,*args):self.stack.__exit__(*args)
    def call(self,op,who='101',**body):
        path=op if op in ('bootstrap','stats') else 'room'
        return self.http.post(self.origin+'/autochess/api/'+path,headers={'Authorization':'Bearer '+self.tokens[who]},json={'op':op,**body})

@unittest.skipUnless(NODE_OK and (ROOT/'vendor/autochess/node_modules/tsx').exists(),'requires locked AutoChess dependencies and AUTOCHESS_TEST_NODE')
class WebIntegration(unittest.TestCase):
    def setUp(self):self.f=Fixture();self.addCleanup(self.f.__exit__,None,None,None);self.f.__enter__()
    def test_auth_bindings_slots_invites_and_aggregate_grant(self):
        f=self.f
        self.assertEqual(f.http.post(f.origin+'/autochess/api/bootstrap',json={}).status_code,403)
        self.assertEqual(f.call('bootstrap','202').status_code,403)
        self.assertEqual(f.call('create',requestId='bad-household',ai_ids=['404']).status_code,403)
        made=f.call('create',requestId='web-http-create',slot=2,seats=8,ai_ids=['202'],pack='jianghu').json()
        self.assertIn('roomId',made,made)
        self.assertEqual(f.call('bootstrap',slot=1).json()['rooms'],[])
        self.assertEqual(len(f.call('bootstrap',slot=2,ai_id='202').json()['rooms']),1)
        self.assertEqual(f.call('state','303',roomId=made['roomId']).status_code,400)
        self.assertEqual(f.call('join','303',invitation=made['invitation']).status_code,200)
        self.assertEqual(f.call('start','303',roomId=made['roomId'],requestId='wrong-owner-start').status_code,400)
        started=f.call('start',slot=2,roomId=made['roomId'],requestId='right-owner-start').json()
        self.assertEqual(started['phase'],'prepare');self.assertEqual(len(started['seats']),8)
        self.assertNotIn('token',json.dumps(started));self.assertNotIn('accountId',json.dumps(started))
        view=f.call('state',slot=2,ai_id='202',roomId=made['roomId']).json();self.assertNotIn('self',view);self.assertEqual(view['seatIndex'],1)
        self.assertEqual(f.call('action',slot=2,ai_id='202',roomId=made['roomId']).status_code,403)
        with server._db_connect() as conn:conn.execute('DELETE FROM user_bindings')
        self.assertEqual(f.call('state',slot=2,ai_id='202',roomId=made['roomId']).status_code,403)
        stats=f.call('stats').json();self.assertEqual(set(stats),{'range','generated_at','active_humans','active_machines','created','started','finished'})
        self.assertEqual(stats['created'],1);self.assertEqual(stats['started'],1)
        self.assertEqual(f.call('stats','303').status_code,403) # site admin is not author
        with patch.dict(os.environ,{'AUTOCHESS_AUTHOR_USER_IDS':''}):
            self.assertEqual(f.call('stats').status_code,403)
        for path in ['/autochess/room','/autochess/account','/autochess/mcp','/autochess/api/account','/autochess/static/%2e%2e/package.json']:
            self.assertEqual(f.http.get(f.origin+path).status_code,404,path)
        foreign=f.http.post(f.origin+'/autochess/api/bootstrap',json={},headers={'Authorization':'Bearer '+f.tokens['101'],'Origin':'https://evil.test'})
        self.assertEqual(foreign.status_code,403)
        self.assertEqual(f.http.post(f.origin+'/_internal/autochess-gateway/prepare',json={}).status_code,404)

    def test_homepage_stats_access_and_ranges(self):
        f = self.f
        def access(who=None):
            headers = {'Authorization': 'Bearer ' + f.tokens[who]} if who else {}
            return f.http.post(f.origin + '/autochess/api/stats', headers=headers, json={'op': 'access'})
        # Access authenticates locally and never forwards credentials or requests to the satellite.
        with patch('autochess_adapter.web.request', side_effect=AssertionError('access must stay local')):
            self.assertEqual(access().status_code, 403)
            self.assertEqual(access('202').status_code, 403)
            response = access('101')
            self.assertEqual(response.json(), {'allowed': True})
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(access('303').json(), {'allowed': False})  # even site admin
        for period in ('10m', '1h', '6h', '12h', '24h'):
            data = f.call('stats', range=period).json()
            self.assertEqual(data['range'], period)
            self.assertEqual({k: v for k, v in data.items() if k not in ('range', 'generated_at')},
                             dict.fromkeys(('active_humans', 'active_machines', 'created', 'started', 'finished'), 0))
        # Revoking a grant after a successful access query must deny the subsequent read.
        with patch.dict(os.environ, {'AUTOCHESS_AUTHOR_USER_IDS': ''}):
            self.assertEqual(access('101').json(), {'allowed': False})
            self.assertEqual(f.call('stats').status_code, 403)
            self.assertEqual(f.call('stats', '303').status_code, 403)
        self.assertEqual(f.http.get(f.origin + '/autochess/api/stats?op=access').status_code, 404)

    def test_launcher_refuses_double_writer_and_non_slim(self):
        env={**os.environ,'PATH':str(Path(NODE).parent)+':'+os.environ['PATH']}
        command=['python3',str(ROOT/'scripts/run_autochess.py')]
        locked=subprocess.run(command,env=env,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(locked.returncode,0)
        self.assertIn('already in use',locked.stderr)
        wrong=subprocess.run(command,env={**env,'CHESS_SLIM':'0'},capture_output=True,text=True,timeout=5)
        self.assertNotEqual(wrong.returncode,0)
        self.assertIn('CHESS_SLIM=1',wrong.stderr)

    def test_real_async_mcp_wait_cancel_path_and_bearer(self):
        f=self.f
        async def run():
            with patch.object(edge,'CEDARTOY_ORIGIN',f.origin):
                app=edge.create_app(shared_secret=server.DUEL_GATEWAY_SHARED_SECRET,keepalive_seconds=.05)
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as client:
                        async def rpc(action,params={},path='/mcp',bearer=True):
                            result=await client.post(path,headers={'Authorization':'Bearer '+f.tokens['202']} if bearer else {},json={'jsonrpc':'2.0','id':7,'method':'tools/call','params':{'name':'play','arguments':{'game':'autochess','action':action,'params':params}}})
                            data=result.json();self.assertFalse(data['result']['isError'],data);return json.loads(data['result']['content'][0]['text'])
                        made=await rpc('create',{'requestId':'async-create-123','mode':'duel'},path='/'+f.tokens['202'],bearer=False)
                        state=await rpc('read',{'roomId':made['roomId']})
                        wait=asyncio.create_task(rpc('read',{'roomId':made['roomId'],'after':state['revision'],'waitMs':25000}))
                        await asyncio.sleep(.2)
                        self.assertFalse(wait.done())
                        rooms=await rpc('rooms');self.assertEqual(len(rooms['rooms']),1)
                        cancelled=await rpc('cancel_wait',{'roomId':made['roomId']});self.assertEqual(cancelled['status'],'wait_cancelled')
                        done=await asyncio.wait_for(wait,2);self.assertEqual(done['status'],'wait_cancelled')
                        # A newer waiter cancels only its predecessor. Disconnect cleans its ticket/slot.
                        params={'roomId':made['roomId'],'after':state['revision'],'waitMs':25000}
                        old=asyncio.create_task(rpc('read',params));await asyncio.sleep(.15)
                        newer=asyncio.create_task(rpc('read',params));await asyncio.sleep(.15)
                        self.assertEqual((await asyncio.wait_for(old,2))['status'],'wait_cancelled')
                        newer.cancel()
                        with self.assertRaises(asyncio.CancelledError):await newer
                        await asyncio.sleep(.15)
                        self.assertEqual(app.state.autochess_slots._value,80)
                        self.assertEqual(len(gateway._TICKETS),0)
        asyncio.run(run())

if __name__=='__main__':unittest.main()
