"""Disposable browser fixture: real HTTP handlers/camping engine, no remote writes."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix='game-page-slots-') as temp, ExitStack() as stack:
        directory = Path(temp)
        for key, filename in [('TURTLE_SOUP_DB', 'accounts.db'), ('SESSIONS_DB', 'sessions.db'), ('CAMPING_PLAZA_DB_PATH', 'camping.db')]:
            os.environ[key] = str(directory / filename)
        os.environ['TOY_SECRET'] = 'isolated-slot-browser-secret'
        import server
        import camping_plaza_adapter as camping
        from fastapi.testclient import TestClient
        client = stack.enter_context(TestClient(camping.app))
        with sqlite3.connect(server.TURTLE_DB_PATH) as db:
            db.executescript('''CREATE TABLE toy_users(id INTEGER, username TEXT, is_ai INTEGER, deleted_at TEXT);
                CREATE TABLE user_bindings(human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users VALUES(101,'A',1,NULL),(202,'B',1,NULL),(303,'unbound',1,NULL);
                INSERT INTO user_bindings VALUES(1,101),(1,202);''')
        def account(token):
            if token != 'fixture': raise server._McpError(-32001, 'expired')
            return {'id': 1, 'username': 'fixture human', 'is_ai': False}
        stack.enter_context(patch.object(server, '_current_account', side_effect=account))
        stack.enter_context(patch.object(server, '_record_web_game_activity'))
        stack.enter_context(patch.object(server, '_game_maintenance', return_value=None))
        stack.enter_context(patch.object(server, '_check_request_rate_limit', return_value=True))
        # Fail closed if anything other than the local camping service is contacted.
        class Connection:
            def __init__(self, host, port, **_):
                assert (host, port) == (server.CAMPING_PLAZA_HOST, server.CAMPING_PLAZA_PORT)
            def request(self, method, target, body=None, headers=None):
                self.response = client.request(method, target, content=body, headers=headers)
            def getresponse(self): return self
            @property
            def status(self): return self.response.status_code
            @property
            def reason(self): return self.response.reason_phrase
            def read(self): return self.response.content
            def getheaders(self): return list(self.response.headers.items())
            def close(self): pass
        stack.enter_context(patch.object(server.http.client, 'HTTPConnection', Connection))
        fixtures = ROOT / 'tests_toy/fixtures'
        public_assets = Path(os.environ['DETROIT_BROWSER_ASSETS']) if os.environ.get('DETROIT_BROWSER_ASSETS') else None
        script = (fixtures / 'detroit_host_v17.js').read_text()
        # The existing upstream-shaped fixture has an inert api(). Give it real
        # fetches and small test controls; production rewrite/bootstrap stays real.
        script = script.replace('return {path, options};', "const r = await fetch(path, {...options, headers: {'Content-Type': 'application/json'}}); if (!r.ok) throw Error('HTTP '+r.status); return r.json();")
        script = script.replace('  return sessions;', '''  const button = document.createElement('button'); button.id='continue'; button.textContent='继续';
  button.onclick=()=>loadSession(sessions[0].id); app.append(button);
  return sessions;''')
        script = script.replace('  return data;', '''  const status=document.createElement('p'); status.id='marker'; status.textContent=data.marker||'empty'; app.append(status);
  const save=document.createElement('button'); save.id='save'; save.textContent='操作';
  save.onclick=async()=>render(await api('/api/action', {method:'POST',body:JSON.stringify({id:'cedartoy-slot',marker:document.querySelector('#value').value})}));
  const value=document.createElement('input'); value.id='value'; app.append(value,save);
  const link=document.createElement('a'); link.id='internal'; link.href='/detroit/'; link.textContent='重新打开'; app.append(link);
  const exportLink=document.createElement('a'); exportLink.id='export'; exportLink.href='/api/export?id=cedartoy-slot'; exportLink.textContent='导出'; app.append(exportLink);
  return data;''')
        def public(name):
            if public_assets is not None and name in {'host', 'host.js', 'host.css'}:
                mime = {'host': 'text/html', 'host.js': 'text/javascript', 'host.css': 'text/css'}[name]
                return 200, mime, (public_assets / ('remote-' + name)).read_bytes()
            if name == 'host': return 200, 'text/html', (fixtures / 'detroit_host_v17.html').read_bytes()
            if name == 'host.js': return 200, 'text/javascript', script.encode()
            if name == 'host.css': return 200, 'text/css', b'body {font:16px sans-serif} button {min-height:44px}'
            raise AssertionError(name)
        stack.enter_context(patch.object(server.detroit_adapter, 'fetch_public', side_effect=public))
        saves = {}
        def detroit_api(player, method, endpoint, *, query, payload):
            if public_assets is not None:
                revision = saves.get(player, 0)
                if method == 'POST':
                    assert payload['action'] == 'continue'
                    revision += 1
                    saves[player] = revision
                session = {'id': 'cedartoy-slot', 'name': 'Fixture ' + player, 'locale': 'zh-Hans',
                           'revision': revision, 'difficulty': 'casual', 'chapter_count': 2, 'chapter_index': 0}
                if endpoint == 'sessions':
                    result = {'sessions': [session], 'ending_progress': {'discovered': 0, 'total': 2, 'percent': 0}}
                elif endpoint in {'export', 'backup'}:
                    result = {'player': player, 'revision': revision}
                    return 200, {'content-type': 'application/json', 'content-disposition': 'attachment; filename="fixture.json"'}, json.dumps(result).encode()
                else:
                    result = {'screen': 'scene', 'session': session, 'chapter': {'number': 1, 'title': 'Fixture', 'protagonist': 'connor'},
                              'choices': [], 'context': f'Fixture {player} step {revision}', 'node_id': 'node-'+str(revision),
                              'decision_count': revision, 'copy_text': 'Fixture'}
                return 200, {'content-type': 'application/json'}, json.dumps(result).encode()
            if endpoint == 'sessions': result = {'sessions': [{'id': 'cedartoy-slot'}]}
            elif method == 'POST':
                saves[player] = payload['marker']; result = {'marker': saves[player]}
            else: result = {'marker': saves.get(player, 'empty')}
            return 200, {'content-type': 'application/json'}, json.dumps(result).encode()
        stack.enter_context(patch.object(server.detroit_adapter, 'browser_api', side_effect=detroit_api))
        class Handler(server.CedarToyHandler):
            def log_message(self, *_): pass
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        print(json.dumps({'base': f'http://127.0.0.1:{httpd.server_port}'}), flush=True)
        try: sys.stdin.readline()
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=3)


if __name__ == '__main__': main()
