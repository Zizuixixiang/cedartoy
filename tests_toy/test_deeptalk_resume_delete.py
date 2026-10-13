"""Continuation and destructive-operation regressions, disposable databases only."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier
import unittest
from unittest.mock import patch

from deeptalk.catalog import DeeptalkError
from tests_toy import test_deeptalk as fixtures
from tests_toy.test_deeptalk import HUMAN, AI, OTHER_HUMAN, OTHER_AI


class ResumeDeleteTests(unittest.TestCase):
    setUp = fixtures.DeeptalkTests.setUp
    connect = fixtures.DeeptalkTests.connect
    call = fixtures.DeeptalkTests.call
    answer = fixtures.DeeptalkTests.answer
    fill = fixtures.DeeptalkTests.fill
    submit = fixtures.DeeptalkTests.submit
    mcp = fixtures.DeeptalkTests.mcp
    http = fixtures.DeeptalkTests.http

    def create_round(self, key, human=HUMAN, topic='relations_communication'):
        return self.store.create(human, AI, topic, key)['round_id']

    def delete_params(self, rid=None):
        rid = rid or self.rid
        preview = self.call(HUMAN, 'delete_preview', round_id=rid)
        return dict(round_id=rid, delete_version=preview['delete_version'], confirm=True)

    def test_encrypted_resume_reads_ciphertext_and_fails_closed_without_key(self):
        """The resume selector must not parse ciphertext as JSON or bypass key validation."""
        self.answer(HUMAN, text='ENCRYPTED_RESUME_SENTINEL_20261014')
        with sqlite3.connect(self.db) as conn:
            stored = conn.execute('SELECT human_answers FROM deeptalk_rounds WHERE id=?',
                                  (self.rid,)).fetchone()[0]
        self.assertTrue(stored.startswith('dtenc:v1:'))
        self.assertNotIn('ENCRYPTED_RESUME_SENTINEL', stored)
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication'),
                         {'round_id': self.rid})
        with patch.dict('os.environ', {'DEEPTALK_SAVE_KEY_FILE': str(self.root / 'missing.key')}):
            with self.assertRaises(DeeptalkError) as error:
                self.call(HUMAN, 'resume', topic_id='relations_communication')
        self.assertEqual(error.exception.status, 503)

    def test_resume_ranking_topic_identity_and_all_pages(self):
        self.answer(HUMAN)
        progressed = self.rid
        # New empty rounds, including more than a page, cannot displace real work.
        for i in range(23):
            self.create_round(f'new_empty_{i}')
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication')['round_id'], progressed)
        newer = self.create_round('new_progress')
        self.rid = newer
        self.answer(AI)
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication')['round_id'], newer)
        self.rid = progressed
        self.answer(HUMAN, q=2)
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication')['round_id'], progressed)
        other = self.create_round('other_human', OTHER_HUMAN)
        wrong_topic = self.create_round('other_topic', topic='couple_50')
        for hint in (other, wrong_topic, 'missing'):
            self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication', draft_round_id=hint)['round_id'], progressed)
        self.assertEqual(self.call(OTHER_HUMAN, 'resume', topic_id='relations_communication')['round_id'], other)
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='couple_50')['round_id'], wrong_topic)
        local = self.create_round('local_draft_round')
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication', draft_round_id=local)['round_id'], local)

    def test_resume_ai_first_and_skip_progress_but_never_human_submitted(self):
        empty = self.create_round('empty_latest')
        self.answer(HUMAN, skip=True)
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication')['round_id'], self.rid)
        self.fill(AI); self.submit(AI)
        selected = self.call(HUMAN, 'resume', topic_id='relations_communication')['round_id']
        view = self.call(HUMAN, 'view', round_id=selected)
        self.assertEqual(selected, self.rid)
        self.assertFalse(view['submitted']); self.assertTrue(view['peer_visible'])
        self.assertEqual(view['next_number'], 2)
        self.fill(HUMAN); self.submit(HUMAN)
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication', draft_round_id=self.rid)['round_id'], empty)
        self.call(HUMAN, 'delete', **self.delete_params(empty))
        self.assertIsNone(self.call(HUMAN, 'resume', topic_id='relations_communication')['round_id'])
        self.assertEqual(self.call(HUMAN, 'view', round_id=self.rid)['submitted'], True)

    def test_resume_shared_binding_and_legacy_owner_rules(self):
        shared = self.call(HUMAN, 'create', topic_id='couple_50', request_id='shared_round')['round_id']
        with self.connect() as conn:
            conn.execute('DELETE FROM user_bindings WHERE human_user_id=101')
            conn.execute('INSERT INTO user_bindings VALUES (102,201)')
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='couple_50')['round_id'], shared)
        self.assertEqual(self.call(HUMAN, 'resume', topic_id='relations_communication')['round_id'], self.rid)
        self.assertIsNone(self.call(OTHER_HUMAN, 'resume', topic_id='couple_50')['round_id'])
        self.assertEqual(self.http(AI, 'resume', topic_id='couple_50')[0], 403)

    def test_delete_permissions_confirmation_and_request_cleanup(self):
        self.answer(HUMAN); self.answer(AI)
        other = self.create_round('preserved_round')
        params = self.delete_params()
        for user, status in [(None, 401), (OTHER_HUMAN, 404), (AI, 403), (OTHER_AI, 403)]:
            self.assertEqual(self.http(user, 'delete', post=True, **params)[0], status)
        self.assertEqual(self.http(OTHER_HUMAN, 'delete_preview', round_id=self.rid)[0], 404)
        self.assertEqual(self.http(HUMAN, 'delete', **params)[0], 404)
        for confirm in (False, 'true', 1, None):
            self.assertEqual(self.http(HUMAN, 'delete', post=True, **dict(params, confirm=confirm))[0], 400)
        self.assertEqual(self.http(HUMAN, 'delete', post=True, **dict(params, delete_version='wrong'))[0], 409)
        self.assertTrue(self.mcp(AI, 'delete', **params)['result']['isError'])
        with self.assertRaises(DeeptalkError):
            self.store.delete(AI, params)
        status, result, headers = self.http(HUMAN, 'delete', post=True, **params, human_id=102, player_id='102')
        self.assertEqual(status, 200); self.assertTrue(result['deleted'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM deeptalk_requests WHERE round_id=?', (self.rid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM deeptalk_rounds').fetchone()[0], 1)
        self.assertEqual(self.call(HUMAN, 'view', round_id=other)['round_id'], other)
        self.assertEqual(self.http(HUMAN, 'view', round_id=self.rid)[0], 404)
        self.assertEqual(self.http(HUMAN, 'delete', post=True, **params)[0], 404)
        # Exact old idempotency replay must not restore a deleted answer/round.
        self.assertEqual(self.http(HUMAN, 'answer', post=True, round_id=self.rid, question_id='q1',
                                  revision=0, request_id='101_00000000', text='SECRET_101_1')[0], 404)

    def test_delete_stale_on_either_answer_or_submission(self):
        for user in (HUMAN, AI):
            before = self.delete_params()
            self.answer(user)
            self.assertEqual(self.http(HUMAN, 'delete', post=True, **before)[0], 409)
            self.fill(user)
            before = self.delete_params()
            self.submit(user)
            self.assertEqual(self.http(HUMAN, 'delete', post=True, **before)[0], 409)
        self.assertTrue(self.call(HUMAN, 'delete', **self.delete_params())['deleted'])

    def test_delete_atomic_against_ai_writes(self):
        for i in range(20):
            # Both original single-AI blind records and new shared chat rounds.
            self.rid = (self.create_round(f'racing_delete_{i}') if i < 10 else
                        self.call(HUMAN, 'create', topic_id='relations_communication',
                                  request_id=f'racing_chat_delete_{i}')['round_id'])
            params = self.delete_params()
            barrier = Barrier(2)
            def delete():
                barrier.wait()
                try: self.store.delete(HUMAN, params); return 200
                except DeeptalkError as exc: return exc.status
            def answer():
                barrier.wait()
                try:
                    self.call(AI, 'answer', round_id=self.rid, question_id='q1', text='in flight',
                              revision=0, request_id=f'racing_answer_{i}')
                    return 200
                except DeeptalkError as exc: return exc.status
            with ThreadPoolExecutor(2) as pool:
                deletion = pool.submit(delete); writing = pool.submit(answer)
                outcomes = (deletion.result(), writing.result())
            self.assertIn(outcomes, [(200, 404), (409, 200)])
            with sqlite3.connect(self.db) as conn:
                count = conn.execute('SELECT COUNT(*) FROM deeptalk_rounds WHERE id=?', (self.rid,)).fetchone()[0]
                receipts = conn.execute('SELECT COUNT(*) FROM deeptalk_requests WHERE round_id=?', (self.rid,)).fetchone()[0]
                self.assertEqual((count, receipts), (0, 0) if outcomes[0] == 200 else (1, 1))

    def test_delete_rolls_back_if_receipt_cleanup_fails(self):
        self.answer(HUMAN)
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON deeptalk_rounds BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call(HUMAN, 'delete', **self.delete_params())
        self.assertEqual(self.call(HUMAN, 'status', round_id=self.rid)['revision'], 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM deeptalk_requests').fetchone()[0], 1)
