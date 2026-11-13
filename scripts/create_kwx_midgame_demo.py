#!/usr/bin/env python3
"""One frozen human midgame fixture, independent of the terminal Mahjong demo.

Default: generate and validate in an automatically removed temporary DB.
Deploy/restart Duel's read-only guards BEFORE the separate operator --apply.
No service calls, live game creation, messages, account/token changes or rewards.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor/duel'))
from app import database, framework
from app.desktop_demo import KWX_DEMO_ROOM, KWX_DEMO_OWNER, KWX_DEMO_BATCH, is_kwx_review_demo
from app.games.ka_wu_xing import KaWuXing

PRODUCTION_DB = ROOT / 'vendor/duel/data/duel.db'
ACCOUNT_DB = ROOT / 'turtle-soup/backend/turtle_soup.db'
ALLOWED_TABLES = {'rooms', 'room_participants', 'room_event_cursors'}


def build_demo():
    players = [dict(
        player_id=KWX_DEMO_OWNER if i == 0 else f'npc:kwx-review:{KWX_DEMO_ROOM}:{i}',
        display_name='南杉' if i == 0 else f'演示陪练{i}',
        role='human' if i == 0 else 'ai',
        participant_kind='human' if i == 0 else 'system_npc',
        npc_persona_id=None, seat_index=i, token=f'seat-{i}',
        join_status='joined', activity_state='active', active=True,
    ) for i in range(3)]
    # Fixed local PRNG and legal engine actions only. Never use SystemRandom,
    # production create_room/play_move or any stored player's hand.
    game = KaWuXing(random.Random(20261114))
    state = game.configure(game.initialize_for_first_player(players, KWX_DEMO_OWNER))
    current = KWX_DEMO_OWNER
    for revision in range(200):
        counts = [len(v) for v in state['discards'].values()]
        if min(counts) >= 10 and state['phase'] == 'discard' and current != KWX_DEMO_OWNER:
            break
        actor = next(p for p in players if p['player_id'] == current)
        actions = game.legal_actions_for(state, current)
        action = next(a for a in actions if a['kind'] in {'discard', 'pass'})
        applied = game.apply_action(state, {'action': 'act', 'action_id': action['action_id']}, actor)
        state = applied.state
        if state.get('game_result'):
            raise ValueError('Fixture unexpectedly ended')
        current = applied.next_player_id or current
    else:
        raise ValueError('Fixture did not reach midgame')
    tiles = [*state['wall'], *(t for h in state['hands'].values() for t in h),
             *(t for h in state['discards'].values() for t in h),
             *(t for ms in state['melds'].values() for m in ms for t in m['tiles'])]
    if (len(tiles) != 84 or len({t['id'] for t in tiles}) != 84
            or Counter((t['id'], t['code']) for t in tiles) != Counter((t['id'], t['code']) for t in game.build_wall())):
        raise ValueError('Physical tile conservation failed')
    if sorted(map(len, state['hands'].values())) != [13, 13, 14]:
        raise ValueError('Unexpected hand sizes')
    state['_demo'] = dict(batch=KWX_DEMO_BATCH, owner=KWX_DEMO_OWNER)
    state['last_action_note'] = '演示房 · 只读中盘，不计战绩、筹码或成就'
    return dict(room_id=KWX_DEMO_ROOM, game_type='ka_wu_xing', mode='human_first',
                board_state=state, turn='ai', current_player_id=current, revision=revision,
                status='playing', stake=0, initiator_player_id=KWX_DEMO_OWNER,
                preserved=True, participants=players)


def verify_account(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        row = conn.execute('SELECT id, username, is_ai, deleted_at FROM toy_users WHERE id=1').fetchone()
    if row != (1, '南杉', 0, None):
        raise ValueError('目标必须是未删除的人类账号 南杉 / 1')


def write_guard(action, table, column, db, trigger):
    if action == sqlite3.SQLITE_INSERT:
        return sqlite3.SQLITE_OK if table in ALLOWED_TABLES and trigger is None else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_UPDATE and table == 'room_participants' and trigger == 'trg_room_participants_infer_legacy_kind':
        return sqlite3.SQLITE_IGNORE
    if action in (sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def create_at(path, backup=None):
    """Insert one fixed ID atomically; exact rerun is a no-op, any drift refuses.

    Existing schema only. Authorizer also blocks side effects from DB triggers.
    Caller supplies an isolated DB in tests; CLI apply pins production paths.
    """
    fixture = build_demo()
    rid = KWX_DEMO_ROOM
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        try:
            row = conn.execute('SELECT * FROM rooms WHERE room_id=?', (rid,)).fetchone()
            if row:
                room = database.decode_room(row, conn)
                keys = ('game_type', 'mode', 'board_state', 'current_player_id', 'revision', 'status', 'stake', 'initiator_player_id', 'preserved')
                if not is_kwx_review_demo(room) or any(room[k] != fixture[k] for k in keys):
                    raise ValueError('ID collision or changed fixture; refusing overwrite')
                pkeys = tuple(fixture['participants'][0])
                if [[p[k] for k in pkeys] for p in room['participants']] != [[p[k] for k in pkeys] for p in fixture['participants']]:
                    raise ValueError('Changed participants; refusing overwrite')
                if room.get('timeout_takeover') or room.get('confirmation_required'):
                    raise ValueError('Changed room automation; refusing overwrite')
                outcome = 'already present (unchanged)'
            else:
                for table in ('achievement_matches', 'achievement_room_openings'):
                    if conn.execute(f'SELECT 1 FROM {table} WHERE room_id=?', (rid,)).fetchone():
                        raise ValueError('ID belongs to historical game data')
                if backup is not None:
                    with backup.open('xb'):
                        backup.chmod(0o600)
                    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
                        with closing(sqlite3.connect(backup)) as dest:
                            source.backup(dest)
                            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                raise ValueError('Backup failed integrity check')
                conn.set_authorizer(write_guard)
                before = conn.total_changes
                now = datetime.now(timezone.utc).isoformat()
                conn.execute('''INSERT INTO rooms
                    (room_id,game_type,mode,board_state,turn,current_player_id,revision,status,
                     stake,initiator_player_id,preserved,created_at,updated_at,last_move_at)
                    VALUES (?, 'ka_wu_xing', 'human_first', ?, 'ai', ?, ?, 'playing', 0, '1', 1, ?, ?, ?)''',
                    (rid, json.dumps(fixture['board_state'], ensure_ascii=False), fixture['current_player_id'], fixture['revision'], now, now, now))
                for p in fixture['participants']:
                    keys = tuple(p)
                    conn.execute(f'INSERT INTO room_participants (room_id,{",".join(keys)},joined_at) VALUES ({",".join("?" for _ in range(len(keys)+2))})',
                                 (rid, *(p[k] for k in keys), now))
                    conn.execute('INSERT INTO room_event_cursors (room_id,player_id,updated_at) VALUES (?,?,?)', (rid, p['player_id'], now))
                if conn.total_changes - before != 7:
                    raise ValueError('Expected exactly 1 room, 3 seats, 3 cursors')
                room = database.decode_room(conn.execute('SELECT * FROM rooms WHERE room_id=?', (rid,)).fetchone(), conn)
                outcome = 'created'
            projected = framework.project_room_for_viewer(framework._decorate(room), KWX_DEMO_OWNER)
            if not is_kwx_review_demo(room) or not projected.get('read_only') or projected['private_state']['legal_actions']:
                raise ValueError('Read-only guards not installed')
            if 'hands' in projected['board_state'] or 'wall' in projected['board_state']:
                raise ValueError('Private tiles leaked')
            if conn.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Foreign key check failed')
            conn.commit()
            return outcome, projected
        except BaseException:
            conn.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--duel-db', type=Path)
    parser.add_argument('--account-db', type=Path)
    parser.add_argument('--backup', type=Path, help='New consistent SQLite snapshot, required for apply')
    parser.add_argument('--projection', type=Path, help='Optional human-only JSON for local UI review')
    args = parser.parse_args(argv)
    if args.projection and args.projection.exists():
        parser.error('projection output must be a new file')
    if args.apply:
        if not all((args.duel_db, args.account_db, args.backup)):
            parser.error('--apply requires --duel-db, --account-db and --backup')
        if args.duel_db.resolve() != PRODUCTION_DB.resolve() or args.account_db.resolve() != ACCOUNT_DB.resolve():
            parser.error('apply requires the verified production Duel and account paths')
        if args.backup.exists() or args.backup.resolve() in (args.duel_db.resolve(), args.account_db.resolve()):
            parser.error('backup must be a new separate file')
        verify_account(args.account_db)
        outcome, projected = create_at(args.duel_db, args.backup)
    else:
        if any((args.duel_db, args.account_db, args.backup)):
            parser.error('dry-run accepts no production paths')
        with tempfile.TemporaryDirectory(prefix='kwx-midgame-demo-') as tmp:
            target = Path(tmp) / 'preview.db'
            with patch.object(database, 'DB_PATH', target):
                database.init_db()
                outcome, projected = create_at(target)
                assert create_at(target)[0] == 'already present (unchanged)'
    if args.projection:
        # Exclusive create prevents accidentally overwriting a DB or another artifact.
        with args.projection.open('x') as stream:
            json.dump(projected, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(mode='apply' if args.apply else 'dry-run', outcome=outcome,
                         room_id=KWX_DEMO_ROOM, owner='南杉 / human 1',
                         discards={p['display_name']: len(projected['board_state']['discards'][p['player_id']]) for p in projected['participants']},
                         route=f'/duel/?room={KWX_DEMO_ROOM}', production_link_available=args.apply), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
