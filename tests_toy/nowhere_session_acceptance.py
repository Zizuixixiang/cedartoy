"""Run directly: isolated real engine/auth + non-loopback HTTP and TLS browser.

Uses the existing acceptance environment and account fixture. No global browser
Authorization headers, production stores, auth mocks, or TLS/config installation.
"""
import base64
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch


class SessionAcceptanceTests(unittest.TestCase):
    def test_browser_session(self):
        from tests_toy import nowhere_live_acceptance as live
        from nowhere_adapter import handler, storage, web, radio
        import server
        fixture = live.LiveAcceptanceTests('test_live_engine_auth_private_world_and_browser')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.play(101, 2, 'open_door', to='北京', cotraveler='0')
        fixture.play(101, 2, 'send_postcard', text='PRIVATE-SESSION-CARD')
        handler.POOL.close()
        # Deterministic image fixture in a real private archive; optional remote
        # poster generation is not the subject of this session/auth regression.
        with storage.locked('101:2'):
            archive = storage.read('101:2')
            card = archive['files']['postcards.json']['items'][0]
            filename = f'postcards/card_{card["id"]}.png'
            card['front_img'] = '/static/' + filename
            archive['images'][filename] = base64.b64encode(base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j0ioAAAAASUVORK5CYII=')).decode()
            storage.write('101:2', archive)
        for name in ('_camping_plaza_save_summary', '_garden_cat_save_summary', '_workkk_save_summary'):
            fixture.stack.enter_context(patch.object(server, name, return_value=None))
        fixture.stack.enter_context(patch.object(server, '_turtle_soup_stats', return_value={}))
        for name in server.VENDOR_GAMES:
            adapter = getattr(server, name + '_adapter', None)
            if name != 'nowhere' and adapter and hasattr(adapter, 'save_summary'):
                fixture.stack.enter_context(patch.object(adapter, 'save_summary', return_value=None))
        cert, key = fixture.root/'cert.pem', fixture.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-subj','/CN=nowhere.test','-addext','subjectAltName=DNS:nowhere.test',
                        '-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,timeout=20)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)

        class HTTP(server.CedarToyHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                path = self.path.split('?',1)[0]
                if path == '/test-radio.mp3':
                    # Valid silent MP3 fixture, served over the real local TLS connection.
                    return web.send(self, base64.b64decode('/+MYxAAAAANIAAAAAExBTUUzLjEwMFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV/+MYxDsAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV/+MYxHYAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV'), 'audio/mpeg')
                if path.startswith('/nowhere/') or path in ('/api/auth/me','/api/nowhere/saves'):
                    return super().do_GET()
                if path == '/':
                    return web.send(self,server.TAROT_WEB.homepage_index((live.ROOT/'index.html').read_text()),'text/html; charset=utf-8')
                if path.startswith('/assets/'):
                    file = (live.ROOT/path.lstrip('/')).resolve()
                    if file.is_relative_to(live.ROOT/'assets') and file.is_file():
                        import mimetypes
                        return web.send(self,file.read_bytes(),mimetypes.guess_type(file)[0] or 'application/octet-stream')
                if path.startswith(('/api/','/soup/api/')): return self._send_json({})
                return self._send_json({'error':'not found'},status=404)
            def do_POST(self): web.serve(self,server)
            def do_DELETE(self): web.serve(self,server)

        class DualServer(live.ThreadingHTTPServer):
            # Both schemes on a random test port so normal protocol upgrade can
            # retain a custom port. TLS is real; the host name is not loopback.
            daemon_threads = True
            def finish_request(self, request, address):
                request.settimeout(15)
                try:
                    if request.recv(1,socket.MSG_PEEK) == b'\x16':
                        request = context.wrap_socket(request,server_side=True)
                    self.RequestHandlerClass(request,address,self)
                except (BrokenPipeError,ConnectionResetError,TimeoutError,ssl.SSLError): pass
                finally: request.close()

        http = DualServer(('127.0.0.1',0),HTTP)
        # Persistent radio fixtures survive upstream's four-second history
        # refresh, and exercise the real private archive/history response.
        with storage.locked('101:2'):
            archive = storage.read('101:2')
            items = archive['files'].setdefault('footprints.json', {'items': []})['items']
            for i, url in enumerate(('javascript:alert(1)', 'data:text/html,bad', 'file:///tmp/bad', 'ftp://example.test/bad')):
                items.append({'action': 'listen', 'text': f'TEST-UNSAFE-{i}', 'stream_url': url})
            for name, url in (('RADIO-HTTP', 'http://example.test/radio.mp3'),
                              ('RADIO-HTTPS', f'https://nowhere.test:{http.server_port}/test-radio.mp3')):
                items.append({'action': 'listen', 'text': name, 'station': {'name': name}, 'stream_url': url})
            storage.write('101:2', archive)
        original_addresses=radio.public_addresses
        def station_addresses(host,port):
            if host=='nowhere.test' and port==http.server_port:
                return socket.getaddrinfo('127.0.0.1',port,type=socket.SOCK_STREAM)
            return original_addresses(host,port)
        fixture.stack.enter_context(patch.object(radio,'public_addresses',side_effect=station_addresses))
        trust=ssl.create_default_context(cafile=str(cert))
        fixture.stack.enter_context(patch.object(radio.ssl,'create_default_context',return_value=trust))
        thread = threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            config = fixture.root/'browser.json'
            config.write_text(json.dumps({'origin':f'https://nowhere.test:{http.server_port}',
                'httpOrigin':f'http://nowhere.test:{http.server_port}','token':fixture.tokens[1],
                'otherToken':fixture.tokens[2], 'cardId':card['id'], 'image':filename,
                'expiredToken':server._jwt_encode({'user_id':1,'is_ai':False,'exp':1})}))
            config.chmod(0o600)
            result = subprocess.run(['node',str(live.ROOT/'scripts/check_nowhere_session_browser.js')],
                env={**os.environ,'NOWHERE_BROWSER_CONFIG':str(config)},cwd=live.ROOT,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
            print(result.stdout)
            self.assertEqual(result.returncode,0,'non-loopback browser acceptance failed')
        finally:
            http.shutdown();http.server_close();thread.join(timeout=5)


if __name__ == '__main__':
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from scripts.accept_nowhere import isolated_env, preflight
    # Reuse existing isolation; never import server before this point.
    with tempfile.TemporaryDirectory(prefix='nowhere-session-') as directory:
        env = isolated_env(Path(directory))
        env['NOWHERE_SCREENSHOTS'] = os.environ.get('NOWHERE_SCREENSHOTS','/tmp/nowhere-session-evidence')
        if os.environ.get('NOWHERE_SESSION_BASELINE'): env['NOWHERE_SESSION_BASELINE']='1'
        os.environ.clear();os.environ.update(env)
        preflight(env)
        unittest.main()
