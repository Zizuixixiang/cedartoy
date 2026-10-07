"""Personal ask quota over real HTTP/SQLite; all model providers are mocked."""
import asyncio
from datetime import datetime, timezone
import sqlite3
from unittest.mock import AsyncMock, patch
import unittest

from fastapi import HTTPException

import test_locked_rooms as fixtures
from test_locked_rooms import database, game
import ask_quota


class AskQuotaTests(unittest.IsolatedAsyncioTestCase):
    headers = fixtures.LockedRoomsTests.headers
    create = fixtures.LockedRoomsTests.create
    mcp = fixtures.LockedRoomsTests.mcp
    asyncTearDown = fixtures.LockedRoomsTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.LockedRoomsTests.asyncSetUp(self)
        self.real_guess = game.judge.judge_guess
        self.models = {}
        for action, name, result in [
            ('ask', 'judge_ask', {'judgment': 'yes'}),
            ('guess', 'judge_guess', {'success': False, 'score': 10}),
            ('hint_request', 'generate_hint', '留意时间顺序'),
        ]:
            mock = patch.object(game.judge, name, AsyncMock(return_value=result))
            self.models[action] = mock.start()
            self.addCleanup(mock.stop)
        await database.execute("UPDATE settings SET value='0' WHERE key='hint_trigger_count'")
        self.rid = await self.create()

    def day(self):
        return ask_quota.datetime.now(ask_quota.BEIJING_TZ).date().isoformat()

    async def seed_quota(self, count=300, uid=101, day=None):
        await database.execute(
            'INSERT OR REPLACE INTO player_daily_asks VALUES (?, ?, ?)',
            (self.players[uid]['id'], day or self.day(), count),
        )

    async def quota(self, uid=101, day=None):
        row = await database.fetch_one(
            'SELECT ask_count FROM player_daily_asks WHERE player_id=? AND beijing_date=?',
            (self.players[uid]['id'], day or self.day()),
        )
        return row['ask_count'] if row else 0

    async def action(self, action='ask', uid=101, rid=None, *, mcp=False, content='男人的身高让他只能按到十楼，下雨时用雨伞按键回家。'):
        if mcp:
            return await self.mcp(action, rid or self.rid, uid, content=content)
        endpoint = 'hint/request' if action == 'hint_request' else action
        return await self.client.post(
            '/soup/api/game/' + endpoint, headers=self.headers(uid),
            json={'room_id': rid or self.rid, 'content': content},
        )

    def assert_limited(self, response):
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()['detail'], '今日个人次数已达上限（300 次），0 点重置')

    async def test_ask_history_contains_only_three_prior_questions_from_this_room(self):
        other = await self.create(uid=201)
        for mcp in [False, True]:
            with self.subTest(mcp=mcp):
                await database.execute('DELETE FROM game_logs')
                for rid, uid, kind, content, judgment in [
                    (self.rid, 101, 'ask', '过早的问题', 'yes'),
                    (self.rid, 101, 'ask', '那个人在实验室吗？', 'no'),
                    (self.rid, 102, 'ask', '他拿着探测器吗？', 'partial'),
                    (self.rid, 101, 'ask', '它打开了吗？', 'unrelated'),
                    (other, 101, 'ask', '别的房间的问题', 'yes'),
                    (self.rid, 101, 'guess', '完整猜测', 'no'),
                    (self.rid, 101, 'chat', '闲聊内容', None),
                    (self.rid, 101, 'auto_hint', '提示内容', 'auto_hint'),
                    (self.rid, 101, 'ask', game.judge.SYSTEM_BUSY_NOTICE, 'unrelated'),
                ]:
                    await database.execute(
                        'INSERT INTO game_logs (room_id,player_id,type,content,judgment) VALUES (?,?,?,?,?)',
                        (rid, self.players[uid]['id'], kind, content, judgment),
                    )
                response = await self.action(content='这个实验呢？', uid=102 if mcp else 101, mcp=mcp)
                self.assertEqual(response.status_code, 200, response.text)
                call = self.models['ask'].call_args
                self.assertEqual(call.args[2], '这个实验呢？')
                self.assertEqual(call.kwargs, {'history': ['那个人在实验室吗？', '他拿着探测器吗？', '它打开了吗？']})

    async def test_first_ask_passes_empty_history(self):
        response = await self.action(content='他是谁？')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.models['ask'].call_args.kwargs, {'history': []})

    async def test_300_allowed_301_blocks_ask_on_web_and_mcp(self):
        for mcp, uid in [(False, 101), (True, 102)]:
            with self.subTest(mcp=mcp):
                await self.seed_quota(299, uid)
                self.models['ask'].reset_mock()
                response = await self.action(uid=uid, mcp=mcp)
                self.assertEqual(response.status_code, 200, response.text)
                self.models['ask'].assert_awaited_once()
                self.assertEqual(await self.quota(uid), 300)
                for mock in self.models.values():
                    mock.reset_mock()
                self.assert_limited(await self.action(uid=uid, mcp=mcp))
                for mock in self.models.values():
                    mock.assert_not_called()
                self.assertEqual(await self.quota(uid), 300)
        player = await database.fetch_one('SELECT * FROM players WHERE id=?', (self.players[101]['id'],))
        self.assertEqual((player['ask_count'], player['ask_count_y']), (1, 1))

    async def test_hints_and_guesses_never_consume_or_obey_ask_quota(self):
        for mcp, uid in [(False, 101), (True, 102)]:
            await self.seed_quota(299, uid)
            for used in [299, 300]:
                for action in ['hint_request', 'guess']:
                    with self.subTest(mcp=mcp, used=used, action=action):
                        self.models[action].reset_mock()
                        response = await self.action(action, uid, mcp=mcp)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.models[action].assert_awaited_once()
                        self.assertEqual(await self.quota(uid), used)
                if used == 299:
                    self.assertEqual((await self.action(uid=uid, mcp=mcp)).status_code, 200)
            self.assert_limited(await self.action(uid=uid, mcp=mcp))
        for uid, used in [(101, 300), (201, 0)]:
            room = await self.client.get('/soup/api/rooms/' + self.rid, headers=self.headers(uid))
            quota = (await self.client.get('/soup/api/game/ask-quota', headers=self.headers(uid))).json()
            self.assertEqual(room.json()['ask_quota'], quota)
            self.assertEqual(quota['used'], used)
            self.assertEqual(quota['limit'], 300)
            self.assertTrue(quota['reset_at'].endswith('T00:00:00+08:00'))
        self.assertEqual((await self.action(uid=201)).status_code, 200)
        self.assertEqual(await self.quota(201), 1)

    async def test_beijing_midnight_resets_not_utc_midnight(self):
        await self.seed_quota(day='2026-10-04')
        with patch.object(ask_quota, 'datetime') as clock:
            clock.now.side_effect = lambda tz: datetime(2026, 10, 4, 15, 59, 59, tzinfo=timezone.utc).astimezone(tz)
            self.assert_limited(await self.action())
            self.models['ask'].assert_not_called()
            clock.now.side_effect = lambda tz: datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc).astimezone(tz)
            response = await self.action('ask')
            self.assertEqual(response.status_code, 200, response.text)
            quota = await ask_quota.get_ask_quota(self.players[101])
            self.assertEqual(quota, {'used': 1, 'limit': 300, 'reset_at': '2026-10-06T00:00:00+08:00'})
        self.assertEqual(await self.quota(day='2026-10-04'), 300)
        self.assertEqual(await self.quota(day='2026-10-05'), 1)

    async def test_admin_exempt_for_all_actions_web_and_mcp(self):
        await self.seed_quota()
        await database.execute('UPDATE players SET is_admin=1 WHERE id=?', (self.players[101]['id'],))
        await database.execute('UPDATE toy_users SET is_admin=1 WHERE id=101')
        for mcp in [False, True]:
            for action in self.models:
                response = await self.action(action, mcp=mcp)
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.quota(), 300)
        quota = (await self.client.get('/soup/api/game/ask-quota', headers=self.headers(101))).json()
        self.assertIsNone(quota['limit'])

    async def test_concurrent_asks_across_rooms_cannot_overrun(self):
        other = await self.create(201)
        await self.seed_quota(299)
        responses = await asyncio.gather(*(self.action(rid=rid) for rid in [self.rid, other] * 3))
        self.assertEqual(sum(r.status_code == 200 for r in responses), 1)
        for response in responses:
            if response.status_code != 200:
                self.assert_limited(response)
        self.models['ask'].assert_awaited_once()
        self.assertEqual(await self.quota(), 300)

    async def test_only_failed_ask_consumes_quota(self):
        await self.seed_quota(299)
        for action in ['guess', 'hint_request', 'ask']:
            with self.subTest(action=action):
                mock = self.models[action]
                mock.side_effect = HTTPException(status_code=503, detail='upstream unavailable')
                response = await self.action(action)
                self.assertEqual(response.status_code, 503 if action == 'hint_request' else 200)
                mock.assert_awaited_once()
                self.assertEqual(await self.quota(), 300 if action == 'ask' else 299)
        self.models['ask'].reset_mock()
        self.assert_limited(await self.action())
        self.models['ask'].assert_not_called()

    async def test_invalid_requests_do_not_consume(self):
        for action in ['ask', 'guess']:
            self.assertEqual((await self.action(action, content='')).status_code, 422)
            self.assertEqual((await self.action(action, content='<>')).status_code, 400)
        # Guess questions already have a local-only rejection; preserve that behavior.
        with patch.object(game.judge, 'judge_guess', self.real_guess):
            response = await self.action('guess', content='他是人吗？')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['score'], 0)
        for _ in range(3):
            await database.execute(
                "INSERT INTO game_logs(room_id, player_id, type, resolved) VALUES (?, ?, 'hint_offer', 1)",
                (self.rid, self.players[101]['id']),
            )
        self.assertEqual((await self.action('hint_request')).status_code, 400)
        await database.execute("UPDATE rooms SET status='finished' WHERE id=?", (self.rid,))
        for action in self.models:
            self.assertEqual((await self.action(action)).status_code, 400)
            self.models[action].assert_not_called()
        self.assertEqual(await self.quota(), 0)

    async def test_auto_hint_at_300_does_not_consume_extra_or_get_blocked(self):
        await self.seed_quota(299, uid=102)
        await database.execute("UPDATE settings SET value='1' WHERE key='hint_trigger_count'")
        response = await self.action(uid=102, mcp=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.models['ask'].assert_awaited_once()
        self.models['hint_request'].assert_awaited_once()
        self.assertIn('auto_hint', response.json())
        self.assertEqual(await self.quota(102), 300)

    async def test_quota_survives_room_end_log_cleanup_and_reinitialization(self):
        await self.seed_quota(299)
        self.assertEqual((await self.action()).status_code, 200)
        await database.execute("UPDATE rooms SET status='finished' WHERE id=?", (self.rid,))
        await database.execute('DELETE FROM game_logs WHERE room_id=?', (self.rid,))
        await database.init_db()
        other = await self.create(201)
        self.models['ask'].reset_mock()
        self.assert_limited(await self.action(rid=other))
        self.models['ask'].assert_not_called()
        self.assertEqual(await self.quota(), 300)

    async def test_exhausted_quota_does_not_block_non_model_actions(self):
        await self.seed_quota()
        response = await self.mcp('status', self.rid, 101)
        self.assertEqual(response.status_code, 200, response.text)
        # MCP status remains compact and contains no daily quota metadata.
        import json
        self.assertNotIn('quota', json.dumps(response.json()))
        self.assertNotIn('reset_at', json.dumps(response.json()))
        response = await self.client.get('/soup/api/rooms/history', headers=self.headers(101))
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.mcp('note_add', self.rid, 101, content='一条笔记')
        self.assertEqual(response.status_code, 200, response.text)
        # Satisfy the independent MCP reveal threshold without model calls.
        await database.execute("UPDATE settings SET value='1' WHERE key='answer_reveal_prompt_count'")
        await database.execute("INSERT INTO game_logs(room_id, type, content) VALUES (?, 'ask', '问题')", (self.rid,))
        response = await self.mcp('reveal_answer', self.rid, 101)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['answer_revealed'])
        self.assertEqual(await self.quota(), 300)
        for mock in self.models.values():
            mock.assert_not_called()

    async def test_additive_migration_on_consistent_old_database_copy_is_idempotent(self):
        await database.execute('DROP TABLE player_daily_asks')
        copy_path = database.DB_PATH.with_name('migration-copy.db')
        with sqlite3.connect(database.DB_PATH) as source, sqlite3.connect(copy_path) as target:
            source.backup(target)
        with patch.object(database, 'DB_PATH', copy_path):
            before = await database.fetch_all('SELECT * FROM players ORDER BY id')
            await database.init_db()
            await self.seed_quota()
            await database.init_db()
            self.assertEqual(await self.quota(), 300)
            self.assertEqual(await database.fetch_all('SELECT * FROM players ORDER BY id'), before)
            db = await database.get_db()
            try:
                rows = await db.execute_fetchall('PRAGMA integrity_check')
                self.assertEqual([tuple(row) for row in rows], [('ok',)])
                self.assertEqual(await db.execute_fetchall('PRAGMA foreign_key_check'), [])
            finally:
                await db.close()
