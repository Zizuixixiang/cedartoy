"""Verified open scales; data files are the reviewed, opt-in publication registry."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from cedar_backend.question_volume import guide_volume

from .engine import ResearchScaleEngine, DISCLAIMER

DB_PATH = os.getenv('SESSIONS_DB', str(Path(__file__).resolve().parents[1] / 'data' / 'sessions.db'))
SCALES = {}
for path in sorted((Path(__file__).parent / 'data').glob('*.json')):
    spec = json.loads(path.read_text(encoding='utf-8'))
    if spec.get('blocked'):
        continue
    engine = ResearchScaleEngine(spec, lambda: DB_PATH)
    SCALES[spec['game']] = engine
MCP_GAMES = frozenset(game for game, engine in SCALES.items() if not engine.spec.get('human_only'))


def human_configs():
    return {game: {
        'handler': SimpleNamespace(PLAYER_ID_RE=engine.PLAYER_ID_RE),
        'title': engine.title, 'subtitle': 'OPEN SCALES / SELF REFLECTION',
        'source': engine.attribution, 'disclaimer': engine.notice,
        'research': True, 'total': len(engine.items),
    } for game, engine in SCALES.items()}


def guide(game):
    engine = SCALES[game]
    if game in MCP_GAMES:
        from .guides import guide as compact_guide
        return compact_guide(engine)
    # Keep the first shared warning, removing only known repetitions in Guide copy.
    notice = engine.spec['notice']
    if not engine.spec.get('human_only'):
        for repeated in (
            '小机作答只作娱乐演绎，不具有人类量表效度。',
            '小机作答仅作娱乐演绎，不具有人类量表效度。',
            '小机作答仅为娱乐演绎，不具有人类量表效度。',
        ):
            notice = notice.replace(repeated, '')
        notice = notice.replace('仅供娱乐/自我了解，不作诊断，不使用临床分界值。', '不使用临床分界值。')
        notice = notice.replace('仅供娱乐和自我了解，不作诊断；不设临床分界值。', '不设临床分界值。')
        notice = notice.replace('不作诊断，不设临床阈值或常模。', '不设临床阈值或常模。')
        notice = DISCLAIMER + ' ' + notice
    else:
        notice = engine.notice
    return f'''# {game} · {engine.title}
{notice}
{guide_volume(game) if game in MCP_GAMES else ''}
{engine.attribution}
{engine.spec['instruction']}
调用 play(game="{game}", action="start", params={{"mode":"full"}}) 逐题，或 mode="full_fast" 一次提交全题；两者同卷同分。
start 恢复已有记录；重测传当前 session_id 并加 restart=true, confirm=true。status / get_result 恢复进度或查看结果。
逐题 action="answer", params={{"session_id":"返回值","expected_index":0,"answer":1}}，expected_index 为返回的 progress（从0开始）。
批量 action="answer_batch", params={{"session_id":"返回值","answers":[...]}}，长度须为 {len(engine.items)}，按返回 options.value 作答。同一会话相同答案重试幂等，过期/冲突请求拒绝。
账号身份由鉴权确定；slot=1..5 隔离。游客进度24小时、结果48小时保留。不提供他人答案/结果或 compare。
选项：{json.dumps(engine.spec['options'], ensure_ascii=False)}
计分：{engine.scoring_note}'''
