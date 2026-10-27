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
        env={'AUTOCHESS_ENABLED':'1','AUTOCHESS_ORIGIN':f'http://127.0.0.1:{port}','CHESS_PLATFORM_SECRET':self.secret,'CHESS_ROOM_DATA':str(self.base/'rooms'),'CHESS_ROOM_PORT':str(port),'CHESS_SLIM':'1','CHESS_CEDAR_MODE':'platform','AUTOCHESS_AUTHOR_USER_IDS':'101'}
        self.stack.enter_context(patch.dict(os.environ,env))
        if os.environ.get('AUTOCHESS_TEST_DIST'):
            self.stack.enter_context(patch.object(server.autochess_web,'DIST',Path(os.environ['AUTOCHESS_TEST_DIST'])))
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
        path=op if op in ('bootstrap','stats','economy') else 'room'
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

    def test_economy_identity_slots_binding_and_root_mcp(self):
        from concurrent.futures import ThreadPoolExecutor
        f = self.f
        def meta(op, who='101', **params):
            return f.http.post(f.origin+'/autochess/api/economy', headers={'Authorization':'Bearer '+f.tokens[who]}, json={'op':op, **params})
        self.assertTrue(f.call('bootstrap').json()['economy'])
        first = meta('profile', player_id='303').json()['self']
        self.assertEqual(first['coins'], {'human':0, 'bot':0})
        self.assertNotEqual(first['id'], meta('profile', slot=2).json()['self']['id'])
        self.assertNotEqual(first['id'], meta('profile', '303').json()['self']['id'])
        self.assertEqual(meta('profile', '202').status_code, 403)  # AI uses root MCP
        self.assertEqual(meta('profile', ai_id='404').status_code, 403)
        self.assertEqual(meta('grantCoins', amount=100000).status_code, 400)
        root = server._tool_play({'game':'autochess','action':'meta','params':{'op':'profile'}},path_token=f.tokens['202'])
        root = json.loads(root) if isinstance(root,str) else root
        self.assertTrue(root['self']['isAi']);self.assertEqual(root['self']['ownerName'], '临时棋手')
        command = {'action':'start','requestId':'web-wheel-start','revision':0}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:meta('wheel_action', command=command).json(), range(2)))
        self.assertEqual(results[0], results[1]);self.assertEqual(results[0]['state']['tickets'],30)
        self.assertEqual(meta('wheel', slot=2).json()['state']['tickets'],0)
        self.assertEqual(meta('wheel_action',command={**command,'action':'buy_tickets','count':10,'requestId':'no-money-tickets','revision':1}).status_code,400)
        machine = meta('profile', ai_id='202').json()['self'];self.assertTrue(machine['isAi'])
        self.assertNotEqual(machine['id'], first['id'])
        self.assertEqual(meta('wheel', ai_id='202').status_code, 200)
        for op, args in [('buy', {'cosmeticId':'skin-rose-court','requestId':'owner-buy-for-ai'}), ('equip', {'cosmeticId':'skin-revival','requestId':'owner-equip-ai'}), ('claim', {'requestId':'owner-claim-ai'}), ('wheel_action', {'command': {'action':'start','revision':0,'requestId':'owner-wheel-ai'}})]:
            self.assertEqual(meta(op, ai_id='202', **args).status_code, 403, op)
        with server._db_connect() as conn:conn.execute('DELETE FROM user_bindings')
        self.assertEqual(meta('profile', ai_id='202').status_code,403)
        root = server._tool_play({'game':'autochess','action':'meta','params':{'op':'profile'}},path_token=f.tokens['202'])
        root = json.loads(root) if isinstance(root,str) else root
        self.assertNotIn('ownerName',root['self'])
        self.assertEqual(root['recipients'],[])

    def test_wallet_in_daily_backup_archive_without_r2_upload(self):
        import sqlite3
        import tarfile
        f = self.f
        made=f.call('create',requestId='backup-room-test').json()
        source=f.base/'rooms'
        root=f.base/'backup-root';rooms=root/'data/autochess_rooms';rooms.mkdir(parents=True)
        shutil.copy2(source/(made['roomId']+'.json'),rooms/(made['roomId']+'.json'))
        relative='data/autochess_rooms/economy/accounts.db'
        (root/relative).parent.mkdir(parents=True)
        with sqlite3.connect('file:'+str(source/'economy/accounts.db')+'?mode=ro',uri=True) as original, sqlite3.connect(root/relative) as copy:
            original.backup(copy)
        for rel in ('turtle-soup/backend/turtle_soup.db','toy-platform/toy_accounts.db','vendor/duel/data/duel.db'):
            target=root/rel;target.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(target) as db:db.execute('CREATE TABLE fixture(id INTEGER)')
        output=f.base/'backup-output'
        result=subprocess.run(['bash',str(ROOT/'scripts/backup_cedartoy.sh')],env={**os.environ,'BACKUP_APP_ROOT':str(root),'BACKUP_ROOT':str(output),'R2_REMOTE':'','RETRY_DELAY_SECONDS':'0'},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        restore=f.base/'restore';restore.mkdir()
        with tarfile.open(next(output.glob('data_*.tar.gz'))) as archive:
            archive.extractall(restore)
        with sqlite3.connect(restore/relative) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            state=json.loads(db.execute('SELECT body FROM state').fetchone()[0])
            self.assertEqual(state['version'],1);self.assertEqual(state['sessions'],{})
            db.execute('SELECT COUNT(*) FROM receipts').fetchone()
        self.assertTrue((restore/'data/autochess_rooms'/(made['roomId']+'.json')).is_file())

    def test_launcher_refuses_double_writer_and_non_slim(self):
        env={**os.environ,'PATH':str(Path(NODE).parent)+':'+os.environ['PATH']}
        command=['python3',str(ROOT/'scripts/run_autochess.py')]
        locked=subprocess.run(command,env=env,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(locked.returncode,0)
        self.assertIn('already in use',locked.stderr)
        wrong=subprocess.run(command,env={**env,'CHESS_SLIM':'0','CHESS_CEDAR_MODE':''},capture_output=True,text=True,timeout=5)
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
