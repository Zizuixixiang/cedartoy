"""Private, append-only completion accounting. No prompts, keys or user IDs.

API_COST_DB is shared by the main admin process and turtle-soup workers. Writes
run on a bounded daemon queue; WAL and short transactions isolate game traffic.
Price versions are selected as of send time, even if a queued event lands later.
This is best-effort telemetry: abrupt process death or a full queue can lose
unflushed events. Storage/queue failures emit privacy-safe operational warnings.
"""
import atexit
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

DB_PATH = Path(os.getenv('API_COST_DB', Path(__file__).resolve().parents[2] / 'data/api_costs.db'))
BEIJING = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)
PURPOSES = {'soup': '海龟汤', 'npc': 'NPC', 'tarot': '塔罗', 'test': '渠道测试'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS prices (
 id INTEGER PRIMARY KEY, config_id INTEGER NOT NULL, model TEXT NOT NULL,
 effective REAL NOT NULL, source TEXT NOT NULL, rule TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_match ON prices(config_id,model,effective);
CREATE UNIQUE INDEX IF NOT EXISTS prices_default ON prices(config_id,model) WHERE source='default';
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, started REAL NOT NULL, config_id INTEGER NOT NULL,
 name TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL,
 status TEXT NOT NULL, http_status INTEGER,
 input_tokens INTEGER, output_tokens INTEGER, cache_tokens INTEGER, reasoning_tokens INTEGER,
 price_snapshot TEXT, cost TEXT, cost_state TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_time ON events(started);
'''


def connect(path=None):
    db = sqlite3.connect(path or DB_PATH, timeout=0.05)
    db.row_factory = sqlite3.Row
    return db


def initialize(path=None):
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)


def validate_rule(body):
    if not isinstance(body, dict):
        raise ValueError('价格规则必须为对象')
    cid, model = body.get('config_id'), body.get('model')
    if isinstance(cid, bool) or not isinstance(cid, int) or cid < 1:
        raise ValueError('渠道无效')
    if (not isinstance(model, str) or not 1 <= len(model) <= 200
            or model != model.strip() or any(ord(c) < 32 or ord(c) == 127 for c in model)):
        raise ValueError('模型名须为 1–200 字符，不能有首尾空白或控制字符')
    mode = body.get('mode')
    if mode not in ('request', 'tokens'):
        raise ValueError('请选择按次或按量计费')
    rule = {'mode': mode}
    reference = body.get('reference_only', False)
    if not isinstance(reference, bool) or (reference and mode != 'tokens'):
        raise ValueError('参考估算标记须为布尔值，且仅用于按量计费')
    if reference:
        rule['reference_only'] = True
    for key in (('per_request',) if mode == 'request' else ('input', 'output', 'cache')):
        value = body.get(key)
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError('价格须为非负小数')
        value = str(value)
        if not re.fullmatch(r'\d{1,9}(?:\.\d{1,12})?', value):
            raise ValueError('价格须为非负小数，最多 9 位整数、12 位小数')
        rule[key] = format(Decimal(value), 'f')
    return cid, model, rule


def default_rule(cfg):
    """Only the verified channel IDs AND exact known models may be seeded."""
    cid, model, name = cfg['id'], cfg['model'], str(cfg.get('name') or '')
    if cid in (11, 17, 24) and name.startswith('小鸡农场') and model == '[反重力][自营][次][假流] gemini-3.1-flash-lite':
        return {'mode': 'request', 'per_request': '0.001'}
    if cid in (31, 32, 33) and name.startswith('巧克力') and model == 'gemini-3.1-flash-lite':
        return {'mode': 'tokens', 'input': '0.10', 'output': '0.60', 'cache': '0.01'}
    if cid in (28, 29, 30) and name.startswith('沉默') and model == '[anti量]gemini-3.8-flash':
        return {'mode': 'tokens', 'input': '0.225', 'output': '1.125', 'cache': '0.0225'}


def seed(db, configs):
    for cfg in configs:
        rule = default_rule(cfg)
        if rule:
            db.execute('''INSERT OR IGNORE INTO prices(config_id,model,effective,source,rule)
                SELECT ?,?,0,'default',? WHERE NOT EXISTS
                (SELECT 1 FROM prices WHERE config_id=? AND model=?)''',
                (cfg['id'], cfg['model'], json.dumps(rule), cfg['id'], cfg['model']))


def _provider_name(cfg):
    # Strip only known business suffixes, never arbitrary merchant name parts.
    return re.sub(r'\s*[·•|｜-]\s*(?:NPC(?:决策|发言)?|塔罗(?:解读)?|海龟汤(?:判题与提示|裁判|提示)?|裁判(?:与提示)?|提示)\s*$',
                  '', str(cfg.get('name') or '').strip(), flags=re.I)


def _provider_key(cfg):
    name = _provider_name(cfg)
    endpoint = str(cfg.get('api_url') or '').rstrip('/').removesuffix('/chat/completions')
    # Historical deleted IDs have no trustworthy endpoint; keep them separate.
    return (name, endpoint) if name and endpoint else ('config', cfg['id'])


def _members(cid, configs):
    cfg = next((c for c in configs if c['id'] == cid), None)
    return sorted({c['id'] for c in configs if _provider_key(c) == _provider_key(cfg)}) if cfg else [cid]


def _latest_prices(db):
    return {(r['config_id'], r['model']): (r['id'], json.loads(r['rule']))
            for r in db.execute('SELECT * FROM prices ORDER BY effective,id')}


def _rule_key(rule):
    if rule is None:
        return None
    fields = ('per_request',) if rule['mode'] == 'request' else ('input', 'output', 'cache')
    return (rule['mode'], bool(rule.get('reference_only')), *(Decimal(rule[k]) for k in fields))


def save_price(body, configs, path=None):
    cid, model, rule = validate_rule(body)
    initialize(path)
    with closing(connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        # Deleted channels need ledger evidence. An already saved manual rule
        # remains editable even if that channel was deleted before its first call.
        if (cid not in {c['id'] for c in configs}
                and not db.execute('SELECT 1 FROM events WHERE config_id=? LIMIT 1', (cid,)).fetchone()
                and not db.execute("SELECT 1 FROM prices WHERE config_id=? AND model=? AND source='user' LIMIT 1",
                                   (cid, model)).fetchone()):
            raise ValueError('渠道不存在且无历史记录，请刷新后重试')
        # Preserve default history before recording the first manual override.
        seed(db, configs)
        ids = [cid]
        if 'config_ids' in body:
            ids = body['config_ids']
            if (not isinstance(ids, list) or not ids
                    or any(type(i) is not int for i in ids) or len(ids) != len(set(ids))
                    or sorted(ids) != _members(cid, configs)):
                raise ValueError('渠道分组已变化，请刷新后重试')
            latest = _latest_prices(db)
            expected = body.get('price_versions')
            versions = {str(i): latest.get((i, model), (0, None))[0] for i in ids}
            if (not isinstance(expected, dict) or any(type(v) is not int for v in expected.values())
                    or expected != versions):
                raise ValueError('价格已变化，请刷新查看最新价格后再保存')
        # One transaction/effective time for all purposes. Never partly save a
        # group, and never rewrite any previous price version or event snapshot.
        effective = time.time()
        db.executemany('INSERT INTO prices(config_id,model,effective,source,rule) VALUES (?,?,?,\'user\',?)',
                       [(i, model, effective, json.dumps(rule)) for i in ids])
    return {'ok': True}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12 else None


def usage_counts(usage):
    usage = usage if isinstance(usage, dict) else {}
    inp, out = _count(usage.get('prompt_tokens')), _count(usage.get('completion_tokens'))
    details = usage.get('prompt_tokens_details')
    details = details if isinstance(details, dict) else {}
    completion = usage.get('completion_tokens_details')
    completion = completion if isinstance(completion, dict) else {}
    cache = _count(details.get('cached_tokens', usage.get('prompt_cache_hit_tokens', usage.get('cached_tokens', 0))))
    reasoning = _count(completion.get('reasoning_tokens', completion.get('thinking_tokens', usage.get('reasoning_tokens', usage.get('thinking_tokens', 0)))))
    if inp is not None and cache is not None and cache > inp:
        cache = None  # inconsistent usage must never produce a fabricated bill
    return inp, out, cache, reasoning


def calculate(rule, counts, success):
    if not rule or not success:
        return None, 'unknown'
    inp, out, cache, _reasoning = counts
    if rule['mode'] == 'request':
        # No usage still has a known tariff, but charging cannot be verified.
        return rule['per_request'], 'known' if inp is not None and out is not None else 'estimated'
    if inp is None or out is None or cache is None:
        return None, 'unknown'
    cost = ((inp-cache)*Decimal(rule['input']) + out*Decimal(rule['output'])
            + cache*Decimal(rule['cache'])) / Decimal(1_000_000)
    # completion_tokens already includes thinking/reasoning tokens.
    return format(cost, 'f'), 'estimated' if rule.get('reference_only') else 'known'


def make_event(cfg, pool, started, response=None):
    usage = None
    if response is not None:
        try:
            raw = response.json()
            usage = raw.get('usage') if isinstance(raw, dict) else None
        except (ValueError, TypeError):
            pass
    return {'id': uuid.uuid4().hex, 'started': started, 'config_id': int(cfg.get('id') or 0),
            'name': str(cfg.get('name') or '')[:200], 'model': str(cfg.get('model') or ''),
            'purpose': 'npc' if pool.startswith('npc') else pool if pool in ('tarot', 'test') else 'soup',
            'status': 'success' if response is not None and 200 <= response.status_code < 300
                      and not response.extensions.get('completion_invalid') else 'failed',
            'http_status': response.status_code if response is not None else None,
            'counts': usage_counts(usage)}


def write_event(event, path=None):
    with closing(connect(path)) as db, db:
        seed(db, [{'id': event['config_id'], 'name': event['name'], 'model': event['model']}])
        price = db.execute('''SELECT rule FROM prices WHERE config_id=? AND model=? AND effective<=?
            ORDER BY effective DESC,id DESC LIMIT 1''',
            (event['config_id'], event['model'], event['started'])).fetchone()
        snapshot = price['rule'] if price else None
        # A rejected task answer can still consume billable upstream tokens.
        cost, state = calculate(json.loads(snapshot) if snapshot else None, event['counts'],
                                event['http_status'] is not None and 200 <= event['http_status'] < 300)
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   tuple(event[k] for k in ('id','started','config_id','name','model','purpose','status','http_status'))
                   + tuple(event['counts']) + (snapshot, cost, state))


_pending = queue.Queue(maxsize=10000)
_worker_lock = threading.Lock()
_worker_thread = None


def _worker():
    ready = False
    while True:
        event = _pending.get()
        for attempt in range(60):
            try:
                if not ready:
                    initialize()
                    ready = True
                write_event(event)
                break
            except Exception:
                # Never log exception contents: upstream content/keys are not telemetry.
                if attempt == 0:
                    logger.warning('API cost storage unavailable; background retry pending')
                time.sleep(0.5)
        else:
            logger.error('API cost event lost after storage retries; billing statistics incomplete')
        _pending.task_done()


def record(cfg, pool, started, response=None):
    global _worker_thread
    try:
        event = make_event(cfg, pool, started, response)
        with _worker_lock:
            if _worker_thread is None or not _worker_thread.is_alive():
                _worker_thread = threading.Thread(target=_worker, name='api-cost-writer', daemon=True)
                _worker_thread.start()
        _pending.put_nowait(event)
    except Exception:
        logger.error('API cost event not queued; billing statistics incomplete')


def _drain():
    deadline = time.monotonic() + 2
    while _pending.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)


atexit.register(_drain)


def read_configs(account_path):
    with closing(sqlite3.connect(Path(account_path).resolve().as_uri()+'?mode=ro', uri=True, timeout=0.05)) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute('SELECT id,name,api_url,model,purpose,enabled FROM judge_api_configs ORDER BY id')]


def _empty():
    return dict(requests=0, success=0, failed=0, input_tokens=0, output_tokens=0,
                cache_tokens=0, reasoning_tokens=0, unknown=0, estimated=0, missing_usage=0, cost='0')


def report(range_name, configs, path=None, now=None):
    if range_name not in ('today', '7d', '30d'):
        raise ValueError('统计范围仅支持今日、近7天、近30天')
    now = now or datetime.now(BEIJING)
    start = now.astimezone(BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days={'today': 0, '7d': 6, '30d': 29}[range_name])
    initialize(path)
    with closing(connect(path)) as db:
        with db:
            seed(db, configs)
        db.execute('BEGIN')  # Read totals, variants and versions from one snapshot.
        class DecimalSum:
            def __init__(self): self.total = Decimal(0)
            def step(self, value): self.total += Decimal(value or '0')
            def finalize(self): return format(self.total, 'f')
        db.create_aggregate('decimal_sum', 1, DecimalSum)
        rows = [dict(r) for r in db.execute('''SELECT config_id,model,purpose,MAX(name) AS name,
            COUNT(*) AS requests,SUM(status='success') AS success,SUM(status='failed') AS failed,
            COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,
            COALESCE(SUM(cache_tokens),0) AS cache_tokens,COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
            SUM(cost_state='unknown') AS unknown,SUM(cost_state='estimated') AS estimated,
            SUM(input_tokens IS NULL OR output_tokens IS NULL) AS missing_usage,decimal_sum(cost) AS cost
            FROM events WHERE started>=? AND started<=? GROUP BY config_id,model,purpose''',
            (start.timestamp(), now.timestamp()))]
        latest, manual = _latest_prices(db), set()
        for r in db.execute('SELECT * FROM prices ORDER BY effective,id'):
            key = (r['config_id'], r['model'])
            if r['source'] == 'user':
                manual.add(key)
        # The catalog is independent of the report window and current model.
        # Failed/retried attempts count as evidence too; unsaved test IDs do not.
        observed = {(r['config_id'], r['model']): r['name'] for r in db.execute('''
            SELECT config_id,model,MAX(name) AS name FROM events
            WHERE config_id>0 GROUP BY config_id,model''')}
        first = db.execute('SELECT MIN(started) FROM events').fetchone()[0]
    total = _empty()
    purposes = {key: dict(purpose=key, label=label, **_empty()) for key,label in PURPOSES.items()}
    config_map = {c['id']: c for c in configs}
    historical_names = {cid: name for (cid, _model), name in observed.items()}
    def identity(key, name=''):
        cid, model = key
        ids = _members(cid, configs)
        members = [config_map[i] for i in ids if i in config_map]
        variants = [dict(config_id=i,
                         label=PURPOSES.get({'both': 'soup', 'judge': 'soup', 'hint': 'soup',
                                            'npc_decision': 'npc', 'npc_speech': 'npc'}.get(
                                                config_map.get(i, {}).get('purpose'),
                                                config_map.get(i, {}).get('purpose')), '共用'),
                         price=latest.get((i, model), (0, None))[1]) for i in ids]
        conflict = len({_rule_key(v['price']) for v in variants}) > 1
        return dict(config_id=min(ids), config_ids=ids,
                    name=_provider_name(members[0]) if members else name or historical_names.get(cid, ''),
                    model=model, current=any(c['model'] == model for c in members),
                    enabled=any(c['enabled'] for c in members), deleted=not members,
                    price=None if conflict else variants[0]['price'], price_conflict=conflict,
                    price_variants=variants,
                    price_versions={str(i): latest.get((i, model), (0, None))[0] for i in ids})
    channels = {}
    # Keep each channel+exact-model split by business without changing the
    # existing aggregate totals or the append-only event/price snapshots.
    channel_purposes = tuple(PURPOSES)
    def add(target, row):
        for key in _empty():
            target[key] = format(Decimal(target[key])+Decimal(row[key]), 'f') if key == 'cost' else target[key]+row[key]
    for row in rows:
        add(total, row)
        add(purposes[row['purpose']], row)
        info = identity((row['config_id'], row['model']), row['name'])
        key = (info['config_id'], row['model'])
        if key not in channels:
            channels[key] = dict(
                **info, **_empty(),
                purposes={purpose: dict(purpose=purpose, label=PURPOSES[purpose], **_empty())
                          for purpose in channel_purposes},
            )
        add(channels[key], row)
        if row['purpose'] in channel_purposes:
            add(channels[key]['purposes'][row['purpose']], row)
    for channel in channels.values():
        channel['purposes'] = [v for k, v in channel['purposes'].items() if k != 'test' or v['requests']]
    prices = {}
    for key in sorted(observed.keys() | manual | {(c['id'], c['model']) for c in configs}):
        row = identity(key, observed.get(key, ''))
        prices[(row['config_id'], row['model'])] = row
    groups = {}
    for cfg in configs:
        ids = _members(cfg['id'], configs)
        groups.setdefault(min(ids), dict(id=min(ids), config_ids=ids, name=_provider_name(cfg),
                                        model=cfg['model'], enabled=any(config_map[i]['enabled'] for i in ids)))
    return {'range': range_name, 'since': first, 'total': total,
            'purposes': [v for k,v in purposes.items() if k != 'test' or v['requests']],
            'prices': list(prices.values()),
            'configs': list(groups.values()),
            'channels': sorted(channels.values(), key=lambda c: (-c['requests'], not c['enabled'], c['config_id'], c['model']))}
