#!/usr/bin/env python3
"""Disposable UI transport fixture: stdio by default, optional loopback HTTP.

Executes real auth, Deeptalk HTTP handlers, SQLite and root MCP. The only stubs
are unrelated game adapters/history. Never connects to production databases.
"""
import io
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests_toy.test_deeptalk import DeeptalkTests, HUMAN
from deeptalk import catalog
import server


def main():
    fixture=DeeptalkTests('test_web_auth_readonly_get_and_cross_account')
    fixture.setUp()
    try:
        # Exercise the shipped content; account/session fixtures remain disposable.
        catalog.DATA_DIR = ROOT / 'deeptalk/data'
        def summaries(raw_token):
            user=server._current_account(raw_token)
            return {'user':user,'self':{'user':user,'username':user['username'],'saves':{}},'machines':[]}
        class Handler(server.CedarToyHandler):
            def do_GET(self):
                if self.path=='/__test__/tokens':
                    self._send_json(fixture.tokens); return
                if self.path=='/':
                    self._send_html_file(ROOT/'index.html'); return
                super().do_GET()
            def log_message(self,*args): pass
        with patch.object(server,'_account_web_saves',summaries), patch.object(server,'_duel_history_for_user',return_value={}):
            if '--http' in sys.argv:
                with ThreadingHTTPServer(('127.0.0.1',0),Handler) as httpd:
                    print(f'http://127.0.0.1:{httpd.server_port}',flush=True)
                    httpd.serve_forever()
            else:
                print(json.dumps({'tokens':fixture.tokens}),flush=True)
                for line in sys.stdin:
                    req=json.loads(line)
                    if req.get('kind')=='legacy_create':
                        # Stdio-only fixture, never exposed through a Web/MCP route.
                        result=fixture.store.create(HUMAN, None, req['topic_id'], req['request_id'],
                                                    sharing_mode='bound_shared', mode='blind')
                    elif req.get('kind')=='mcp':
                        result=server._handle_root_mcp(req['payload'],bearer_token=req['token'])
                    else:
                        handler=object.__new__(Handler)
                        handler.path=req['url']; handler.headers=req.get('headers',{})
                        raw=req.get('body','').encode()
                        handler.headers['Content-Length']=str(len(raw))
                        handler.rfile=io.BytesIO(raw); handler.wfile=io.BytesIO()
                        result={'status':200,'headers':{}}
                        handler.send_response=lambda status,*_:result.update(status=status)
                        handler.send_header=lambda k,v:result['headers'].update({k:v})
                        handler.end_headers=lambda:None
                        if urlsplit(req['url']).path=='/api/auth/history':
                            handler._handle_api_auth_history()
                        else:
                            handler._handle_deeptalk(req.get('method','GET'),urlsplit(req['url']).path,parse_qs(urlsplit(req['url']).query))
                        result['body']=json.loads(handler.wfile.getvalue())
                    print(json.dumps(result,ensure_ascii=False),flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        fixture.doCleanups()


if __name__=='__main__': main()
