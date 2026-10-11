"""Explicit, per-result AIDep consent. Never serialize a private result receipt."""
import hashlib
import json
import math
from contextlib import closing
from research_scales.interpretation import build_interpretation, format_interpretation


def _init(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS aidep_result_shares (
        human_id INTEGER PRIMARY KEY,
        ai_id INTEGER NOT NULL,
        binding_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        result_version TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
    )''')


def _result(conn, human_id, engine):
    row = conn.execute("SELECT result_detail, completed_at FROM test_results WHERE player_id=? AND game='aidep'",
                       (str(human_id),)).fetchone()
    active = conn.execute("SELECT 1 FROM test_sessions WHERE player_id=? AND game='aidep'", (str(human_id),)).fetchone()
    if not row or active:
        return None
    try:
        detail = json.loads(row[0])
        answers, scores, sid = detail['answers'], detail['scores'], detail['session_id']
        if not isinstance(sid, str) or not sid or not isinstance(answers, list) or len(answers) != 22:
            return None
        if any(type(v) is not int or not 1 <= v <= 5 for v in answers):
            return None
        # A strict numeric allowlist prevents arbitrary stored fields/text from escaping.
        safe = {key: scores[key] for key in engine.result_dimensions}
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 1 <= v <= 5 for v in safe.values()):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    version = hashlib.sha256(json.dumps([row[0], row[1]], ensure_ascii=False).encode()).hexdigest()
    return sid, version, safe


def _bindings(accounts, human_id):
    return accounts.execute('''SELECT b.id, a.id, a.username
        FROM user_bindings b
        JOIN toy_users h ON h.id=b.human_user_id
        JOIN toy_users a ON a.id=b.ai_user_id
        WHERE h.id=? AND h.is_ai=0 AND a.is_ai=1
          AND h.deleted_at IS NULL AND a.deleted_at IS NULL
          AND h.deletion_requested_at_epoch IS NULL AND a.deletion_requested_at_epoch IS NULL
        ORDER BY a.id''', (human_id,)).fetchall()


def access(engine, account_connect, user, error, *, change=None, human_id=None):
    """user must come from token authentication, never request parameters.

    Hold an account read transaction through the session transaction. All result
    and consent read/modify/write operations use the same sessions connection.
    Binding generation IDs also prevent consent reviving after unbind/rebind.
    """
    shared = human_id is not None
    if shared:
        if not user.get('is_ai'):
            raise error(-32003, '仅已授权的绑定小机可读取共享结果')
        if type(human_id) is not int or not 0 < human_id < 2**63:
            raise error(-32003, '无权查看此共享结果')
    else:
        if user.get('is_ai'):
            raise error(-32003, '只有人类账号可以设置结果分享')
        human_id = int(user['id'])

    with closing(engine._connect()) as conn, closing(account_connect()) as accounts:
        engine._init_db(conn)
        _init(conn)
        conn.execute('BEGIN IMMEDIATE')
        accounts.execute('BEGIN')
        bindings = _bindings(accounts, human_id)
        grant = conn.execute('SELECT ai_id, binding_id, session_id, result_version, enabled FROM aidep_result_shares WHERE human_id=?', (human_id,)).fetchone()
        linked = bool(grant and grant[4] and any((b[1], b[0]) == (grant[0], grant[1]) for b in bindings))
        if shared and (not linked or grant[0] != int(user['id'])):
            raise error(-32003, '无权查看此共享结果')
        result = _result(conn, human_id, engine)
        valid = bool(linked and result and (grant[2], grant[3]) == result[:2])
        if shared:
            if not valid or grant[0] != int(user['id']):
                raise error(-32003, '无权查看此共享结果')
            # No receipt, answer, question, username, or other account data here.
            report = build_interpretation(engine.spec, result[2])
            ranges = engine.score_ranges()
            readable = '\n'.join(f'{engine.result_dimensions[key]}：{engine.display_score(value, ranges[key])}'
                                 for key, value in result[2].items())
            return {'ok': True, 'game': 'aidep', 'human_id': human_id,
                    'scores': result[2], 'score_ranges': ranges, 'dimensions': dict(engine.result_dimensions),
                    'explanation': '总体与四维均分范围为1–5；越高表示相应依赖表现越多，不代表诊断或临床分级。',
                    'notice': engine.notice,
                    'interpretation': report, 'result': readable + '\n\n' + format_interpretation(report)}

        if change is not None:
            if type(change.get('enabled')) is not bool:
                raise error(-32602, 'enabled 须为布尔值')
            if not change['enabled']:
                conn.execute('UPDATE aidep_result_shares SET enabled=0 WHERE human_id=?', (human_id,))
                valid = False
            else:
                if not result or (change.get('session_id'), change.get('result_version')) != result[:2]:
                    raise error(-32602, '结果已变化，请刷新结果页后重新授权')
                ai_id = change.get('ai_id')
                binding = next((b for b in bindings if type(ai_id) is int and b[1] == ai_id), None)
                if binding is None:
                    raise error(-32003, '只能选择当前绑定的小机')
                conn.execute('''INSERT OR REPLACE INTO aidep_result_shares
                    (human_id, ai_id, binding_id, session_id, result_version, enabled)
                    VALUES (?, ?, ?, ?, ?, 1)''', (human_id, ai_id, binding[0], *result[:2]))
                grant = (ai_id, binding[0], *result[:2], 1)
                valid = True
        conn.commit()
        if not result:
            raise error(-32004, '没有可分享的完整结果')
        return {'ok': True, 'session_id': result[0], 'result_version': result[1],
                'enabled': valid, 'ai_id': grant[0] if valid else None,
                'bindings': [{'ai_id': b[1], 'username': b[2]} for b in bindings],
                'example': f"play(game='aidep', action='shared_result', params={{human_id: {human_id}}})" if valid else ''}
