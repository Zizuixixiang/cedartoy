"""Focused radio navigation: real temporary auth/cookie, no engine startup."""
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from contextlib import contextmanager


class RadioBrowserTests(unittest.TestCase):
    def test_cookie_bound_internal_radio_and_desktop_popup(self):
        from http.server import ThreadingHTTPServer
        from tests_toy.nowhere_live_acceptance import LiveAcceptanceTests, ROOT
        from nowhere_adapter import handler, storage, web, radio
        from tests_toy.test_nowhere_radio import fake_station, historical_archive, upstream_footprints
        import socket
        import server
        fixture = LiveAcceptanceTests('test_live_engine_auth_private_world_and_browser')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        saved=historical_archive('HTTPS://RADIO.EXAMPLE.TEST:443/fip.mp3?x="fip"&y=2#stored')
        saved['files']['footprints.json']['items'][0]['station']['name']='RADIO-HTTPS'
        saved['files']['footprints.json']['items'].extend([
            *({'text': f'TEST-UNSAFE-{i}', 'stream_url': url} for i, url in enumerate(
                ('javascript:alert(1)', 'data:text/html,bad', 'file:///tmp/bad', 'ftp://bad/stream'))),
            *({'text': f'TEST-NO-STREAM-{i}', **fields} for i,fields in enumerate(
                ({}, {'stream_url':''}, {'stream_url':'/nowhere/?player=101:2'}))),
        ])
        fixture.put('101:2',saved)
        from tests_toy.test_nowhere_history import journey_archive
        current = json.loads(Path(os.environ['NOWHERE_PARIS_COPY']).read_text()) if os.environ.get('NOWHERE_PARIS_COPY') else journey_archive()
        if not os.environ.get('NOWHERE_PARIS_COPY'):
            current['files']['journey.json']['last_env']={'radio':{'name':'FIP','stream_url':'https://icecast.radiofrance.fr/fip-midfi.mp3'}}
        fixture.put('101',current)
        self.assertEqual(radio.authorize('101',current['files']['journey.json']['last_env']['radio']['stream_url']),
                         'https://icecast.radiofrance.fr/fip-midfi.mp3')
        def engine_payload(payload):
            request = payload['request']
            # Only engine rendering is synthetic. Account, binding, document,
            # cookie and private route authorization are the actual handlers.
            saved = payload['archive']
            if request['path'] == '/state':
                body = {**saved['files']['journey.json'], 'last_text': '临时测试旅程', 'env': {}, 'radio':saved['files']['journey.json'].get('last_env',{}).get('radio')}
            elif request['path'] == '/history':
                body = {'landings':[],'path':[],'footprints':upstream_footprints(saved)}
            else:
                body = []
            return {'result': {'status': 200, 'body': body}}
        @contextmanager
        def worker_for(player):
            yield Mock(call=Mock(side_effect=engine_payload))
        fixture.stack.enter_context(patch.object(handler.POOL, 'acquire', side_effect=worker_for))
        fixture.stack.enter_context(patch.object(server,'_account_web_saves',side_effect=AssertionError('picker must not scan all games')))
        cert, key = fixture.root/'cert.pem', fixture.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-subj','/CN=nowhere.test','-addext','subjectAltName=DNS:nowhere.test,DNS:radio.example.test,DNS:icecast.radiofrance.fr',
                        '-keyout',str(key),'-out',str(cert)],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key)
        station,hits=fixture.stack.enter_context(fake_station(playable=True))
        station.socket=tls.wrap_socket(station.socket,server_side=True)
        # Only the fake station's dial address/trust are overridden. Browser
        # cookies, route checks, membership and actual byte streaming are real.
        public_addresses=radio.public_addresses
        def addresses(host,port):
            if host in {'radio.example.test','icecast.radiofrance.fr'}:
                return socket.getaddrinfo('127.0.0.1',station.server_port,type=socket.SOCK_STREAM)
            return public_addresses(host,port)
        fixture.stack.enter_context(patch.object(radio,'public_addresses',side_effect=addresses))
        trust=ssl.create_default_context(cafile=str(cert))
        fixture.stack.enter_context(patch.object(radio.ssl,'create_default_context',return_value=trust))
        class HTTP(server.CedarToyHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                path=self.path.split('?',1)[0]
                if path.startswith('/nowhere/'):return web.serve(self,server)
                if path in ('/api/auth/me','/api/nowhere/saves'):return super().do_GET()
                if path=='/':
                    return web.send(self,server.TAROT_WEB.homepage_index((ROOT/'index.html').read_text()),'text/html; charset=utf-8')
                if path.startswith('/assets/'):
                    file=(ROOT/path.lstrip('/')).resolve()
                    if file.is_relative_to(ROOT/'assets') and file.is_file():
                        import mimetypes
                        return web.send(self,file.read_bytes(),mimetypes.guess_type(file)[0] or 'application/octet-stream')
                return self._send_json({})
        http = ThreadingHTTPServer(('127.0.0.1',0),HTTP)
        http.socket = tls.wrap_socket(http.socket,server_side=True)
        thread = threading.Thread(target=http.serve_forever,daemon=True)
        thread.start()
        try:
            config = fixture.root/'browser.json'
            config.write_text(json.dumps({'origin':f'https://nowhere.test:{http.server_port}', 'token':fixture.tokens[1]}))
            config.chmod(0o600)
            result = subprocess.run(['node',str(ROOT/'scripts/check_nowhere_radio_click.js')],
                env={**os.environ,'NOWHERE_BROWSER_CONFIG':str(config)},cwd=ROOT,
                stdout=None,stderr=subprocess.STDOUT,text=True,timeout=120)
            self.assertEqual(result.returncode,0)
            self.assertGreaterEqual(len(hits),4,"listen and enriched trail links in both mobile contexts must reach fake upstream")
            self.assertEqual(storage.read('101'),current,'read-only browser flow must preserve the copied archive')
        finally:
            http.shutdown();http.server_close();thread.join(timeout=5)


if __name__ == '__main__':
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from scripts.accept_nowhere import isolated_env
    with tempfile.TemporaryDirectory(prefix='nowhere-radio-') as directory:
        env = isolated_env(Path(directory))
        if os.environ.get('NOWHERE_PARIS_COPY'):
            env['NOWHERE_PARIS_COPY']=os.environ['NOWHERE_PARIS_COPY']
        if os.environ.get('NOWHERE_SCREENSHOTS'):
            env['NOWHERE_SCREENSHOTS'] = os.environ['NOWHERE_SCREENSHOTS']
        os.environ.clear();os.environ.update(env)
        unittest.main()
