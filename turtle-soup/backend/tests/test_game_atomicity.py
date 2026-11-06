"""Cross-process transaction/crash probes; all databases and gates live in /tmp."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
from fastapi import HTTPException
from models import ContentBody, GuessBody, HintResponseBody
from routers import game


async def worker(db_path, action, failure='', gate=''):
    database.DB_PATH = Path(db_path)
    player = {'id': 1, 'username': 'test', 'is_guest': 1, 'is_ai': 0}

    async def judged(*args, **kwargs):
        if gate:
            Path(f'{gate}.{os.getpid()}').touch()
            deadline = asyncio.get_running_loop().time() + 15
            while len(list(Path(gate).parent.glob(Path(gate).name + '.*'))) < 2:
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError('second test worker did not reach the gate')
                await asyncio.sleep(.01)
        return {'judgment': 'yes', 'clue': 'shared clue', 'success': True, 'score': 100, 'answer': 'answer'}

    async def hint(*args, **kwargs):
        await judged()
        return 'hint'

    original_get_db = database.get_db

    async def get_db():
        db = await original_get_db()
        execute = db.execute

        async def crashing_execute(sql, *args, **kwargs):
            cursor = await execute(sql, *args, **kwargs)
            if failure and failure in sql:
                os._exit(71)  # No rollback/finally: exercise SQLite recovery.
            return cursor

        db.execute = crashing_execute
        return db

    with patch.object(game, 'get_db', get_db), \
         patch.object(game, 'require_room_access', AsyncMock()), \
         patch.object(game, 'reserve_daily_ask', AsyncMock()), \
         patch.object(game, 'broadcast', AsyncMock()), \
         patch.object(game, '_maybe_auto_hint_safely', AsyncMock()), \
         patch.object(game.judge, 'judge_ask', judged), \
         patch.object(game.judge, 'judge_guess', judged), \
         patch.object(game.judge, 'generate_hint', hint):
        try:
            if action == 'ask':
                result, task = await game._ask_impl(ContentBody(room_id='TEST', content='question'), player)
                await task
            elif action == 'hint':
                result = await game._offer_hint(await game._room('TEST'), 3)
            elif action == 'manual':
                result = await game._offer_hint(await game._room('TEST'), 3, manual=True, player=player)
            elif action == 'guess':
                result = await game.guess(GuessBody(room_id='TEST', content='answer'), player)
            else:
                result = await game.hint_respond(HintResponseBody(room_id='TEST', log_id=4, accept=True), player)
            print(json.dumps({'status': 200, 'result': result}))
        except HTTPException as exc:
            print(json.dumps({'status': exc.status_code}))


class GameAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='soup-atomic-', dir='/tmp')
        self.db_path = Path(self.tmp.name) / 'soup.db'
        self.old_db = database.DB_PATH
        database.DB_PATH = self.db_path
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                INSERT INTO players(id, username, is_guest) VALUES (1, 'test', 1), (2, 'other', 1);
                INSERT INTO rooms(id, surface, answer, status) VALUES ('TEST', 'surface', 'answer', 'playing');
                INSERT INTO game_logs(room_id, type, content) VALUES
                    ('TEST', 'ask', 'seed1'), ('TEST', 'ask', 'seed2'), ('TEST', 'ask', 'seed3');
                UPDATE settings SET value='3' WHERE key='hint_trigger_count';
            """)

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db
        self.tmp.cleanup()

    def sql(self, sql, args=()):
        with sqlite3.connect(self.db_path) as db:
            return db.execute(sql, args).fetchall()

    def spawn(self, action, failure='', gate=''):
        return subprocess.Popen(
            [sys.executable, __file__, 'worker', str(self.db_path), action, failure, gate],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    async def collect(self, proc, expected=0):
        try:
            out, err = await asyncio.to_thread(proc.communicate, timeout=30)
        except BaseException:
            proc.kill()
            await asyncio.to_thread(proc.communicate)
            raise
        self.assertEqual(proc.returncode, expected, out + err)
        return json.loads(out) if out else None

    async def run_worker(self, action, failure=''):
        return await self.collect(self.spawn(action, failure), 71 if failure else 0)

    async def pair(self, action, gate=False):
        path = str(Path(self.tmp.name) / 'gate') if gate else ''
        return await asyncio.gather(*(self.collect(self.spawn(action, gate=path)) for _ in range(2)))

    async def test_ask_crash_rolls_back_log_statistics_and_clue_then_retry(self):
        for statement in ["VALUES (?, ?, 'ask'", 'UPDATE players SET ask_count', "VALUES (?, ?, 'auto_hint'"]:
            with self.subTest(statement=statement):
                await self.run_worker('ask', statement)
                self.assertEqual(self.sql('SELECT ask_count, ask_count_y FROM players WHERE id=1'), [(0, 0)])
                self.assertEqual(self.sql('SELECT COUNT(*) FROM game_logs WHERE player_id=1'), [(0,)])
        self.assertEqual((await self.run_worker('ask'))['status'], 200)
        self.assertEqual(self.sql('SELECT ask_count, ask_count_y FROM players WHERE id=1'), [(1, 1)])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM game_logs WHERE player_id=1'), [(2,)])

    async def test_concurrent_asks_count_both_but_publish_clue_once(self):
        results = await self.pair('ask', gate=True)
        self.assertEqual([r['status'] for r in results], [200, 200])
        self.assertEqual(self.sql('SELECT ask_count, ask_count_y FROM players WHERE id=1'), [(2, 2)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM game_logs WHERE judgment='auto_hint'"), [(1,)])

    async def test_auto_hint_crash_rolls_back_log_and_watermark_then_retry(self):
        for statement in ["VALUES (?, 'auto_hint'", 'UPDATE rooms SET last_hint_at_ask_count']:
            with self.subTest(statement=statement):
                await self.run_worker('hint', statement)
                self.assertEqual(self.sql('SELECT last_hint_at_ask_count FROM rooms'), [(0,)])
                self.assertEqual(self.sql("SELECT COUNT(*) FROM game_logs WHERE type='auto_hint'"), [(0,)])
        await self.run_worker('hint')
        await self.run_worker('hint')
        self.assertEqual(self.sql('SELECT last_hint_at_ask_count FROM rooms'), [(3,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM game_logs WHERE type='auto_hint'"), [(1,)])

    async def test_concurrent_auto_hint_publication_is_once_per_watermark(self):
        results = await self.pair('hint', gate=True)
        self.assertEqual(sum(r['result'] is not None for r in results), 1)
        self.assertEqual(self.sql('SELECT last_hint_at_ask_count FROM rooms'), [(3,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM game_logs WHERE type='auto_hint'"), [(1,)])

    async def test_hint_response_crash_and_concurrent_retries_keep_legacy_contract(self):
        self.sql("INSERT INTO game_logs(id, room_id, type, hint_text) VALUES (4, 'TEST', 'hint_offer', 'secret')")
        for statement in ['UPDATE game_logs SET resolved', 'INSERT INTO game_logs']:
            with self.subTest(statement=statement):
                await self.run_worker('respond', statement)
                self.assertEqual(self.sql('SELECT resolved FROM game_logs WHERE id=4'), [(0,)])
                self.assertEqual(self.sql("SELECT COUNT(*) FROM game_logs WHERE type='hint_accept'"), [(0,)])
        results = await self.pair('respond')
        self.assertEqual(sorted(r['status'] for r in results), [200, 409])
        self.assertEqual(self.sql('SELECT resolved FROM game_logs WHERE id=4'), [(1,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM game_logs WHERE type='hint_accept'"), [(1,)])
        self.assertEqual((await self.run_worker('respond'))['status'], 409)

    async def test_manual_hint_limit_is_checked_after_concurrent_generation(self):
        self.sql("INSERT INTO game_logs(room_id, player_id, type, resolved) VALUES ('TEST', 1, 'hint_offer', 1)")
        self.sql("INSERT INTO game_logs(room_id, player_id, type, resolved) VALUES ('TEST', 1, 'hint_offer', 1)")
        results = await self.pair('manual', gate=True)
        self.assertEqual(sorted(r['status'] for r in results), [200, 400])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM game_logs WHERE type='hint_offer'"), [(3,)])

    async def test_ask_rechecks_finish_and_personal_reveal_after_model(self):
        player = {'id': 1, 'is_guest': 1}
        async def judge_ask(*args, **kwargs):
            if mode == 'finished':
                self.sql("UPDATE rooms SET status='finished' WHERE id='TEST'")
            else:
                self.sql("INSERT INTO room_answer_reveals(room_id, player_id) VALUES ('TEST', 1)")
            return {'judgment': 'yes'}
        for mode in ['finished', 'revealed']:
            self.sql("UPDATE rooms SET status='playing' WHERE id='TEST'")
            with patch.object(game, 'require_room_access', AsyncMock()), \
                 patch.object(game, 'reserve_daily_ask', AsyncMock()), \
                 patch.object(game.judge, 'judge_ask', judge_ask):
                with self.assertRaises(HTTPException) as raised:
                    await game._ask_impl(ContentBody(room_id='TEST', content='question'), player)
                self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(self.sql('SELECT ask_count FROM players WHERE id=1'), [(0,)])
            self.assertEqual(self.sql('SELECT COUNT(*) FROM game_logs WHERE player_id=1'), [(0,)])

    async def test_existing_guess_atomic_settlement_survives_crash_and_race(self):
        await self.run_worker('guess', 'UPDATE players SET win_count')
        self.assertEqual(self.sql('SELECT status, winner_id FROM rooms'), [('playing', None)])
        self.assertEqual(self.sql('SELECT win_count, game_count FROM players WHERE id=1'), [(0, 0)])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM judge_audit_logs'), [(0,)])
        results = await self.pair('guess', gate=True)
        self.assertEqual(sorted(r['status'] for r in results), [200, 400])
        self.assertEqual(self.sql('SELECT win_count, game_count FROM players WHERE id=1'), [(1, 1)])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM judge_audit_logs'), [(1,)])
        self.assertEqual(self.sql('PRAGMA integrity_check'), [('ok',)])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        asyncio.run(worker(*sys.argv[2:]))
    else:
        unittest.main()
