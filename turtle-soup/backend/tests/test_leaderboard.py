"""HTTP leaderboard aggregation against the real schema in a disposable DB."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
import database
from auth_utils import current_player
from routers import leaderboard


class LeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='soup-leaderboard-')
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(database, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        # A UTC instant on the previous date is already October 1 in Beijing.
        self.tz_patch = patch.dict(os.environ, TZ='Asia/Shanghai')
        self.tz_patch.start()
        time.tzset()
        self.addCleanup(self.restore_tz)
        self.clock = patch.object(leaderboard, 'SQL_NOW', "datetime('2026-09-30 16:30:00', 'localtime')")
        self.clock.start()
        self.addCleanup(self.clock.stop)
        await database.init_db()
        app = FastAPI()
        app.include_router(leaderboard.router)
        app.dependency_overrides[current_player] = lambda: {'id': 1}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://soup.test')
        self.addAsyncCleanup(self.client.aclose)
        for pid, guest, name in [(1, 0, '甲'), (2, 0, '乙'), (3, 0, '  '), (4, 1, '游客'), (5, 0, '零分')]:
            await database.execute('INSERT INTO players(id, username, is_guest, is_ai) VALUES (?, ?, ?, ?)', (pid, name, guest, pid == 2))

    def restore_tz(self):
        self.tz_patch.stop()
        time.tzset()

    async def room(self, rid, finished='2026-10-01 00:00:00', winner=None, status='finished'):
        await database.execute("INSERT INTO rooms(id, surface, answer, status, winner_id, finished_at, created_by) VALUES (?, 's', 'a', ?, ?, ?, 5)", (rid, status, winner, finished))

    async def log(self, rid, pid, kind='ask', judgment='yes', at='2026-10-01 00:00:00'):
        lid = await database.execute('INSERT INTO game_logs(room_id, player_id, type, judgment) VALUES (?, ?, ?, ?)', (rid, pid, kind, judgment))
        # Existing insert triggers set local now; set the fixture time afterwards.
        await database.execute('UPDATE game_logs SET created_at = ? WHERE id = ?', (at, lid))

    async def rows(self, metric, scope='today'):
        response = await self.client.get(f'/leaderboard/{metric}' + (f'?scope={scope}' if scope is not None else ''))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def scores(self, metric):
        return [(r['id'], r['score']) for r in await self.rows(metric)]

    async def test_all_unchanged_invalid_scope_and_metric_fallback(self):
        columns = ['game_count', 'win_count', 'ask_count', 'ask_count_y', 'ask_count_n']
        await database.execute('UPDATE players SET ' + ', '.join(f'{c} = 9' for c in columns) + ' WHERE id IN (1, 2, 4)')
        await database.execute('UPDATE players SET ' + ', '.join(f'{c} = 3' for c in columns) + ' WHERE id = 3')
        for metric in ['games', 'wins', 'asks', 'yes', 'no', 'unknown']:
            implicit = await self.rows(metric, None)
            self.assertEqual(implicit, await self.rows(metric, 'all'))
            self.assertEqual(implicit, [
                {'id': 1, 'username': '甲', 'is_ai': 0, 'score': 9},
                {'id': 2, 'username': '乙', 'is_ai': 1, 'score': 9},
                {'id': 3, 'username': '玩家3', 'is_ai': 0, 'score': 3},
            ])
        for scope in ['', 'week', 'TODAY']:
            response = await self.client.get('/leaderboard/games', params={'scope': scope})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {'detail': 'scope must be all or today'})

    async def test_finished_rooms_distinct_participants_and_winners(self):
        await self.room('midnight', winner=2)
        await self.room('now', '2026-10-01 00:30:00', winner=2)
        await self.room('yesterday', '2026-09-30 23:59:59', winner=1)
        await self.room('future', '2026-10-01 00:30:01', winner=1)
        await self.room('tomorrow', '2026-10-02 00:00:00', winner=1)
        await self.room('playing', winner=1, status='playing')
        await self.room('null', None, winner=1)
        await self.room('guest', winner=4)
        for kind in ['ask', 'ask', 'guess']:
            await self.log('midnight', 1, kind, at='2026-09-30 12:00:00')
        await self.log('now', 1, 'guess')
        await self.log('midnight', 2, 'guess')
        await self.log('midnight', 3, 'hint')
        await self.log('now', 3, 'chat')
        await self.log('guest', 4)
        for rid in ['yesterday', 'future', 'tomorrow', 'playing', 'null']:
            await self.log(rid, 1)
        self.assertEqual(await self.scores('games'), [(1, 2), (2, 1)])
        self.assertEqual(await self.scores('wins'), [(2, 2)])
        self.assertEqual(await self.rows('unknown'), await self.rows('games'))

    async def test_asks_boundaries_judgments_and_no_cumulative_writes(self):
        await self.room('playing', None, status='playing')
        for pid, judgment, at in [
            (1, 'yes', '2026-10-01 00:00:00'), (1, 'no', '2026-10-01 00:30:00'),
            (1, 'yes', '2026-09-30 23:59:59'), (1, 'no', '2026-09-30 17:00:00'),
            (1, 'yes', '2026-10-01 00:30:01'), (1, 'no', '2026-10-02 00:00:00'),
            (2, 'yes', '2026-10-01 00:10:00'), (2, 'unknown', '2026-10-01 00:10:00'),
            (3, 'no', '2026-10-01 00:10:00'), (4, 'yes', '2026-10-01 00:00:00'),
            (4, 'no', '2026-10-01 00:00:00'),
        ]:
            await self.log('playing', pid, judgment=judgment, at=at)
        for kind in ['guess', 'hint', 'chat']:
            await self.log('playing', 1, kind)
        before = await database.fetch_all('SELECT * FROM players ORDER BY id')
        self.assertEqual(await self.scores('asks'), [(1, 2), (2, 2), (3, 1)])
        self.assertEqual(await self.scores('yes'), [(1, 1), (2, 1)])
        self.assertEqual(await self.scores('no'), [(1, 1), (3, 1)])
        self.assertEqual((await self.rows('no'))[1]['username'], '玩家3')
        self.assertEqual(before, await database.fetch_all('SELECT * FROM players ORDER BY id'))
        # Aggregation is live, not cached or materialized into players.
        await self.log('playing', 2)
        self.assertEqual(await self.scores('asks'), [(2, 3), (1, 2), (3, 1)])
        self.assertEqual(await self.rows('asks', 'all'), [])

    async def test_empty_and_top_twenty_for_every_metric(self):
        for metric in ['games', 'wins', 'asks', 'yes', 'no']:
            self.assertEqual(await self.rows(metric), [])
        for pid in range(10, 35):
            await database.execute('INSERT INTO players(id, username) VALUES (?, ?)', (pid, f'p{pid}'))
            await self.room(str(pid), winner=pid)
            await self.log(str(pid), pid, judgment='yes')
            await self.log(str(pid), pid, judgment='no')
        for metric in ['games', 'wins', 'asks', 'yes', 'no']:
            rows = await self.rows(metric)
            self.assertEqual([r['id'] for r in rows], list(range(10, 30)))
            self.assertTrue(all(r['score'] > 0 for r in rows))
