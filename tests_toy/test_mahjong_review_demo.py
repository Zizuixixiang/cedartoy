"""Demo safety contract in a disposable SQLite DB, using Duel's native engine env."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "vendor/duel/.venv/bin/python"


class MahjongReviewDemoTests(unittest.TestCase):
    def test_atomic_fixture_http_projection_and_no_side_effects(self):
        code = r'''
import asyncio, json, sqlite3, tempfile
from pathlib import Path
from unittest.mock import patch
from scripts import create_mahjong_review_demo as demo
from app import database, framework, achievements
from app import main
import httpx

with tempfile.TemporaryDirectory(prefix="mahjong-demo-test-") as tmp:
    target = Path(tmp) / "duel.db"
    database.DB_PATH = target
    database.init_db()
    def snapshot():
        with database.connect() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {t: [tuple(r) for r in conn.execute('SELECT * FROM "'+t+'"')] for t in tables}
    before = snapshot()
    projected = demo.create_at(target, "MJTEST01", Path(tmp) / "backup.db")
    after = snapshot()
    changed = {t for t in before if before[t] != after[t]}
    assert changed == demo.ALLOWED_TABLES, changed
    assert len(after['rooms']) == 1 and len(after['room_participants']) == 4 and len(after['room_event_cursors']) == 4
    with sqlite3.connect(Path(tmp) / "backup.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
    assert projected['viewer']['player_id'] == '1'
    assert projected['preserved'] and projected['stake'] == 0
    assert projected['terminal_reason'] == demo.DEMO_REASON
    assert [p['participant_kind'] for p in projected['participants']] == ['human', 'system_npc', 'system_npc', 'system_npc']
    public = projected['board_state']
    result = public['game_result']
    assert result['total_fan'] >= 8 and result['total_fan'] == sum(f['fan'] for f in result['fans'])
    assert sum(t['id']==result['winning_tile']['id'] for t in public['terminal_hands']['1']) == 1
    assert len(public['terminal_hands']) == 4 and all(public['melds'].values())
    assert all(not t.get('back') for t in public['melds']['1'][0]['tiles'])
    try: demo.create_at(target, 'MJTEST02')
    except ValueError: pass
    else: raise AssertionError('duplicate allowed')
    assert snapshot() == after
    # A failure after partial inserts must roll back the whole operation.
    with database.connect() as conn:
        conn.execute("DELETE FROM rooms")
    clean = snapshot()
    with patch.object(framework, 'project_room_for_viewer', side_effect=ValueError('projection failure')):
        try: demo.create_at(target, 'MJFAIL01')
        except ValueError: pass
        else: raise AssertionError('failure ignored')
    assert snapshot() == clean
    demo.create_at(target, 'MJTEST01')
    # Guard refuses arbitrary side-effect triggers as well as direct writes.
    with database.connect() as conn:
        conn.execute("DELETE FROM rooms")
        conn.execute("CREATE TABLE accidental_side_effect(x)")
        conn.execute("CREATE TRIGGER test_bad_trigger AFTER INSERT ON rooms BEGIN INSERT INTO accidental_side_effect VALUES (1); END")
    guarded = snapshot()
    try: demo.create_at(target, 'MJBAD001')
    except sqlite3.DatabaseError: pass
    else: raise AssertionError('side effect allowed')
    assert snapshot() == guarded
    with database.connect() as conn:
        conn.execute("DROP TRIGGER test_bad_trigger")
    demo.create_at(target, 'MJTEST01')
    stable = snapshot()
    with database.write_transaction() as conn:
        assert achievements.backfill_authoritative_matches(conn) == 0
    assert snapshot() == stable

    async def check_http():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://demo.test') as client:
            for _ in range(2):
                response = await client.get('/api/rooms/MJTEST01', headers={'X-Duel-Human-Player':'1'})
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload['room']['board_state'] == public
                assert payload['timeline'] == []
                assert not payload['room']['private_state']['legal_actions']
            for headers, query in [({},''), ({'X-Duel-Human-Player':'2'},''), ({'X-Duel-Human-Player':'1'},'?player_id=2')]:
                response = await client.get('/api/rooms/MJTEST01'+query, headers=headers)
                assert response.status_code == 403, response.text
    asyncio.run(check_http())
    # Web may advance its own event cursor, but never rewards/notifications/wallets.
    read_after = snapshot()
    for table in stable:
        if table not in {'room_event_cursors'}:
            assert stable[table] == read_after[table], table
    assert framework.list_human_rooms('2') == []
    # Restart-time schema/backfill also cannot convert this fixture into a match.
    database.init_db()
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM achievement_matches').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0] == 0
    accounts = Path(tmp) / 'accounts.db'
    with sqlite3.connect(accounts) as conn:
        conn.execute('CREATE TABLE toy_users(id, username, is_ai, deleted_at)')
        conn.execute("INSERT INTO toy_users VALUES (1, '南杉', 0, NULL)")
    account_bytes = accounts.read_bytes()
    demo.verify_account(accounts)
    assert accounts.read_bytes() == account_bytes
    with sqlite3.connect(accounts) as conn:
        conn.execute("UPDATE toy_users SET username='另一个人'")
    try: demo.verify_account(accounts)
    except ValueError: pass
    else: raise AssertionError('identity mismatch allowed')
print('fixture, rollback, trigger guard, backup, projection, HTTP ownership, restart backfill: OK')
'''
        completed = subprocess.run([str(PYTHON), "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_real_dom_review_and_modal_compatibility(self):
        completed = subprocess.run(["node", "scripts/check_mahjong_review_dom.cjs"],
                                   cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_cli_requires_explicit_apply_and_identity(self):
        for args in ([], ["--username", "南杉", "--account-id", "2"],
                     ["--username", "南杉", "--account-id", "1", "--apply"],
                     ["--username", "南杉", "--account-id", "1", "--duel-db", "/not/a/database"]):
            completed = subprocess.run([str(PYTHON), "scripts/create_mahjong_review_demo.py", *args],
                                       cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2, completed.stderr)


if __name__ == "__main__":
    unittest.main()
