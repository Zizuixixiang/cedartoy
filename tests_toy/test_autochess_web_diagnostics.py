import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import server
from autochess_adapter import web, web_diagnostics as diag
from cedar_backend.errors import _McpError


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.activation = root / 'activation.json'
        self.log = root / 'diagnostics.jsonl'
        self.now = int(time.time())
        self.activation.write_text(json.dumps({'account_id':12635, 'enabled_at':self.now-1, 'expires_at':self.now+100}))
        for name, value in [('ACTIVATION',self.activation), ('LOG',self.log), ('_BUDGET',{})]:
            p = patch.object(diag,name,value);p.start();self.addCleanup(p.stop)

    def rows(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_target_expiry_privacy_rotation_and_fail_open(self):
        self.assertIsNone(diag.begin('12','/autochess/api/bootstrap'))
        self.assertIsNone(diag.begin('12635','/autochess/api/bootstrap?token=SECRET'))
        trace=diag.begin('12635','/autochess/api/bootstrap')
        trace.emit('response',400,'TypeError')
        rows=self.rows();self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['request_id'],rows[1]['request_id'])
        self.assertEqual(rows[1]['error_type'],'TypeError')
        self.assertEqual(self.log.stat().st_mode & 0o777,0o600)
        self.assertEqual(set(rows[0]),{'request_id','time_epoch','account_id','route','stage','elapsed_ms','http_status','error_type'})
        with patch.object(diag,'MAX_BYTES',1):trace.emit('response',200,'SECRET')
        self.assertTrue(Path(str(self.log)+'.1').exists())
        self.assertNotIn('SECRET',self.log.read_text())
        with patch.object(diag.time,'time',return_value=self.now+200):
            trace.emit('response',200);self.assertIsNone(diag.begin('12635','/autochess/api/bootstrap'))
        self.assertEqual(len(self.rows()),1)
        with patch.object(diag,'LOG',Path(self.temp.name)/'absent'/'log'):
            diag.begin('12635','/autochess/api/bootstrap').emit('response',200)
        self.activation.write_text('{}');self.assertIsNone(diag.begin('12635','/autochess/api/bootstrap'))

    def test_rate_budget(self):
        for _ in range(120):self.assertIsNotNone(diag.begin('12635','/autochess/api/bootstrap'))
        self.assertIsNone(diag.begin('12635','/autochess/api/bootstrap'))

    def test_real_rate_limiter_signature_and_isolation(self):
        with patch.object(web,'_WEB_RATE_BUCKETS',{}), patch.object(web,'WEB_RATE_LIMIT',2):
            self.assertTrue(web.rate_allowed(server,'12635'))
            self.assertTrue(web.rate_allowed(server,'12635'))
            self.assertFalse(web.rate_allowed(server,'12635'))
            self.assertTrue(web.rate_allowed(server,'12636'))
            self.assertTrue(server._check_duel_web_request_rate_limit('user:test-autochess-isolation','GET','/'))

    def handler(self,owner='12635',error=None):
        def auth(token):
            if error:raise error
            return {'human_player':owner,'human_name':'PRIVATE_NAME','machines':[]}
        h=SimpleNamespace(command='POST',path='/autochess/api/bootstrap',headers={'Content-Type':'application/json','Content-Length':'2','Authorization':'Bearer PRIVATE_TOKEN'},rfile=io.BytesIO(b'{}'),_duel_human_context=auth)
        h._send_json=lambda data,status=200,extra_headers=None: (status,data)
        return h

    def test_handler_authenticated_error_and_no_cross_identity(self):
        with patch.object(web,'enabled',return_value=True), patch.object(web,'_WEB_RATE_BUCKETS',{}), patch.object(web.economy,'context',return_value=[]), patch.object(web,'prepare',return_value={}), patch.object(web,'request',return_value={'rooms':[]}):
            self.assertEqual(web.serve(self.handler(),server)[0],200)
            self.assertEqual(self.rows()[-1]['http_status'],200)
            count=len(self.rows())
            self.assertEqual(web.serve(self.handler('12636'),server)[0],200)
            self.assertEqual(web.serve(self.handler(error=_McpError(-32001,'SECRET')),server)[0],403)
            self.assertEqual(len(self.rows()),count)
            with patch.object(web,'prepare',side_effect=TypeError('PRIVATE_TOKEN')):
                self.assertEqual(web.serve(self.handler(),server)[0],400)
            self.assertEqual(self.rows()[-1]['error_type'],'TypeError')
            self.assertNotIn('PRIVATE',self.log.read_text())
