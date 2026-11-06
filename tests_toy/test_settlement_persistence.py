"""Three-game audit regressions: isolated child processes, local scorer stubs only.

AutoChess has its matching Node suite in vendor/autochess/test/cedartoy-settlement-recovery.test.ts.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOUP_PYTHON = os.environ.get('SOUP_TEST_PYTHON', '/opt/cedarstar/venv/bin/python')
if not Path(SOUP_PYTHON).exists():
    SOUP_PYTHON = sys.executable


def worker(kind, folder, step, tag):
    root = Path(folder)
    # Guard all SQLite connections, including import-time mistakes. No live HTTP.
    def guard(event, args):
        if event == 'socket.connect':
            raise RuntimeError('Network forbidden in persistence regression')
        if event == 'sqlite3.connect':
            name = os.fsdecode(args[0])
            if not Path(name).resolve().is_relative_to(root):
                raise RuntimeError('Non-isolated SQLite connection')
    sys.addaudithook(guard)
    if kind == 'bdsm':
        sys.path.insert(0, str(ROOT))
        from bdsmtest import handler as h
        h.DB_PATH = str(root / 'bdsm.db')
        h.api.init_session = lambda: {'rauth': {'rid': tag}, 'pdata': {}}
        h.api.fetch_questions = lambda *a: [{'id': 3, 'wording': 'fixture'}]
        player = 'guest:settlementaudit'
        def score(*a):
            if step == 'blocked':
                (root / (tag + '.ready')).touch()
                deadline = time.monotonic() + 20
                while not (root / (tag + '.release')).exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError('barrier timeout')
                    time.sleep(.01)
            if step == 'score_failure':
                raise h.BdsmApiError('local scorer unavailable')
            return {'scores': [], 'rid': tag}
        h.api.submit_and_score = score
        if step in ('start', 'normal'):
            out = h.bdsmtest_start({'player_id': player, 'mode': 'normal' if step == 'normal' else 'fast'})
        else:
            try:
                if tag.startswith('normal'):
                    out = h.bdsmtest_answer({'player_id': player, 'score': 4})
                else:
                    out = h.bdsmtest_answer_batch({'player_id': player, 'answers': {3: 5 if tag == 'updated' else 4}})
            except h.JsonRpcError as exc:
                print(json.dumps({'error': exc.code, 'message': exc.message})); return
        print(json.dumps({'ok': True, 'text': out})); return

    import asyncio
    os.environ['TURTLE_SOUP_DB'] = str(root / 'soup.db')
    sys.path.insert(0, str(ROOT / 'turtle-soup/backend'))
    import database
    from routers import game, rooms, admin
    from models import GuessBody, RevealAnswerBody
    from fastapi import HTTPException
    async def fake_judge(*a, **kw):
        (root / (tag + '.ready')).touch()
        deadline = time.monotonic() + 20
        while not (root / (tag + '.release')).exists():
            if time.monotonic() > deadline:
                raise RuntimeError('barrier timeout')
            await asyncio.sleep(.01)
        return {'success': tag != 'wrong', 'score': 100 if tag != 'wrong' else 20, 'answer': 'fixture'}
    game.judge.judge_guess = fake_judge
    player = {'id': 2 if tag == 'second' else 1, 'username': 'fixture'}
    async def run():
        if step == 'init':
            await database.init_db()
            for i in (1, 2):
                await database.execute('INSERT INTO players(id,username) VALUES(?,?)', (i, 'fixture'+str(i)))
            await database.execute("INSERT INTO rooms(id,surface,answer,status,created_by) VALUES('AUDIT123','fixture','fixture','playing',1)")
            # Both players participated before the race, so each game count must advance once.
            for i in (1, 2):
                await database.execute("INSERT INTO game_logs(room_id,player_id,type,content) VALUES('AUDIT123',?,'ask','fixture')", (i,))
            out = {'ok': True}
        else:
            try:
                if step == 'guess': out = await game.guess(GuessBody(room_id='AUDIT123',content='fixture'),player)
                elif step == 'reveal': out = await game.reveal_answer(RevealAnswerBody(room_id='AUDIT123',confirm_reveal=True),player)
                elif step == 'close': out = await rooms.close_room('AUDIT123',player)
                elif step == 'admin_finish': out = await admin.finish_room('AUDIT123',player)
                elif step == 'expire':
                    from scheduler import cleanup_inactive_rooms
                    await database.execute("UPDATE game_logs SET created_at='2000-01-01 00:00:00'")
                    await cleanup_inactive_rooms()
                    out = {'ok': True}
                else: raise AssertionError(step)
            except HTTPException as exc:
                out = {'error': exc.status_code, 'detail': exc.detail}
            except sqlite3.DatabaseError as exc:
                out = {'database_error': str(exc)}
        print(json.dumps(out))
    asyncio.run(run())


class ProcessFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cedar-settlement-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def start_child(self, kind, step, tag='first'):
        python = SOUP_PYTHON if kind == 'soup' else sys.executable
        child = subprocess.Popen([python, '-B', __file__, '--worker', kind, str(self.root), step, tag],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        def stop():
            if child.poll() is None: child.kill()
            child.communicate()
        self.addCleanup(stop)
        return child

    def finish(self, child):
        out, err = child.communicate(timeout=25)
        self.assertEqual(child.returncode, 0, err)
        return json.loads(out)

    def call(self, kind, step, tag='first'):
        return self.finish(self.start_child(kind, step, tag))

    def ready(self, tag):
        deadline = time.monotonic() + 15
        while not (self.root / (tag + '.ready')).exists():
            self.assertLess(time.monotonic(), deadline, 'child did not reach local scorer')
            time.sleep(.01)

    def release(self, tag):
        (self.root / (tag + '.release')).touch()

    def rows(self, db, sql):
        with sqlite3.connect(self.root / db) as conn:
            return conn.execute(sql).fetchall()


class BdsmCompletionTests(ProcessFixture):
    def test_old_completion_preserves_new_session_and_new_result(self):
        for complete_new in (False, True):
            with self.subTest(complete_new=complete_new):
                tag = 'old' + str(complete_new)
                self.call('bdsm', 'start', tag)
                old = self.start_child('bdsm', 'blocked', tag); self.ready(tag)
                self.call('bdsm', 'start', 'new')  # Must finish while scorer is still blocked.
                if complete_new: self.call('bdsm', 'finish', 'new')
                before = self.rows('bdsm.db', 'SELECT * FROM test_sessions'), self.rows('bdsm.db', 'SELECT * FROM test_results')
                self.release(tag)
                self.assertEqual(self.finish(old)['error'], -32002)
                self.assertEqual(before, (self.rows('bdsm.db', 'SELECT * FROM test_sessions'), self.rows('bdsm.db', 'SELECT * FROM test_results')))

    def test_legacy_normal_retry_releases_writer_and_preserves_restarted_session(self):
        self.call('bdsm', 'normal', 'normal-old')
        # Existing pre-upgrade row is adopted without losing answers.
        with sqlite3.connect(self.root / 'bdsm.db') as c:
            c.execute('UPDATE test_sessions SET bdsm_session_id=NULL')
        self.assertEqual(self.call('bdsm', 'score_failure', 'normal-old')['error'], -32010)
        self.assertEqual(self.rows('bdsm.db', 'SELECT answers FROM test_sessions'), [('[4]',)])
        old = self.start_child('bdsm', 'blocked', 'normal-retry'); self.ready('normal-retry')
        self.call('bdsm', 'normal', 'normal-new')
        self.release('normal-retry'); self.assertEqual(self.finish(old)['error'], -32002)
        self.assertEqual(self.rows('bdsm.db', 'SELECT answers FROM test_sessions'), [('[]',)])

    def test_concurrent_identical_completions_keep_first_result(self):
        self.call('bdsm', 'start')
        a = self.start_child('bdsm', 'blocked', 'one'); self.ready('one')
        b = self.start_child('bdsm', 'blocked', 'two'); self.ready('two')
        self.release('one'); self.assertTrue(self.finish(a)['ok'])
        self.release('two'); self.assertTrue(self.finish(b)['ok'])
        self.assertEqual(self.rows('bdsm.db', 'SELECT COUNT(*) FROM test_sessions'), [(0,)])
        detail = json.loads(self.rows('bdsm.db', 'SELECT result_detail FROM test_results')[0][0])
        self.assertEqual(detail['rid'], 'one')

    def test_changed_answers_reject_old_completion_of_same_session(self):
        self.call('bdsm', 'start')
        old = self.start_child('bdsm', 'blocked', 'old'); self.ready('old')
        updated = self.start_child('bdsm', 'blocked', 'updated'); self.ready('updated')
        self.release('old'); self.assertEqual(self.finish(old)['error'], -32002)
        self.assertEqual(self.rows('bdsm.db', 'SELECT answers FROM test_sessions'), [('[5]',)])
        self.assertEqual(self.rows('bdsm.db', 'SELECT COUNT(*) FROM test_results'), [(0,)])
        self.release('updated'); self.assertTrue(self.finish(updated)['ok'])
        detail = json.loads(self.rows('bdsm.db', 'SELECT result_detail FROM test_results')[0][0])
        self.assertEqual(detail['rid'], 'updated')


class SoupCompletionTests(ProcessFixture):
    def setUp(self):
        super().setUp()
        self.call('soup', 'init')

    def assert_once(self, winner=1):
        self.assertEqual(self.rows('soup.db', 'SELECT status,winner_id FROM rooms'), [('finished', winner)])
        self.assertEqual(self.rows('soup.db', 'SELECT id,win_count,game_count FROM players ORDER BY id'), [(1,int(winner==1),1),(2,int(winner==2),1)])
        self.assertEqual(self.rows('soup.db', "SELECT COUNT(*) FROM game_logs WHERE judgment='game_over'"), [(1,)])

    def test_two_correct_and_duplicate_request_settle_once(self):
        a = self.start_child('soup', 'guess', 'first'); self.ready('first')
        b = self.start_child('soup', 'guess', 'second'); self.ready('second')
        self.release('first'); out = self.finish(a)
        self.assertTrue(out['correct']); self.assertEqual(out['result_log']['judgment'], 'game_over')
        self.release('second'); self.assertEqual(self.finish(b)['error'], 400)
        self.assertEqual(self.call('soup', 'guess')['error'], 400)
        self.assert_once()
        self.call('soup', 'close'); self.call('soup', 'admin_finish'); self.assert_once()
        self.assertEqual(self.call('soup', 'reveal')['error'],400)

    def test_same_player_concurrent_duplicate_settles_once(self):
        a = self.start_child('soup', 'guess', 'first'); self.ready('first')
        b = self.start_child('soup', 'guess', 'duplicate'); self.ready('duplicate')
        self.release('duplicate'); self.assertTrue(self.finish(b)['correct'])
        self.release('first'); self.assertEqual(self.finish(a)['error'], 400)
        self.assert_once()
        self.assertEqual(self.rows('soup.db', "SELECT COUNT(*) FROM game_logs WHERE type='guess'"), [(1,)])

    def test_reveal_while_guess_waits_prevents_award(self):
        a = self.start_child('soup', 'guess'); self.ready('first')
        self.assertTrue(self.call('soup', 'reveal')['answer_revealed'])
        self.release('first'); self.assertEqual(self.finish(a)['error'],400)
        self.assertEqual(self.rows('soup.db', 'SELECT status,winner_id FROM rooms'), [('playing',None)])
        self.assertEqual(self.rows('soup.db', 'SELECT SUM(win_count) FROM players'), [(0,)])

    def test_close_and_admin_finish_while_guess_waits(self):
        for closer in ('close','admin_finish','expire'):
            with self.subTest(closer=closer):
                with sqlite3.connect(self.root/'soup.db') as c: c.execute("UPDATE rooms SET status='playing',finished_at=NULL")
                tag = closer
                a=self.start_child('soup','guess',tag);self.ready(tag)
                self.call('soup',closer);self.release(tag)
                self.assertEqual(self.finish(a)['error'],400)
                self.assertEqual(self.rows('soup.db','SELECT SUM(win_count) FROM players'),[(0,)])

    def test_game_over_write_failure_rolls_back_entire_completion_then_retries(self):
        with sqlite3.connect(self.root/'soup.db') as c:
            c.execute("CREATE TRIGGER fail_game_over BEFORE INSERT ON game_logs WHEN NEW.judgment='game_over' BEGIN SELECT RAISE(ABORT,'injected log failure'); END")
        self.release('first')
        self.assertIn('database_error',self.call('soup','guess'))
        self.assertEqual(self.rows('soup.db','SELECT status,winner_id FROM rooms'),[('playing',None)])
        self.assertEqual(self.rows('soup.db','SELECT SUM(game_count),SUM(win_count) FROM players'),[(0,0)])
        self.assertEqual(self.rows('soup.db',"SELECT COUNT(*) FROM game_logs WHERE type='guess'"),[(0,)])
        self.assertEqual(self.rows('soup.db','SELECT COUNT(*) FROM judge_audit_logs'),[(0,)])
        with sqlite3.connect(self.root/'soup.db') as c:c.execute('DROP TRIGGER fail_game_over')
        self.assertTrue(self.call('soup','guess')['correct']);self.assert_once()

    def test_wrong_guess_contract(self):
        self.release('wrong');out=self.call('soup','guess','wrong')
        self.assertFalse(out['correct']);self.assertEqual(out['score'],20)
        self.assertEqual(out['result_log']['judgment'],'guess_result')
        self.assertEqual(self.rows('soup.db','SELECT status FROM rooms'),[('playing',)])


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':worker(*sys.argv[2:])
    else:unittest.main()
