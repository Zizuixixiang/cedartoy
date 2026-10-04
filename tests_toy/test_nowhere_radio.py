"""Short proxy/picker regressions; all account and save data are temporary."""
from contextlib import contextmanager
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from tests_toy.test_nowhere import TemporaryStores, archive, server, storage, handler
from nowhere_adapter import radio

STREAM = 'http://radio.example.test/live'


def historical_archive(stream='HTTPS://ICECAST.RADIOFRANCE.FR:443/fip-midfi.mp3?tag=%7e&note=\"fip\"#old'):
    """Real upstream layout: slot-wide footprints survive active journey changes."""
    data=archive('Tokyo')
    current=data['files']['journey.json'];current['journey_slug']='tokyo'
    old=copy.deepcopy(current);old.update(place_name='Paris',journey_slug='paris')
    data['files'].update({
        'journeys/index.json':{'active':'tokyo','journeys':[{'slug':'paris'},{'slug':'tokyo'}]},
        'journeys/paris.json':old,'journeys/tokyo.json':copy.deepcopy(current),
        'footprints.json':{'items':[
            {'action':'listen','place':'Paris','station':{'name':'FIP'},'stream_url':stream,'at':'2026-01-01T00:00:00Z'},
            {'action':'land','place':'Tokyo','at':'2026-01-02T00:00:00Z'},
        ]},
    })
    return data


def upstream_footprints(saved):
    # Execute the actual upstream history builder with only a disposable home.
    vendor=Path(__file__).resolve().parents[1]/'vendor/nowhere'
    spec=importlib.util.spec_from_file_location('test_radio_placememory',vendor/'nowhere/placememory.py')
    module=importlib.util.module_from_spec(spec)
    with patch.object(sys,'path',[str(vendor),*sys.path]):spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix='radio-history-') as directory:
        root=Path(directory)
        for name in ('footprints.json','postcards.json','landings.json'):
            if name in saved['files']:(root/name).write_text(json.dumps(saved['files'][name]))
        with patch.object(module,'_get_home',return_value=root):return module.journey_footprints()


@contextmanager
def fake_station(playable=False):
    """Finite test lifetime, but no Content-Length: genuine slow chunked source."""
    stopped = threading.Event()
    hits = []
    chunks=(b'first',b'second')
    if playable:
        import io, wave
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000)
            wav.writeframes(b'\x00\x00'*8000)
        content=buffer.getvalue()
        chunks=(content[:8000],content[8000:])
    class HTTP(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args): pass
        def do_GET(self):
            hits.append((self.path, dict(self.headers)))
            if self.path.startswith(('/redirect', '/loop')):
                self.send_response(302)
                self.send_header('Location', ('/loop' if self.path == '/loop' else self.path.partition('?')[2] or '/live'))
                self.send_header('Content-Length','0');self.end_headers();return
            self.send_response(200)
            self.send_header('Content-Type','text/html' if self.path=='/bad' else ('audio/wav' if playable else 'audio/mpeg'))
            self.send_header('Set-Cookie','evil=1')
            self.send_header('icy-name','Test radio')
            self.send_header('Transfer-Encoding','chunked');self.end_headers()
            try:
                for data in chunks:
                    self.wfile.write(b'%x\r\n'%len(data)+data+b'\r\n'); self.wfile.flush()
                    if stopped.wait(.15): break
                # Leave the stream silent so disconnect must interrupt read1.
                if not playable:stopped.wait(8)
                self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
            except OSError: pass
    http = ThreadingHTTPServer(('127.0.0.1',0),HTTP)
    worker = threading.Thread(target=http.serve_forever,daemon=True);worker.start()
    try: yield http, hits
    finally:
        stopped.set();http.shutdown();http.server_close();worker.join(2)


