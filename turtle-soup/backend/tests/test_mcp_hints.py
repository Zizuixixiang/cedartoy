"""MCP notification/reveal flows over HTTP and disposable SQLite databases."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import AsyncMock, patch

import test_locked_rooms as fixtures

from test_locked_rooms import database, game, mcp_app


class McpHintsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LockedRoomsTests.asyncSetUp
    asyncTearDown = fixtures.LockedRoomsTests.asyncTearDown
    headers = fixtures.LockedRoomsTests.headers
    create = fixtures.LockedRoomsTests.create
    mcp = fixtures.LockedRoomsTests.mcp

    async def seed_asks(self, rid, count):
        db = await database.get_db()
        try:
            await db.executemany(
                "INSERT INTO game_logs(room_id, type, content) VALUES (?, 'ask', '问题')",
                [(rid,)] * count,
            )
            await db.commit()
        finally:
            await db.close()

    async def hint(self, rid, *, kind='auto_hint', judgment=None):
        return await database.execute(
            'INSERT INTO game_logs(room_id, type, judgment, content, hint_text) VALUES (?, ?, ?, ?, ?)',
            (rid, kind, judgment, '秘密线索正文', '秘密线索正文'),
        )

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def auto_logs(self, response):
        return [row for row in response['logs'] if row['type'] == 'auto_hint']

    async def status(self, rid, uid=102, **params):
        return self.ok(await self.mcp('status', rid, uid, **params))

    async def test_answer_prompt_persists_at_100_through_200_and_isolates_players_rooms(self):
        rid = await self.create()
        await self.seed_asks(rid, 99)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await self.seed_asks(rid, 1)
        first = await self.status(rid)
        self.assertEqual(first['answer_reveal_prompt']['ask_count'], 100)
        self.assertNotIn('next_ask', json.dumps(first))
        for _ in range(3):
            self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await database.init_db()
        await self.seed_asks(rid, 100)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        self.assertEqual((await self.status(rid, 103))['answer_reveal_prompt']['ask_count'], 200)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid, 103))
        other = await self.create(201)
        await self.seed_asks(other, 101)
        self.assertIn('answer_reveal_prompt', await self.status(other))

    async def test_answer_prompt_message_uses_configured_trigger(self):
        await database.execute("UPDATE settings SET value='7' WHERE key='answer_reveal_prompt_count'")
        rid = await self.create()
        await self.seed_asks(rid, 6)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await self.seed_asks(rid, 1)
        result = (await self.status(rid))['answer_reveal_prompt']
        self.assertEqual(result['ask_count'], 7)
        self.assertEqual(result['message'], '本房间已达到 7 题查看门槛。调用 reveal_answer，传当前 room_id；查看后不能再进入或操作本房间。')
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await self.seed_asks(rid, 8)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        late = (await self.status(rid, 103))['answer_reveal_prompt']
        self.assertEqual(late['ask_count'], 15)
        self.assertEqual(late['message'], result['message'])
        self.assertNotIn('answer_reveal_prompt', await self.status(rid, 103))

    async def test_ask_delivers_prompt_once_above_threshold(self):
        rid = await self.create()
        await self.seed_asks(rid, 103)
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes'})), patch.object(game, '_maybe_auto_hint_safely', AsyncMock(return_value=None)):
            first = self.ok(await self.mcp('ask', rid, 102, content='问题'))
            self.assertEqual(first['answer_reveal_prompt']['ask_count'], 104)
            self.assertNotIn('answer_reveal_prompt', self.ok(await self.mcp('ask', rid, 102, content='下一个问题')))
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))

    async def test_concurrent_status_claims_each_notification_once(self):
        rid = await self.create()
        await self.seed_asks(rid, 100)
        lid = await self.hint(rid)
        results = await asyncio.gather(*(self.status(rid) for _ in range(4)))
        self.assertEqual(sum('answer_reveal_prompt' in r for r in results), 1)
        self.assertEqual(sum('auto_hint_notification' in row for r in results for row in r['logs']), 1)
        self.assertEqual((await database.fetch_one('SELECT accepted FROM room_hint_views WHERE log_id=?', (lid,)))['accepted'], None)

    async def test_notifications_survive_a_new_process(self):
        rid = await self.create()
        await self.seed_asks(rid, 100)
        await self.hint(rid)
        await self.status(rid)
        script = '''import asyncio, json, mcp_app
async def main():
    result = await mcp_app.play(mcp_app.PlayBody(game="turtle_soup", action="status", room_id=%r, path_token=%r))
    print(json.dumps(result))
asyncio.run(main())
''' % (rid, self.platform[102])
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c', script,
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'TURTLE_SOUP_DB': str(database.DB_PATH)},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        self.assertEqual(proc.returncode, 0, stderr.decode())
        result = json.loads(stdout)
        self.assertNotIn('answer_reveal_prompt', result)
        self.assertNotIn('auto_hint_notification', stdout.decode())
        self.assertNotIn('秘密线索正文', stdout.decode())

    async def assert_reveal_threshold(self, trigger):
        rid = await self.create()
        other = await self.create(201)
        await self.seed_asks(other, trigger)
        # Neither another room's questions nor this room's non-ask logs count.
        await self.hint(rid)
        for count, added in [(0, 0), (trigger - 1, trigger - 1)]:
            await self.seed_asks(rid, added)
            response = await self.mcp('reveal_answer', rid, 102)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['detail'], f'本房间需累计 {trigger} 次提问后才能查看汤底（当前 {count} 次）。')
            self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), [])
        await self.seed_asks(rid, 1)
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
        await self.seed_asks(rid, 1)
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 103))['answer_revealed'])
        revealed = await database.fetch_all('SELECT player_id FROM room_answer_reveals WHERE room_id=?', (rid,))
        self.assertEqual({r['player_id'] for r in revealed}, {self.players[102]['id'], self.players[103]['id']})

    async def test_reveal_default_threshold_and_missing_setting_fallback(self):
        await database.execute("DELETE FROM settings WHERE key='answer_reveal_prompt_count'")
        await self.assert_reveal_threshold(100)

    async def test_reveal_custom_threshold(self):
        await database.execute("UPDATE settings SET value='7' WHERE key='answer_reveal_prompt_count'")
        await self.assert_reveal_threshold(7)

    async def test_reveal_disabled_for_nonpositive_threshold(self):
        rid = await self.create()
        await self.seed_asks(rid, 101)
        for trigger in [0, -7]:
            await database.execute("UPDATE settings SET value=? WHERE key='answer_reveal_prompt_count'", (str(trigger),))
            response = await self.mcp('reveal_answer', rid, 102)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['detail'], '查看汤底功能当前未开放。')
            self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), [])

    async def test_reveal_idempotency_survives_later_threshold_changes(self):
        rid = await self.create()
        await self.seed_asks(rid, 100)
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
        before = await database.fetch_all('SELECT * FROM room_answer_reveals')
        for trigger in [200, 0, -1]:
            await database.execute("UPDATE settings SET value=? WHERE key='answer_reveal_prompt_count'", (str(trigger),))
            self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
            self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), before)

    async def test_finished_room_keeps_existing_reveal_rejection(self):
        rid = await self.create()
        await database.execute("UPDATE rooms SET status='finished' WHERE id=?", (rid,))
        for trigger in [100, 0]:
            await database.execute("UPDATE settings SET value=? WHERE key='answer_reveal_prompt_count'", (str(trigger),))
            response = await self.mcp('reveal_answer', rid, 102)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['detail'], mcp_app.ROOM_FINISHED_STATUS_HINT)
        self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), [])

    async def test_reveal_independent_idempotent_and_locks_all_participation(self):
        rid = await self.create(102)
        await self.seed_asks(rid, 100)
        other = await self.create(103)
        nid = self.ok(await self.mcp('note_add', rid, 102, content='自己的笔记'))['id']
        lid = await self.hint(rid)
        await database.execute('UPDATE rooms SET answer=? WHERE id=?', ('完整公开汤底\n【隐藏后台设定】隐藏秘密', rid))
        reveal = self.ok(await self.mcp('reveal_answer', rid, 102))
        self.assertEqual(reveal, {'answer': '完整公开汤底', 'answer_revealed': True})
        before = await database.fetch_all('SELECT * FROM room_presence WHERE room_id=?', (rid,))
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
        self.assertEqual(before, await database.fetch_all('SELECT * FROM room_presence WHERE room_id=?', (rid,)))
        self.assertEqual((await database.fetch_one('SELECT COUNT(*) AS n FROM room_answer_reveals'))['n'], 1)
        for action in ['join', 'status', 'ask', 'guess', 'hint_request', 'note_list', 'note_add', 'note_edit', 'note_delete', 'view_auto_hint']:
            res = await self.mcp(action, rid, 102, content='继续', note_id=nid, log_id=lid)
            self.assertEqual(res.status_code, 400, (action, res.text))
            self.assertEqual(res.json()['detail'], mcp_app.MCP_ANSWER_REVEALED_MESSAGE)
        for action in ['note_edit', 'note_delete']:
            for supplied_room in [None, other]:
                res = await self.mcp(action, supplied_room, 102, note_id=nid, content='绕过')
                self.assertEqual(res.json()['detail'], mcp_app.MCP_ANSWER_REVEALED_MESSAGE)
        self.assertIsNone(await mcp_app._answer_reveal_prompt(rid, self.players[102]['id']))
        for action in ['join', 'status', 'note_list']:
            self.ok(await self.mcp(action, rid, 103))
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes'})), patch.object(game, '_maybe_auto_hint_safely', AsyncMock(return_value=None)):
            self.ok(await self.mcp('ask', rid, 103, content='另一个玩家正常提问'))
        self.assertNotEqual((await database.fetch_one('SELECT status FROM rooms WHERE id=?', (rid,)))['status'], 'finished')
        self.ok(await self.mcp('list_rooms', uid=102))
        self.ok(await self.mcp('my_rooms', uid=102))
        self.assertEqual((await self.mcp('close_room', rid, 103)).status_code, 403)
        self.ok(await self.mcp('close_room', rid, 102))
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])

    async def test_auto_hint_notifies_once_then_explicit_view_and_player_isolation(self):
        rid = await self.create()
        lid = await self.hint(rid)
        first = await self.status(rid)
        self.assertEqual(self.auto_logs(first)[0]['auto_hint_notification'], {
            'log_id': lid,
            'message': '收到一条自动提示。调用 view_auto_hint，传当前 room_id 和 log_id 查看。',
        })
        self.assertNotIn('秘密线索正文', json.dumps(first, ensure_ascii=False))
        for _ in range(2):
            repeated = await self.status(rid)
            self.assertNotIn('auto_hint_notification', self.auto_logs(repeated)[0])
            self.assertNotIn('confirmation_required', json.dumps(repeated))
            self.assertIsNone(self.auto_logs(repeated)[0]['hint_text'])
        self.assertIn('auto_hint_notification', self.auto_logs(await self.status(rid, 103))[0])
        viewed = self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid))
        self.assertEqual(viewed['hint_text'], '秘密线索正文')
        self.assertEqual(viewed, self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid)))
        self.assertEqual(self.auto_logs(await self.status(rid))[0]['hint_text'], '秘密线索正文')
        self.assertIsNone(self.auto_logs(await self.status(rid, 103))[0]['hint_text'])
        self.assertIsNone(self.auto_logs(await self.status(rid, None))[0]['hint_text'])

    async def test_ask_auto_generation_and_clue_logs_are_masked_and_notify_once(self):
        rid = await self.create()
        await self.seed_asks(rid, 29)
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes', 'clue': '问答秘密线索'})), patch.object(game.judge, 'generate_hint', AsyncMock(return_value='周期秘密提示')) as generate:
            result = self.ok(await self.mcp('ask', rid, 102, content='触发第30问'))
        generate.assert_awaited_once()
        self.assertIn('log_id', result['auto_hint'])
        self.assertEqual(result['auto_hint']['message'], '收到一条自动提示。调用 view_auto_hint，传当前 room_id 和 log_id 查看。')
        self.assertNotIn('周期秘密提示', json.dumps(result, ensure_ascii=False))
        self.assertNotIn('问答秘密线索', json.dumps(result, ensure_ascii=False))
        self.assertEqual(sum('auto_hint_notification' in log for log in result['logs_since_last_own_action']), 1)
        repeated = await self.status(rid)
        self.assertNotIn('auto_hint_notification', json.dumps(repeated))
        self.assertEqual(self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=result['auto_hint']['log_id']))['hint_text'], '周期秘密提示')

    async def test_legacy_hint_data_remains_readable_without_repeated_notification(self):
        rid = await self.create()
        lids = [await self.hint(rid) for _ in range(3)]
        for lid, accepted in zip(lids, [1, 0]):
            await database.execute('INSERT INTO room_hint_views(log_id, player_id, accepted) VALUES (?, ?, ?)', (lid, self.players[102]['id'], accepted))
        logs = self.auto_logs(await self.status(rid))
        self.assertEqual(logs[0]['hint_text'], '秘密线索正文')
        self.assertTrue(logs[1]['auto_hint_rejected'])
        for log in logs[:2]:
            self.assertNotIn('auto_hint_notification', log)
            self.assertNotIn('auto_hint_confirmation_required', log)
        self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lids[1]))
        self.assertEqual(self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lids[2]))['hint_text'], '秘密线索正文')

    async def test_obsolete_ask_parameters_are_rejected_without_gameplay_side_effects(self):
        rid = await self.create()
        await self.seed_asks(rid, 100)
        lid = await self.hint(rid)
        cases = [({'confirm_reveal': value}, 'reveal_answer') for value in [True, False, None]]
        for name in ['auto_hint_log_id', 'accept_auto_hint_log_id', 'reject_auto_hint_log_id']:
            cases.extend([({name: lid}, 'view_auto_hint'), ({name: None}, 'view_auto_hint')])
        cases.extend([({'accept_auto_hint': value}, 'view_auto_hint') for value in [True, False, None]])
        cases.extend([({'auto_hint_log_id': lid, 'accept_auto_hint': value}, 'view_auto_hint') for value in [True, False]])
        before_logs = await database.fetch_all('SELECT * FROM game_logs')
        with patch.object(mcp_app, 'game_reveal_answer', AsyncMock()) as reveal, patch.object(mcp_app, '_ask_impl', AsyncMock()) as ask, patch.object(mcp_app, '_view_auto_hint', AsyncMock()) as view:
            for params, action in cases:
                with self.subTest(params=params):
                    res = await self.mcp('ask', rid, 102, content='旧流程不应判题', **params)
                    self.assertEqual(res.status_code, 400, res.text)
                    detail = res.json()['detail']
                    self.assertIn(action, detail)
                    self.assertIn('room_id', detail)
                    if action == 'reveal_answer':
                        self.assertIn('达到汤底查看门槛后', detail)
                        self.assertIn('不能再进入或操作本房间', detail)
                    else:
                        self.assertIn('log_id', detail)
            reveal.assert_not_awaited()
            ask.assert_not_awaited()
            view.assert_not_awaited()
        self.assertEqual(await database.fetch_all('SELECT * FROM game_logs'), before_logs)
        for table in ['room_answer_reveals', 'room_hint_views', 'room_answer_reveal_prompts']:
            self.assertEqual(await database.fetch_all(f'SELECT * FROM {table}'), [])
        self.assertEqual(self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid))['hint_text'], '秘密线索正文')
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])

    async def test_view_only_real_auto_hint_in_accessible_room(self):
        rid = await self.create(is_locked=True)
        lid = await self.hint(rid)
        other = await self.create(201)
        manual = await self.hint(rid, kind='hint_offer')
        for action in ['view_auto_hint', 'reveal_answer']:
            self.assertEqual((await self.mcp(action, rid, 202, log_id=lid)).status_code, 403)
        for room, log_id in [(other, lid), (rid, manual), (rid, 999999)]:
            self.assertEqual((await self.mcp('view_auto_hint', room, 102, log_id=log_id)).status_code, 404)
        self.assertEqual((await self.mcp('view_auto_hint', rid, 102)).status_code, 400)
        self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid))

    async def test_manual_hint_direct_three_per_player_per_room_without_threshold_tip(self):
        rid = await self.create()
        with patch.object(game.judge, 'generate_hint', AsyncMock(return_value='主动提示正文')) as generate:
            for remaining in [2, 1, 0]:
                result = self.ok(await self.mcp('hint_request', rid, 102))
                self.assertEqual(result['hint_text'], '主动提示正文')
                self.assertEqual(result['manual_hint_remaining'], remaining)
                self.assertNotIn('tip', result)
                self.assertNotIn('confirmation_required', result)
            denied = await self.mcp('hint_request', rid, 102)
            self.assertEqual(denied.status_code, 400)
            self.assertIn('次数已用完', denied.text)
            self.assertEqual(generate.await_count, 3)
            self.assertEqual(self.ok(await self.mcp('hint_request', rid, 103))['manual_hint_remaining'], 2)
            other = await self.create(201)
            self.assertEqual(self.ok(await self.mcp('hint_request', other, 102))['manual_hint_remaining'], 2)
        self.assertEqual((await database.fetch_one('SELECT last_hint_at_ask_count FROM rooms WHERE id=?', (rid,)))['last_hint_at_ask_count'], 0)
        old = await self.mcp('hint_respond', rid, 102)
        self.assertEqual(old.status_code, 400)
        self.assertEqual(old.json()['detail'], '自动提示改用 view_auto_hint(room_id, log_id)；主动提示直接 hint_request(room_id) 返回。')

    async def test_web_reveal_confirmation_and_room_read_remain_unchanged(self):
        rid = await self.create()
        response = await self.client.post('/soup/api/game/reveal-answer', headers=self.headers(101), json={'room_id': rid})
        self.assertTrue(self.ok(response)['confirmation_required'])
        response = await self.client.post('/soup/api/game/reveal-answer', headers=self.headers(101), json={'room_id': rid, 'confirm_reveal': True})
        self.assertTrue(self.ok(response)['answer_revealed'])
        self.ok(await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(101)))
        self.assertEqual((await self.client.post('/soup/api/game/ask', headers=self.headers(101), json={'room_id': rid, 'content': '不能继续'})).status_code, 400)

    async def test_additive_migration_on_consistent_legacy_snapshot_is_idempotent(self):
        rid = await self.create()
        lid = await self.hint(rid)
        await database.execute('INSERT INTO room_hint_views(log_id, player_id, accepted) VALUES (?, ?, 0)', (lid, self.players[102]['id']))
        snapshot = Path(self.tmp.name) / 'legacy.db'
        with sqlite3.connect(database.DB_PATH) as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
            dest.execute('DROP TABLE room_answer_reveal_prompts')
        database.DB_PATH = snapshot
        await database.init_db()
        await database.init_db()
        self.assertEqual((await database.fetch_one('PRAGMA integrity_check'))['integrity_check'], 'ok')
        self.assertEqual(await database.fetch_all('PRAGMA foreign_key_check'), [])
        self.assertEqual((await database.fetch_one('SELECT accepted FROM room_hint_views WHERE log_id=?', (lid,)))['accepted'], 0)
        await self.seed_asks(rid, 100)
        self.assertIn('answer_reveal_prompt', await self.status(rid))
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
