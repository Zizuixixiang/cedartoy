"""Persistent, de-duplicated ECO capacity alerts via CedarClio."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)

MAX_SESSIONS = 3000
THRESHOLDS = (
    (2400, 80),
    (2700, 90),
    (2850, 95),
    (3000, 100),
)

ROOT = Path(__file__).resolve().parent.parent
CEDARCLIO_PYTHON = os.getenv(
    "CEDARCLIO_PYTHON", "/opt/cedarclio/venv/bin/python"
)
NOTIFY_BRIDGE = os.getenv(
    "CEDARCLIO_NOTIFY_BRIDGE", str(ROOT / "scripts" / "cedarclio_notify.py")
)


@dataclass(frozen=True)
class CapacityAlert:
    threshold: int
    percent: int
    count: int

    @property
    def level(self) -> str:
        return "alert" if self.percent == 100 else "warning"

    @property
    def text(self) -> str:
        title = f"⚠️ CedarToy ECO 容量预警（{self.percent}%）"
        if self.percent == 100:
            body = (
                f"当前池塘 {self.count}/{MAX_SESSIONS}，容量已满；游客新建池塘已受限。"
                "已有池塘仍可访问，注册账号存档不会被淘汰。"
            )
        else:
            body = (
                f"当前池塘 {self.count}/{MAX_SESSIONS}。容量回收只会清理超过 30 天"
                "不活跃的明确游客池塘，不会淘汰注册账号或身份不明存档。"
            )
        return f"{title}\n{body}"


def _band_for_count(count: int) -> tuple[int, int]:
    band = (0, 0)
    for threshold, percent in THRESHOLDS:
        if count < threshold:
            break
        band = (threshold, percent)
    return band


def init_alert_state(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS eco_capacity_alert_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            threshold INTEGER NOT NULL DEFAULT 0,
            last_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )


def update_alert_state(conn, count: int) -> CapacityAlert | None:
    """Persist the current band and return one alert only on an upward crossing.

    Persisting downward movement rearms any higher threshold, so a later crossing
    can alert again without repeating alerts for every request within one band.
    """
    init_alert_state(conn)
    row = conn.execute(
        "SELECT threshold FROM eco_capacity_alert_state WHERE id = 1"
    ).fetchone()
    previous = int(row[0]) if row is not None else 0
    threshold, percent = _band_for_count(int(count))
    conn.execute(
        """
        INSERT INTO eco_capacity_alert_state (id, threshold, last_count, updated_at)
        VALUES (1, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(id) DO UPDATE SET
            threshold = excluded.threshold,
            last_count = excluded.last_count,
            updated_at = excluded.updated_at
        """,
        (threshold, int(count)),
    )
    if threshold > previous:
        return CapacityAlert(threshold=threshold, percent=percent, count=int(count))
    return None


def rearm_after_count_drop(conn, count: int) -> None:
    """Record only flat/downward movement without consuming a future crossing."""
    init_alert_state(conn)
    row = conn.execute(
        "SELECT threshold FROM eco_capacity_alert_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return
    previous = int(row[0])
    threshold, _percent = _band_for_count(int(count))
    if threshold > previous:
        return
    conn.execute(
        """
        UPDATE eco_capacity_alert_state
        SET threshold = ?, last_count = ?, updated_at = datetime('now', 'localtime')
        WHERE id = 1
        """,
        (threshold, int(count)),
    )


def dispatch_alert(alert: CapacityAlert | None) -> bool:
    """Launch the CedarClio notifier without blocking or affecting ECO traffic."""
    if alert is None:
        return False
    try:
        subprocess.Popen(
            [
                CEDARCLIO_PYTHON,
                NOTIFY_BRIDGE,
                "--level",
                alert.level,
                "--text",
                alert.text,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        return True
    except Exception:
        logger.exception(
            "ECO capacity notification launch failed at threshold=%s count=%s",
            alert.threshold,
            alert.count,
        )
        return False