class ProxyTests(TemporaryStores):
    def setUp(self):
        super().setUp()
        data=archive();data['files']['footprints.json']={'items':[{'stream_url':STREAM}]}
        self.put('101',data)

    def test_membership_is_private_and_exact(self):
        radio.authorize('101', STREAM)
        self.put('101:2');self.put('202')
        for player,url in [('101:2',STREAM),('202',STREAM),('101',STREAM+'?other=1')]:
            with self.assertRaises(radio.RadioError) as err:radio.authorize(player,url)
            self.assertEqual(err.exception.status,403)
        for url in ['javascript:alert(1)','data:audio/mpeg,x','file:///tmp/x','ftp://radio/x','//radio/x','http://user@radio/x','http://radio:0/x']:
            with self.assertRaises(ValueError):radio.authorize('101',url)

    def test_historical_footprints_are_visible_and_authorized_across_journeys(self):
        saved=historical_archive();self.put('101',saved)
        history=upstream_footprints(saved)
        stream=next(f['stream_url'] for f in history if f.get('station',{}).get('name')=='FIP')
        self.assertEqual(saved['files']['journeys/index.json']['active'],'tokyo')
        self.assertEqual(radio.authorize('101',radio.canonical_url(stream)),radio.canonical_url(stream))
        for player in ('101:2','202'):
            self.put(player)
            with self.assertRaises(radio.RadioError):radio.authorize(player,stream)

    def test_indexed_historical_station_but_not_unrelated_fields(self):
        saved=historical_archive()
        saved['files']['footprints.json']={'items':[]}
        fip='https://icecast.radiofrance.fr/fip-midfi.mp3'
        saved['files']['journeys/paris.json']['radio_station']={'name':'FIP','stream_url':fip}
        saved['files']['journeys/paris.json']['last_env']={'radio':{'name':'FIP','stream_url':fip}}
        # Unrelated text, arbitrary nested fields and unindexed files grant nothing.
        extra='https://unrelated.test/audio'
        saved['files']['notebook.json']={'stream_url':extra}
        saved['files']['journeys/orphan.json']={**saved['files']['journey.json'],'radio_station':{'stream_url':extra}}
        self.put('101',saved)
        self.assertEqual(radio.authorize('101',fip+'#browser'),fip)
        with self.assertRaises(radio.RadioError):radio.authorize('101',extra)
        for player in ('101:2','202'):
            self.put(player)
            with self.assertRaises(radio.RadioError):radio.authorize(player,fip)

    def test_url_equivalence_does_not_merge_distinct_targets(self):
        groups=[
            ('HTTPS://EXAMPLE.TEST:443/a/../%66ip?q=%7e&name="FIP"#old','https://example.test/fip?q=~&name=%22FIP%22#new'),
            ('http://EXAMPLE.TEST:80','http://example.test/'),
            ('https://example.test/a?q=%2f','https://example.test/a?q=%2F'),
            ('https://example.test/巴黎','https://example.test/%E5%B7%B4%E9%BB%8E'),
        ]
        for first,second in groups:self.assertEqual(radio.canonical_url(first),radio.canonical_url(second))
        pairs=[('/a%2fb','/a/b'),('/a?x=a%26y=b','/a?x=a&y=b'),('/a?x=1&x=2','/a?x=2&x=1'),
               ('/a?x=a+b','/a?x=a%20b'),('/a?x=1','/a?x=2'),('/a?x=1&y=2','/a?y=2&x=1'),('/A','/a')]
        for first,second in pairs:self.assertNotEqual(radio.canonical_url('https://example.test'+first),radio.canonical_url('https://example.test'+second))
        for url in ('https://example.test/a%zz','https://example.test/a%','https://user@example.test/a','javascript:alert(1)','https://straße.test/a'):
            with self.assertRaises(ValueError):radio.canonical_url(url)
        saved=archive();saved['files']['footprints.json']={'items':[{'stream_url':'https://bad/%zz'},{}]}
        self.put('101',saved)
        with self.assertRaises(radio.RadioError):radio.authorize('101','https://example.test/a')

    def test_dns_blocks_all_nonpublic_and_mixed_answers(self):
        def answer(ip):return (socket.AF_INET6 if ':' in ip else socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,80))
        for ip in ['127.0.0.1','10.0.0.1','172.16.1.1','192.168.1.1','169.254.169.254','224.0.0.1','240.0.0.1','0.0.0.0','100.64.0.1','::1','fc00::1','fe80::1','ff02::1','::ffff:127.0.0.1','fec0::1','2002:7f00:1::1','192.0.0.11','192.88.99.1']:
            with self.subTest(ip=ip),patch.object(socket,'getaddrinfo',return_value=[answer('8.8.8.8'),answer(ip)]):
                with self.assertRaises(radio.RadioError):radio.public_addresses('looks-public.test',80)
        with self.assertRaises(radio.RadioError):radio.public_addresses('localhost.',80)
        with patch.object(socket,'getaddrinfo',return_value=[answer('8.8.8.8')]):
            self.assertEqual(radio.public_addresses('public.test',80)[0][-1][0],'8.8.8.8')

    def test_connection_pins_validated_numeric_address(self):
        with patch.object(radio,'public_addresses',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',80))]) as dns,patch.object(socket,'socket') as sock:
            conn=radio.PinnedConnection(urlsplit(STREAM));conn.connect()
            sock.return_value.connect.assert_called_once_with(('8.8.8.8',80))
            dns.assert_called_once_with('radio.example.test',80)
            conn.close()

    def test_redirects_revalidate_and_are_bounded_and_mime_checked(self):
        real=radio.public_addresses
        with fake_station() as (station,hits):
            def addresses(host,port):
                if host=='radio.example.test':return socket.getaddrinfo('127.0.0.1',station.server_port,type=socket.SOCK_STREAM)
                return real(host,port)
            with patch.object(radio,'public_addresses',side_effect=addresses):
                conn,response,mime=radio.open_stream('http://radio.example.test/redirect?/live')
                self.assertEqual(response.read1(5),b'first');response.close();conn.close()
                for target in ['http://127.0.0.1/private','http://169.254.169.254/latest','file:///etc/passwd']:
                    with self.subTest(target=target),self.assertRaises((radio.RadioError,ValueError)):
                        radio.open_stream('http://radio.example.test/redirect?'+target)
                before=len(hits)
                with self.assertRaises(radio.RadioError):radio.open_stream('http://radio.example.test/loop')
                self.assertEqual(len(hits)-before,radio.MAX_REDIRECTS+1)
                with patch.object(radio,'MAX_REDIRECTS',0),self.assertRaises(radio.RadioError):
                    radio.open_stream('http://radio.example.test/redirect?/live')
                with self.assertRaises(radio.RadioError):radio.open_stream('http://radio.example.test/bad')

    def test_incremental_stream_disconnect_and_capacity_release(self):
        done=threading.Event()
        with fake_station() as (station,hits):
            def addresses(host,port):
                self.assertEqual(host,'radio.example.test')
                return socket.getaddrinfo('127.0.0.1',station.server_port,type=socket.SOCK_STREAM)
            class Proxy(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    try:radio.stream(self,'101',STREAM)
                    finally:done.set()
            with patch.object(radio,'public_addresses',side_effect=addresses),patch.object(radio,'SLOTS',threading.BoundedSemaphore(1)):
                proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
                thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
                try:
                    client=http.client.HTTPConnection('127.0.0.1',proxy.server_port,timeout=3)
                    start=time.monotonic();client.request('GET','/');response=client.getresponse()
                    self.assertEqual(response.read(5),b'first')
                    self.assertLess(time.monotonic()-start,1,'must deliver before source finishes')
                    self.assertEqual(response.read(6),b'second')
                    self.assertNotIn('Set-Cookie',dict(response.headers));self.assertNotIn('Location',dict(response.headers))
                    self.assertEqual(response.getheader('icy-name'),'Test radio')
                    with self.assertRaises(radio.RadioError) as err:radio.stream(None,'101',STREAM)
                    self.assertEqual(err.exception.status,503)
                    response.close();client.close()
                    self.assertTrue(done.wait(2),'silent upstream must stop on client disconnect')
                    self.assertTrue(radio.SLOTS.acquire(False));radio.SLOTS.release()
                    self.assertNotIn('Cookie',hits[0][1]);self.assertNotIn('Authorization',hits[0][1])
                finally:proxy.shutdown();proxy.server_close();thread.join(2)

    def test_upstream_failure_releases_capacity(self):
        with patch.object(radio,'SLOTS',threading.BoundedSemaphore(1)),patch.object(radio,'open_stream',side_effect=OSError):
            with self.assertRaises(radio.RadioError):radio.stream(None,'101',STREAM)
            self.assertTrue(radio.SLOTS.acquire(False));radio.SLOTS.release()


class PickerTests(TemporaryStores):
    def setUp(self):
        super().setUp()
        with server._db_connect() as db:
            db.executescript("""CREATE TABLE toy_users(id INTEGER,username TEXT,is_ai INTEGER,deleted_at TEXT);
                CREATE TABLE user_bindings(human_user_id INTEGER,ai_user_id INTEGER);
                INSERT INTO toy_users VALUES(101,'one',1,NULL),(202,'two',1,NULL);
                INSERT INTO user_bindings VALUES(1,101);""")
        def account(token):
            if token != 'test-human':raise server._McpError(-32001,'需要登录')
            return {'id':1,'is_ai':False}
        self.stack.enter_context(patch.object(server,'_current_account',side_effect=account))
    def call_picker(self, token='test-human'):
        h=object.__new__(server.CedarToyHandler);h.headers={'Authorization':'Bearer '+token}
        result={}
        h._send_json=lambda body,status=200,**kwargs:result.update(body=body,status=status)
        h._handle_api_nowhere_saves()
        return result

    def test_only_bound_nowhere_files_no_other_games_or_worker(self):
        with server._db_connect() as db:
            db.execute("INSERT INTO user_bindings VALUES(1,202)");db.commit()
        self.put('101');self.put('101:5');self.put('202:2');self.put('999')
        for value in vars(server).values():
            if value is not handler and callable(getattr(value,'save_summary',None)):
                self.stack.enter_context(patch.object(value,'save_summary',side_effect=AssertionError('other game summary')))
        with patch.object(server,'_account_saves_for_user',side_effect=AssertionError('full scan')),patch.object(server,'_auto_migrate_legacy_account_saves',side_effect=AssertionError('migration')),patch.object(handler.POOL,'acquire',side_effect=AssertionError('worker')),patch.object(handler,'save_summary',wraps=handler.save_summary) as summaries,patch.object(storage,'read',wraps=storage.read) as reads:
            start=time.monotonic();result=self.call_picker();elapsed=time.monotonic()-start
            self.assertEqual(result['status'],200)
            self.assertEqual(sorted(c.args[0] for c in summaries.call_args_list),sorted(['101','202']+[f'{p}:{s}' for p in ('101','202') for s in range(2,6)]))
            self.assertEqual(sorted(c.args[0] for c in reads.call_args_list),['101','101:5','202:2'])
            self.assertEqual({m['user']['id']:[s['slot'] for s in m['slots']] for m in result['body']['machines']},{101:[1,5],202:[2]})
            self.assertLess(elapsed,1)
            print(f'Nowhere picker: {elapsed*1000:.1f}ms, {summaries.call_count} Nowhere checks, {reads.call_count} save.json reads')

    def test_empty_unbound_invalid_and_ai_login(self):
        self.assertEqual(self.call_picker()['body']['machines'][0]['slots'],[])
        with server._db_connect() as db:db.execute('DELETE FROM user_bindings');db.commit()
        self.assertEqual(self.call_picker()['body'],{'machines':[]})
        self.assertEqual(self.call_picker('')['status'],401)
        self.assertEqual(self.call_picker('expired')['status'],401)
        with patch.object(server,'_current_account',return_value={'id':101,'is_ai':True}):
            self.assertEqual(self.call_picker()['status'],401)
