"""Run under the soup service venv to exercise its real aiosqlite driver."""
import ast
import asyncio
import functools
import importlib.util
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT/'turtle-soup/backend'

class SoupDiagnosticsTests(unittest.TestCase):
    def test_real_async_database_helpers(self):
        try:
            import aiosqlite
        except ImportError:
            self.skipTest('run with turtle-soup service venv for aiosqlite')
        previous = list(sys.path)
        sys.path.insert(0,str(BACKEND))
        try:
            spec=importlib.util.spec_from_file_location('_test_soup_database',BACKEND/'database.py')
            db=importlib.util.module_from_spec(spec);spec.loader.exec_module(db)
            with tempfile.TemporaryDirectory() as t:
                db.DB_PATH=Path(t)/'temp.db'
                async def exercise():
                    await db.execute('CREATE TABLE sample(value TEXT)')
                    await db.execute('INSERT INTO sample VALUES (?)',('private',))
                    self.assertEqual((await db.fetch_one('SELECT COUNT(*) AS n FROM sample'))['n'],1)
                    self.assertEqual(len(await db.fetch_all('SELECT * FROM sample')),1)
                    conn=await db.get_db()
                    self.assertEqual(type(conn._conn).__name__,'ObservedConnection')
                    self.assertIn('exercise',conn._conn._diag_origin)
                    await conn.close()
                asyncio.run(exercise())
        finally:sys.path[:]=previous

    def test_job_timing_preserves_result_and_failure(self):
        tree=ast.parse((BACKEND/'scheduler.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_diagnosed_job')
        logger=logging.getLogger('test.soup.job')
        scope=dict(functools=functools,os=os,time=time,logger=logger)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'scheduler.py','exec'),scope)
        async def successful():return 42
        async def failure():raise ValueError('PRIVATE_ERROR')
        with self.assertLogs(logger,logging.WARNING) as logs:
            self.assertEqual(asyncio.run(scope['_diagnosed_job'](successful)()),42)
            with self.assertRaises(ValueError):asyncio.run(scope['_diagnosed_job'](failure)())
        output='\n'.join(logs.output)
        self.assertIn('time_utc=',output);self.assertIn('duration_ms=',output)
        self.assertIn('outcome=failed_or_cancelled',output)
        self.assertNotIn('PRIVATE_ERROR',output)
