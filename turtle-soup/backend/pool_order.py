"""Admin price previews and the persisted priorities consumed by judge routing."""
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from decimal import Decimal
from itertools import groupby
from pathlib import Path

import api_costs
import database


PURPOSES = {
    "soup": ("judge", "hint", "both", "all"),
    "npc": ("npc_decision", "npc_speech", "all", "npc"),
    "tarot": ("tarot",),
}
SETTING_PREFIX = "api_pool_order."
SETTING_KEYS = tuple(SETTING_PREFIX + pool for pool in PURPOSES)


class StalePreview(ValueError):
    pass


def priority_sql(pool):
    # Only internal pool names enter the query. One SQLite snapshot covers
    # both configuration and override, including new rows with no override.
    assert pool in PURPOSES
    return f"""COALESCE(json_extract((SELECT value FROM settings WHERE key='{SETTING_PREFIX}{pool}'),
                                    '$."' || id || '"'), priority, 0)"""


def _order_tier(rows, descending):
    """Pin unknowns in their slots. Equally priced neighbours share a layer."""
    def comparable(row):
        return row["cost"] is not None and not row["hold_reason"]

    priced = [r for r in rows if comparable(r)]
    if len(priced) < 2:
        return rows
    sorted_prices = iter(sorted(priced, key=lambda r: Decimal(r["cost"]), reverse=descending))
    proposed = [next(sorted_prices) if comparable(r) else r for r in rows]

    def layer_key(row):
        if not comparable(row):
            return ("fixed", row["before"])
        return ("price", Decimal(row["cost"]))

    # Reuse the old slots' priority numbers. Split a round-robin layer only
    # where distinct prices (or a pinned unknown) require distinct ranks.
    offset = 0
    last = None
    for _, members in groupby(proposed, key=layer_key):
        rank = rows[offset]["before"]
        if last is not None:
            rank = max(rank, last + 1)
        for row in members:
            row["after"] = rank
            offset += 1
        last = rank
    # SQLite INTEGER tops out at 2**63-1, even if the last legacy layer ties there.
    if last > 2**63 - 1:
        for rank, (_, members) in enumerate(groupby(proposed, key=layer_key), 1):
            for row in members:
                row["after"] = rank
    return sorted(proposed, key=lambda r: (r["after"], r["id"]))


def save_config_price(pool, config_id, body):
    # Hold the config writer lock through the separate ledger commit so a
    # rename, key rotation or reassignment cannot race the identity check.
    uri = Path(database.DB_PATH).resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        configs = [dict(r) for r in db.execute("SELECT * FROM judge_api_configs ORDER BY id")]
        by_id = {cfg["id"]: cfg for cfg in configs}
        cfg = by_id.get(config_id)
        if (not cfg or (cfg["purpose"] or "judge").strip().lower() not in PURPOSES[pool]
                or cfg["model"] != body.get("model")):
            raise api_costs.PriceConflict("模型配置已变化，请刷新预览后重试")
        ids = api_costs.provider_config_ids(config_id, configs)
        if body.get("group_revision") != api_costs.price_group_revision([by_id[i] for i in ids]):
            raise api_costs.PriceConflict("报价涉及的配置已变化，请刷新预览后重试")
        # Route supplies the identity. save_price validates the rule and locks
        # all (config_id, exact model) versions, just as in the main admin.
        return api_costs.save_price(dict(body, config_id=config_id, config_ids=body.get("config_ids")), configs)


def price_order(pool, direction, *, unavailable, cutoff=None, revision=None):
    """Preview in a read transaction. Rebuild and compare under the apply lock."""
    now = time.time()
    cutoff = now if cutoff is None else cutoff
    if revision is not None and not now - 3600 <= cutoff <= now:
        raise StalePreview("预览已过期，请重新预览")
    key = SETTING_PREFIX + pool
    mode = "ro" if revision is None else "rw"
    uri = Path(database.DB_PATH).resolve().as_uri() + "?mode=" + mode
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN" if revision is None else "BEGIN IMMEDIATE")
        configs = [dict(r) for r in db.execute("SELECT * FROM judge_api_configs ORDER BY id")]
        saved = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        overrides = json.loads(saved[0]) if saved else {}
        candidates = [r for r in configs if (r["purpose"] or "judge").strip().lower() in PURPOSES[pool]]
        usage, estimates = api_costs.pool_estimates(pool, configs, candidates, cutoff)
        held = unavailable(configs)
        tiers = {}
        disabled = []
        for cfg in candidates:
            cid = cfg["id"]
            rank = overrides.get(str(cid), cfg["priority"] or 0)
            row = {k: cfg[k] for k in ("id", "name", "model", "purpose", "enabled")}
            row.update(estimates[cid], before=rank, after=rank, hold_reason=held.get(cid))
            if not all(str(cfg[k] or "").strip() for k in ("api_url", "api_key", "model")):
                row.update(cost=None, reason="节点配置不完整")
            if not cfg["enabled"]:
                disabled.append(row)
                continue
            tier = PURPOSES["npc"].index(cfg["purpose"].strip().lower()) if pool == "npc" else 0
            tiers.setdefault(tier, []).append(row)
        ordered = []
        for tier in sorted(tiers):
            rows = sorted(tiers[tier], key=lambda r: (r["before"], r["id"]))
            ordered.extend(_order_tier(rows, direction == "desc"))
        ordered.extend(disabled)
        changed = any(r["before"] != r["after"] for r in ordered)
        snapshot = [pool, direction, usage, ordered, overrides, candidates]
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        if revision is not None:
            if digest != revision:
                raise StalePreview("渠道状态、报价、样本或本池顺序已变化，请重新预览")
            if changed:
                priorities = {str(r["id"]): r["after"] for r in ordered
                              if r["enabled"] or str(r["id"]) in overrides}
                db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
                           (key, json.dumps(priorities)))
        return dict(pool=pool, direction=direction, usage=usage, rows=ordered,
                    changed=changed, revision=digest)
