import io
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from cedar_backend import db_observability as diag

class DatabaseObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name)/'db.sqlite')
        self.a = sqlite3.connect(self.path, factory=diag.ObservedConnection, timeout=.02)
        self.b = sqlite3.connect(self.path, factory=diag.ObservedConnection, timeout=.02)
        self.addCleanup(self.a.close); self.addCleanup(self.b.close)
        self.a.execute('CREATE TABLE test (value TEXT)')
        self.a.commit()
        diag._LAST_LOG.clear()

    def test_busy_has_writer_site_without_sql_or_values(self):
        self.a.execute('INSERT INTO test VALUES (?)', ('SECRET_VALUE',))
        with self.assertLogs(diag._LOG, logging.WARNING) as logs:
            with self.assertRaises(sqlite3.OperationalError):
                self.b.execute('INSERT INTO test VALUES (?)', ('SECRET_OTHER',))
        output='\n'.join(logs.output)
        self.assertIn('lock_wait_failed',output)
        self.assertIn('test_busy_has_writer_site',output)
        self.assertIn('open_write_candidates',output)
        for secret in ('SECRET_VALUE','SECRET_OTHER','INSERT INTO',self.path):self.assertNotIn(secret,output)
        self.assertIn(self.a._diag_id,diag._ACTIVE)
        self.a.rollback()
        self.assertNotIn(self.a._diag_id,diag._ACTIVE)
        self.b.execute('INSERT INTO test VALUES (?)',('after',));self.b.commit()
        self.assertEqual(self.b.execute('SELECT COUNT(*) FROM test').fetchone()[0],1)

    def test_context_commit_and_rollback_cleanup(self):
        with self.a:
            self.a.cursor().executemany('INSERT INTO test VALUES (?)',[('one',),('two',)])
            self.assertIn(self.a._diag_id,diag._ACTIVE)
        self.assertNotIn(self.a._diag_id,diag._ACTIVE)
        with self.assertRaises(ValueError):
            with self.a:
                self.a.execute('INSERT INTO test VALUES (?)',('rollback',))
                raise ValueError('expected')
        self.assertNotIn(self.a._diag_id,diag._ACTIVE)
        self.assertEqual(self.a.execute('SELECT COUNT(*) FROM test').fetchone()[0],2)

    def test_slow_transaction_and_read_only_not_writer(self):
        self.a.execute('SELECT * FROM test').fetchall()
        self.assertNotIn(self.a._diag_id,diag._ACTIVE)
        self.a.execute('INSERT INTO test VALUES (?)',('not_logged',))
        with patch.object(diag,'SLOW_SECONDS',0), self.assertLogs(diag._LOG,logging.WARNING) as logs:self.a.commit()
        self.assertIn('slow_write_transaction','\n'.join(logs.output))
        self.assertNotIn('not_logged','\n'.join(logs.output))

    def test_diagnostic_failures_preserve_sqlite_semantics(self):
        with patch.object(diag,'_emit',side_effect=RuntimeError('log broken')):
            self.a.execute('INSERT INTO test VALUES (?)',('ok',))
            with patch.object(diag,'SLOW_SECONDS',0):self.a.commit()
        self.assertEqual(self.a.execute('SELECT COUNT(*) FROM test').fetchone()[0],1)
        with self.assertRaises(sqlite3.OperationalError):self.a.execute('SELECT * FROM absent')

    def test_script_and_close_cleanup(self):
        self.a.executescript("BEGIN; INSERT INTO test VALUES ('not_logged');")
        self.assertIn(self.a._diag_id,diag._ACTIVE)
        self.a.close()
        self.assertNotIn(self.a._diag_id,diag._ACTIVE)
        self.assertEqual(self.b.execute('SELECT COUNT(*) FROM test').fetchone()[0],0)

    def test_cross_process_lock_reports_owner_pid(self):
        program = "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('INSERT INTO test VALUES (?)',('PRIVATE',)); print('ready',flush=True); sys.stdin.readline(); c.rollback(); c.close()"
        process = subprocess.Popen([sys.executable, '-c', program, self.path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            with self.assertLogs(diag._LOG, logging.WARNING) as logs:
                with self.assertRaises(sqlite3.OperationalError):
                    self.b.execute('INSERT INTO test VALUES (?)', ('NEVER_LOG',))
            event = json.loads(logs.output[-1].split('account_db ',1)[1])
            self.assertTrue(any(row['pid']==process.pid and row['mode']=='WRITE' for row in event['kernel_locks']), event)
            self.assertEqual(event['open_write_candidates'], [])
            self.assertNotIn('NEVER_LOG',str(event))
        finally:
            process.communicate('release\n', timeout=5)
