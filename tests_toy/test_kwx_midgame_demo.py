"""Human-only frozen KaWuXing fixture safety, isolated in Duel's engine env."""
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / 'vendor/duel/.venv/bin/python'


class KwxMidgameDemoTests(unittest.TestCase):
    def test_safety_and_real_http_paths(self):
        completed = subprocess.run([str(PYTHON), '-c', r'''
import asyncio, copy, json, sqlite3, tempfile
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from scripts import create_kwx_midgame_demo as demo
from app import database, framework, main
from app.desktop_demo import is_desktop_demo, is_kwx_review_demo, desktop_demo_sql, DEMO_ROOMS
from app.npc_controller import run_current_npc_turn
from app.npc_runtime import list_active_npc_turn_room_ids
from app.npc_scheduler import is_system_npc_turn
import httpx

with tempfile.TemporaryDirectory(prefix='kwx-demo-test-') as tmp:
    path = Path(tmp) / 'duel.db'
    database.DB_PATH = path
    database.init_db()
    # Existing human account wallet is independent of the fixture; whoami ensures it.
    main.get_wallet('human','1')
    rid = demo.KWX_DEMO_ROOM
    def snapshot():
        with closing(database.connect()) as conn:
            return {r[0]: [tuple(row) for row in conn.execute('SELECT * FROM "'+r[0]+'"')]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    before = snapshot()
    outcome, projected = demo.create_at(path, Path(tmp) / 'backup.db')
    after = snapshot()
    assert outcome == 'created'
    assert {k for k in before if before[k] != after[k]} == demo.ALLOWED_TABLES
    assert len(after['rooms']) == 1 and len(after['room_participants']) == 3
    assert len(after['room_event_cursors']) == 3
    with closing(sqlite3.connect(Path(tmp) / 'backup.db')) as conn:
        assert conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0] == 0
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert demo.create_at(path)[0] == 'already present (unchanged)'
    assert snapshot() == after
    raw = framework.get_room(rid, read_only=True)
    assert raw['status'] == 'playing' and raw['preserved'] and raw['stake'] == 0
    assert is_kwx_review_demo(raw)
    assert not is_kwx_review_demo({**raw, 'room_id':'OTHER001'})
    assert len(DEMO_ROOMS) == 7 and 'ka_wu_xing' not in DEMO_ROOMS
    assert [len(v) for v in raw['board_state']['discards'].values()] == [11,10,10]
    assert raw['board_state']['game_result'] is None
    assert raw['board_state'] == demo.build_demo()['board_state']
    for p in raw['participants']:
        view = framework.project_room_for_viewer(raw, p['player_id'])
        assert 'hands' not in view['board_state'] and 'wall' not in view['board_state']
        assert 'terminal_hands' not in view['board_state']
        assert {t['id'] for t in view['private_state']['hand']} == {t['id'] for t in raw['board_state']['hands'][p['player_id']]}
        assert view['private_state']['legal_actions'] == []
    assert projected['viewer']['player_id'] == '1' and projected['read_only']
    # Python + correlated SQL guard must agree, including wrong-owner collisions.
    with closing(database.connect()) as conn:
        def sql_guard():
            return bool(conn.execute(f'SELECT {desktop_demo_sql("r.")} FROM rooms r WHERE room_id=?', (rid,)).fetchone()[0])
        assert sql_guard()
        mutations = [('stake', 1), ('game_type', 'mahjong'), ('initiator_player_id', '7874'),
                     ('board_state', json.dumps({**raw['board_state'], '_demo': {'owner':'7874', 'batch':demo.KWX_DEMO_BATCH}}))]
        for key,value in mutations:
            conn.execute('SAVEPOINT mismatch')
            conn.execute(f'UPDATE rooms SET {key}=? WHERE room_id=?', (value,rid))
            row = conn.execute('SELECT * FROM rooms').fetchone()
            room = database.decode_room(row,conn)
            assert not is_desktop_demo(room)
            assert not conn.execute(f'SELECT {desktop_demo_sql()} FROM rooms').fetchone()[0]
            conn.execute('ROLLBACK TO mismatch');conn.execute('RELEASE mismatch')
        for key,value in [('participant_kind','bound_machine'),('role','ai'),('active',0),('join_status','left')]:
            conn.execute('SAVEPOINT mismatch')
            conn.execute(f'UPDATE room_participants SET {key}=? WHERE player_id=\'1\'',(value,))
            assert not sql_guard()
            assert not is_kwx_review_demo(database.decode_room(conn.execute('SELECT * FROM rooms').fetchone(),conn))
            conn.execute('ROLLBACK TO mismatch');conn.execute('RELEASE mismatch')
    assert snapshot() == after
    # Stale maintenance and the actual NPC candidate query may not advance/archive it.
    with database.write_transaction() as conn:
        conn.execute("UPDATE rooms SET last_move_at='2001-01-01T00:00:00+00:00'")
    frozen = snapshot()
    assert list_active_npc_turn_room_ids() == []
    assert not is_system_npc_turn(raw)
    assert framework.get_room(rid)['status'] == 'playing'
    for call in [lambda:framework.play_move(rid,'human','1',{'action':'act','action_id':'discard:B1-1'}),
                 lambda:framework.post_message(rid,'human','1','no message'),
                 lambda:framework.resign(rid,'human','1'),
                 lambda:framework.leave_room(rid,'human','1')]:
        try: call()
        except framework.DuelError as exc: assert '演示房' in str(exc)
        else: raise AssertionError('write accepted')
    async def checks():
        with patch.object(main, '_schedule_current_system_npc', side_effect=AssertionError('read scheduled work')):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://demo.test') as client:
                headers={'X-Duel-Human-Player':'1','X-Duel-Human-Name':'%E5%8D%97%E6%9D%89'}
                listed=await client.get('/api/whoami',headers=headers)
                assert listed.status_code == 200, listed.text
                assert [r['room_id'] for r in listed.json()['rooms']] == [rid]
                assert '演示/只读' in listed.json()['rooms'][0]['game_name']
                # whoami's existing wallet ensure may consume an autoincrement ID.
                # Room reads and rejected actions below must be completely unchanged.
                read_baseline = snapshot()
                for suffix in ['', '?wait=true&after_revision='+str(raw['revision'])]:
                    response=await client.get('/api/rooms/'+rid+suffix,headers=headers)
                    assert response.status_code == 200,response.text
                    data=response.json()
                    assert data['room']['viewer']['player_id']=='1'
                    assert data['room']['read_only'] and data['timeline']==[]
                    assert data['room']['board_state']==projected['board_state']
                for headers2,suffix in [({},''),({'X-Duel-Human-Player':'2'},''),(headers,'?player_id=7874')]:
                    response=await client.get('/api/rooms/'+rid+suffix,headers=headers2)
                    assert response.status_code==403,response.text
                for endpoint,body in [('move',dict(player_id='1',revision=raw['revision'],move={'action':'act','action_id':'discard:B1-1'})),
                                      ('messages',dict(player_id='1',message='no message')),('resign',dict(player_id='1')),('leave',dict(player_id='1'))]:
                    response=await client.post('/api/rooms/'+rid+'/'+endpoint,headers=headers,json=body)
                    assert response.status_code==409,response.text
                    assert '演示房' in response.text
                for player in ['1','7874',raw['current_player_id']]:
                    response=await client.post('/mcp/play',json=dict(action='move',player_id=player,room_id=rid,move={'action':'act','action_id':'discard:B1-1'}))
                    assert response.status_code in (403,409),response.text
                assert snapshot() == read_baseline
        try: await run_current_npc_turn(rid)
        except framework.DuelError as exc: assert '演示房' in str(exc)
        else: raise AssertionError('NPC acted')
    asyncio.run(checks())
    assert {k:v for k,v in snapshot().items() if k!='sqlite_sequence'} == {k:v for k,v in frozen.items() if k!='sqlite_sequence'}
    assert framework.list_human_rooms('2') == []
    # Refuse any changed fixture without partial writes.
    with database.write_transaction() as conn:
        conn.execute('UPDATE rooms SET stake=1 WHERE room_id=?',(rid,))
    drifted=snapshot()
    try: demo.create_at(path)
    except ValueError: pass
    else: raise AssertionError('collision accepted')
    assert snapshot()==drifted
    with database.write_transaction() as conn: conn.execute('DELETE FROM rooms')
    empty=snapshot()
    with patch.object(framework,'project_room_for_viewer',side_effect=ValueError('projection failed')):
        try: demo.create_at(path)
        except ValueError: pass
        else: raise AssertionError('failure ignored')
    assert snapshot()==empty
    with database.write_transaction() as conn:
        conn.execute('CREATE TABLE unwanted(x)')
        conn.execute('CREATE TRIGGER bad AFTER INSERT ON rooms BEGIN INSERT INTO unwanted VALUES (1); END')
    guarded=snapshot()
    try: demo.create_at(path)
    except sqlite3.DatabaseError: pass
    else: raise AssertionError('trigger accepted')
    assert snapshot()==guarded
    with database.write_transaction() as conn: conn.execute('DROP TRIGGER bad')
    demo.create_at(path)
    database.init_db()
    assert snapshot()['achievement_matches'] == []
    assert snapshot()['notifications'] == []
    accounts=Path(tmp)/'accounts.db'
    with sqlite3.connect(accounts) as conn:
        conn.execute('CREATE TABLE toy_users(id,username,is_ai,deleted_at)')
        conn.execute("INSERT INTO toy_users VALUES(1,'南杉',0,NULL)")
    saved=accounts.read_bytes();demo.verify_account(accounts);assert accounts.read_bytes()==saved
    with sqlite3.connect(accounts) as conn: conn.execute('UPDATE toy_users SET is_ai=1')
    try: demo.verify_account(accounts)
    except ValueError: pass
    else: raise AssertionError('wrong account accepted')
print('fixture + SQL/Python guards + Web/MCP + NPC + isolation + rollback + backup: passed')
'''], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_cli_defaults_and_apply_requirements(self):
        run = lambda args: subprocess.run([str(PYTHON), 'scripts/create_kwx_midgame_demo.py', *args], cwd=ROOT, capture_output=True, text=True)
        result = run([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"production_link_available": false', result.stdout)
        for args in [['--apply'], ['--duel-db', '/not/a/database'], ['--apply','--duel-db','/tmp/wrong','--account-db','/tmp/wrong2','--backup','/tmp/wrong3']]:
            self.assertEqual(run(args).returncode, 2)

    def test_dom(self):
        result = subprocess.run(['node','scripts/check_kwx_midgame_dom.cjs'], cwd=ROOT, capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
