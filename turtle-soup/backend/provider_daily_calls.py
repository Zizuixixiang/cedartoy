"""Persistent wallet guard for official DeepSeek completion attempts only."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import database


DEEPSEEK_DAILY_CALL_CAP = 250
BEIJING = timezone(timedelta(hours=8))


class ProviderDailyCallsUnavailable(RuntimeError):
    """Skip this provider, without changing its upstream health/cooldown."""


def is_official_deepseek_endpoint(endpoint: str) -> bool:
    return urlsplit(endpoint).hostname == "api.deepseek.com"


def _beijing_day() -> str:
    return datetime.now(BEIJING).date().isoformat()


def reserve_deepseek_call(endpoint: str, api_key: str) -> None:
    """Commit one attempt immediately before send; never refund sent failures.

    Use the account DB selected by the service, shared by every worker. The
    official /v1 and unversioned endpoint aliases share the same credential
    identity, regardless of config ID, pool or model. Only a SHA-256 digest is
    stored. No async suspension between reservation and the caller's send:
    cancellation during a background DB commit could otherwise charge an
    unsent request. Keep lock waits bounded and fail closed on storage errors.
    """
    if not is_official_deepseek_endpoint(endpoint):
        return
    identity = hashlib.sha256(
        f"https://api.deepseek.com/chat/completions\0{api_key.strip()}".encode()
    ).hexdigest()
    try:
        with closing(sqlite3.connect(database.DB_PATH, timeout=1.0)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS provider_daily_calls (
                        credential_hash TEXT NOT NULL,
                        beijing_day TEXT NOT NULL,
                        calls INTEGER NOT NULL CHECK (calls >= 0),
                        PRIMARY KEY (credential_hash, beijing_day)
                    )
                """)
                # Determine the date after acquiring the write lock.
                cursor = db.execute("""
                    INSERT INTO provider_daily_calls
                        (credential_hash, beijing_day, calls) VALUES (?, ?, 1)
                    ON CONFLICT (credential_hash, beijing_day)
                    DO UPDATE SET calls = calls + 1 WHERE calls < ?
                """, (identity, _beijing_day(), DEEPSEEK_DAILY_CALL_CAP))
                allowed = cursor.rowcount == 1
    except sqlite3.Error as exc:
        raise ProviderDailyCallsUnavailable(
            "DeepSeek 每日调用计数不可用，已跳过官方节点"
        ) from exc
    if not allowed:
        raise ProviderDailyCallsUnavailable(
            f"DeepSeek 已达北京时间今日 {DEEPSEEK_DAILY_CALL_CAP} 次调用上限"
        )
