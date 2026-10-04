"""Host-only real engine + real platform authentication + original browser UI.

Loaded by scripts/accept_nowhere.py after environment paths are isolated.
Not part of automatic unittest discovery. No Nowhere/auth/save response mocks.
Unrelated platform counters/announcements/activity/migrations are isolated by
the fixture; all identity binding uses the real resolver.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

from tests_toy.test_nowhere import TemporaryStores, ROOT
import server
from nowhere_adapter import handler, shared, storage, web
from vendor_cmd_adapter.base import VendorCmdError

PUBLIC_USER = server._public_user


class LiveAcceptanceTests(TemporaryStores):
    def setUp(self):
        super().setUp()
        self.addCleanup(handler.POOL.close)
        self.stack.enter_context(patch.object(server, 'TOY_SECRET', secrets.token_hex(32)))
        for name in ('_duel_unread_request_reminder', '_mcp_forced_announcement'):
            self.stack.enter_context(patch.object(server, name, return_value=''))
        self.stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda response, **kw: response))
        self.tokens = {}
        self.stack.enter_context(patch.object(server, '_public_user', PUBLIC_USER))
        with server._db_connect() as db:
            db.executescript('''
CREATE TABLE toy_users(id INTEGER PRIMARY KEY,username TEXT,is_ai INTEGER,deleted_at TEXT,
 ai_token_version INTEGER DEFAULT 0,last_active_at TEXT,is_admin INTEGER DEFAULT 0,
 avatar_type TEXT,avatar_value TEXT,created_at TEXT);
CREATE TABLE user_bindings(human_user_id INTEGER,ai_user_id INTEGER,created_at TEXT);
CREATE TABLE ai_access_tokens(token_hash TEXT,user_id INTEGER,generation INTEGER,
 format_version INTEGER,revoked_at_epoch INTEGER);
INSERT INTO toy_users(id,username,is_ai) VALUES
 (101,'验收旅者甲',1),(202,'验收旅者乙',1),(1,'验收人类甲',0),(2,'验收人类乙',0),(3,'验收未绑定人类',0);
INSERT INTO user_bindings VALUES (1,101,NULL),(2,202,NULL);
''')
            server.avatar_appearances.init_schema(db)
            for uid in (101,202):
                token = server.AI_OPAQUE_TOKEN_PREFIX + secrets.token_urlsafe(32)
                self.tokens[uid] = token
                db.execute('INSERT INTO ai_access_tokens VALUES (?,?,0,?,NULL)',
                           (hashlib.sha256(token.encode()).hexdigest(),uid,server.AI_OPAQUE_TOKEN_FORMAT_VERSION))
            db.commit()
        for uid in (1,2,3):
            self.tokens[uid] = server._jwt_encode({'user_id':uid, 'is_ai':False, 'exp':int(time.time())+3600})

    def tearDown(self):
        handler.POOL.close()
        super().tearDown()

    def rpc(self, name, args, uid=101, bearer=False):
        result = server._handle_root_mcp({'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':name,'arguments':args}}, **{('bearer_token' if bearer else 'path_token'):self.tokens[uid]})
        self.assertNotIn('error',result, 'root MCP rejected test request')
        self.assertFalse(result.get('result',{}).get('isError'), 'root MCP tool returned an error')
        return result['result']['content'][0]['text']

    def play(self, uid, slot, action, **params):
        return self.rpc('play',{'game':'nowhere','action':action,'player_id':'999',
                               'params':{'slot':slot,**params}}, uid, bearer=slot==2)

    def test_live_engine_auth_private_world_and_browser(self):
        self.assertIn('nowhere',self.rpc('list_games',{}))
        self.assertIn('walk',self.rpc('get_guide',{'game':'nowhere'}))
        self.assertIn('open_door',self.play(101,1,'schema'))
        # Successful initialization calls actual upstream FastMCP.get_tools and
        # checks ALL 28 registrations/parameter names, not an AST substitute.
        for uid,slot in ((101,1),(101,2),(202,1),(202,2)):
            self.play(uid,slot,'open_door',to='北京',cotraveler='1',traveler_name='同名旅者')
            self.play(uid,slot,'mark',name=f'PRIVATE-{uid}-{slot}',note='只属于这个槽')
            self.play(uid,slot,'say',text=f'JOURNAL-{uid}-{slot}')
        self.assertFalse((storage.ROOT/'999').exists())
        with shared.transaction() as db:
            self.assertEqual(set(shared.get(db,'travelers.json')),{'101','101:2','202','202:2'})
        for player in ('101','101:2','202','202:2'):
            data = handler.play({'player_id':player,'action':'export'})['save_data']
            serial = json.dumps(data,ensure_ascii=False)
            uid,slot = player.split(':') if ':' in player else (player,'1')
            self.assertIn(f'PRIVATE-{uid}-{slot}',serial)
            for other in ('101-1','101-2','202-1','202-2'):
                if other != f'{uid}-{slot}': self.assertNotIn('PRIVATE-'+other,serial)
        player='101:2'
        before=storage.read(player)['files']['journey.json']['pos']
        self.play(101,2,'walk',direction='N',distance_km=.1)
        moved=storage.read(player)['files']['journey.json']['pos']
        self.assertNotEqual(before,moved)
        handler.POOL.close()
        self.play(101,2,'continue_journey')
        self.assertEqual(storage.read(player)['files']['journey.json']['pos'],moved)
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(lambda text:self.play(101,2,'say',text=text),('CONCURRENT-A','CONCURRENT-B')))
        quotes=storage.read(player)['files']['journey.json']['quotes']
        for text in ('CONCURRENT-A','CONCURRENT-B'):
            self.assertEqual(sum(q['text']==text for q in quotes),1)
        self.play(101,2,'send_postcard',text='PRIVATE-CARD-101-2')
        self.assertGreaterEqual(handler.save_summary(player)['postcards'],1)
        with shared.transaction() as db:
            public = json.dumps(db.execute('SELECT * FROM shared').fetchall())
        for private in ('PRIVATE-', 'JOURNAL-', '只属于这个槽'):
            self.assertNotIn(private,public)
        saved=storage.read(player)
        with self.assertRaises(VendorCmdError):
            handler.play({'player_id':player,'action':'import','save_data':saved})
        self.assertEqual(storage.read(player),saved)
        self.play(202,1,'walk_alone')
        handler.POOL.close()
        self.play(202,1,'continue_journey')
        self.assertTrue(storage.read('202')['files']['journey.json']['cotraveler_alone'])
        with shared.transaction() as db:
            self.assertNotIn('202',shared.get(db,'travelers.json'))
        self.play(202,2,'open_door',to='北京',cotraveler='0',confirm=True)
        with shared.transaction() as db:
            self.assertNotIn('202:2',shared.get(db,'travelers.json'))

        # Homepage saves use the real aggregator, while unrelated games cannot
        # consult their own production stores or satellite services.
        for name in ('_turtle_soup_stats',):
            self.stack.enter_context(patch.object(server, name, return_value={}))
        for name in ('_camping_plaza_save_summary', '_garden_cat_save_summary', '_workkk_save_summary'):
            self.stack.enter_context(patch.object(server, name, return_value=None))
        for name in server.VENDOR_GAMES:
            adapter = getattr(server, name + '_adapter', None)
            if name != 'nowhere' and adapter and hasattr(adapter, 'save_summary'):
                self.stack.enter_context(patch.object(adapter, 'save_summary', return_value=None))

        class HTTP(server.CedarToyHandler):
            def log_message(self,*args): pass  # never log test credentials
            def do_GET(self):
                path = self.path.split('?', 1)[0]
                if path.startswith('/nowhere/') or path in ('/api/auth/me', '/api/auth/saves'):
                    return super().do_GET()
                if path == '/':
                    return web.send(self, server.TAROT_WEB.homepage_index((ROOT/'index.html').read_text()), 'text/html; charset=utf-8')
                if path.startswith('/assets/'):
                    file = (ROOT/path.lstrip('/')).resolve()
                    if file.is_relative_to(ROOT/'assets') and file.is_file():
                        import mimetypes
                        return web.send(self, file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream')
                # Neutral fixtures for unrelated public counters/announcements.
                if path.startswith(('/api/', '/soup/api/')):
                    return self._send_json({})
                return self._send_json({'error':'not found'}, status=404)
            def do_POST(self): web.serve(self,server)
            def do_DELETE(self): web.serve(self,server)
        http=ThreadingHTTPServer(('127.0.0.1',0),HTTP)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{http.server_port}'
        def fetch(path,uid=1,method='GET',body=None):
            headers={'Authorization':'Bearer '+self.tokens[uid]} if uid else {}
            if body is not None: headers['Content-Type']='application/json'
            request=urllib.request.Request(origin+'/nowhere'+path,
                data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
            try:
                with urllib.request.urlopen(request,timeout=180) as response:
                    return response.status,json.loads(response.read())
            except urllib.error.HTTPError as exc:
                return exc.code,json.loads(exc.read())
        try:
            for route,method in (('/state','GET'),('/postcards','GET'),('/messages','GET'),
                ('/message','POST'),('/postcard/1/reply','POST'),('/postcard/1','DELETE'),
                ('/static/postcards/card_1.png','GET')):
                self.assertEqual(fetch(route+'?player=101:2',2,method,{} if method!='GET' else None)[0],403)
                self.assertEqual(fetch(route+'?player=101:2',3,method,{} if method!='GET' else None)[0],403)
                self.assertEqual(fetch(route+'?player=101:2',None,method,{} if method!='GET' else None)[0],401)
            self.assertEqual(fetch('/state?player=101:6')[0],403)
            self.assertEqual(fetch('/message?player=101:2',method='POST',body={'player_id':'202','content':'spoof'})[0],403)
            self.assertEqual(fetch('/message?player=101:2',method='POST',body={'content':'PRIVATE-WEB-MESSAGE'})[0],200)
            self.assertNotIn('PRIVATE-WEB-MESSAGE',json.dumps(fetch('/messages?player=101')[1]))
            status,cards=fetch('/postcards?player=101:2');self.assertEqual(status,200)
            self.assertIsInstance(cards,list);self.assertTrue(cards)
            card_id=cards[0]['id']
            self.assertEqual(fetch(f'/postcard/{card_id}?player=101:2',method='DELETE',body={})[0],400)
            self.assertEqual(fetch(f'/postcard/{card_id}/reply?player=101:2',method='POST',body={'content':'PRIVATE-REPLY'})[0],200)
            handler.POOL.close()
            self.assertIn('PRIVATE-REPLY',json.dumps(fetch('/postcards?player=101:2')[1]))
            # Live mode reuses the original browser script; no synthetic route fulfillment.
            config=self.root/'browser.json'
            config.write_text(json.dumps({'origin':origin,'player':'101:2','token':self.tokens[1]}))
            config.chmod(0o600)
            env={**os.environ,'NOWHERE_BROWSER_CONFIG':str(config)}
            result=subprocess.run(['node',str(ROOT/'scripts/check_nowhere_browser.js')],cwd=ROOT,env=env,
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=240)
            # The JS script doesn't print headers or the config file.
            print(result.stdout)
            self.assertEqual(result.returncode,0,'live browser failed; see browser log above')
            # A second bound machine belongs to this disposable human but has
            # no journey: exercise the real empty saves response as well.
            with server._db_connect() as db:
                db.execute("INSERT INTO toy_users(id,username,is_ai) VALUES (303,'验收未开门小机',1),(4,'验收未开门人类',0)")
                db.execute('INSERT INTO user_bindings VALUES (4,303,NULL)')
                db.commit()
            empty_token = server._jwt_encode({'user_id':4,'is_ai':False,'exp':int(time.time())+3600})
            config.write_text(json.dumps({'origin':origin,'token':self.tokens[1],
                                         'unboundToken':self.tokens[3],'emptyToken':empty_token}))
            result=subprocess.run(['node',str(ROOT/'scripts/check_nowhere_home_browser.js')],cwd=ROOT,env=env,
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=240)
            print(result.stdout)
            self.assertEqual(result.returncode,0,'homepage browser failed; see browser log above')
            self.assertEqual(fetch(f'/postcard/{card_id}?player=101:2',method='DELETE',body={'confirm':True})[0],200)
            handler.POOL.close()
            self.assertNotIn(card_id,[c['id'] for c in fetch('/postcards?player=101:2')[1]])
            self.assertEqual(handler.save_summary('101')['postcards'],0)
            with shared.transaction() as db:
                public=json.dumps(db.execute('SELECT * FROM shared').fetchall())
            for private in ('PRIVATE-', 'CONCURRENT-', 'JOURNAL-'):
                self.assertNotIn(private,public)
        finally:
            http.shutdown();http.server_close();thread.join(timeout=5)
            handler.POOL.close()
        for path in (self.root/'shared.db',self.root/'empty-accounts.db'):
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
