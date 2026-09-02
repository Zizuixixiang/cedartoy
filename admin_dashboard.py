"""Read-only aggregate metrics for the CEDAR TOY admin dashboard.

This module deliberately returns operational metadata only.  It never selects
chat messages, puzzle text/answers, request notes, loan terms, or usernames.
"""

from __future__ import annotations

import logging
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path


logger = logging.getLogger(__name__)

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))
RANGE_LABELS = {
    "10m": "10分钟",
    "1h": "1小时",
    "6h": "6小时",
    "12h": "12小时",
    "24h": "24小时",
}
RANGE_DURATIONS = {
    "10m": timedelta(minutes=10),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
}
EXCHANGE_STATUSES = ("completed", "pending", "withdrawn", "rejected", "expired")
LOAN_STATUSES = (
    "negotiating",
    "active",
    "overdue",
    "repaid",
    "rejected",
    "withdrawn",
    "expired",
)


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def range_window(range_key: str, now: datetime | None = None) -> dict:
    """Resolve one whitelisted dashboard range into an exact UTC window."""
    if range_key not in RANGE_LABELS:
        allowed = ", ".join(RANGE_LABELS)
        raise ValueError(f"range 必须是以下之一：{allowed}")
    end = _as_utc(now).replace(microsecond=0)
    start = end - RANGE_DURATIONS[range_key]
    return {
        "key": range_key,
        "label": RANGE_LABELS[range_key],
        "start": start,
        "end": end,
        "start_at": _iso_utc(start),
        "end_at": _iso_utc(end),
    }


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _duel_ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _soup_ts(value: datetime) -> str:
    return value.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _read_only_connect(path: Path | str) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    uri = f"file:{urllib.parse.quote(str(resolved))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 2000")
    return conn


def _int(value) -> int:
    return int(value or 0)


def _group_counts(rows, keys) -> dict:
    result = {key: 0 for key in keys}
    for row in rows:
        result[str(row["name"])] = _int(row["count"])
    return result


def _empty_duel(error: str | None = None) -> dict:
    result = {
        "ok": error is None,
        "realtime": {
            "active_rooms": 0,
            "current_open_rooms": 0,
            "active_humans": 0,
            "active_bound_machines": 0,
            "active_npc_rooms": 0,
        },
        "range": {
            "new_rooms": 0,
            "started_rooms": 0,
            "completed_rooms": 0,
            "participants": {"human": 0, "bound_machine": 0},
            "game_distribution": [],
        },
        "npc": {
            "rooms": 0,
            "started_room_share": 0.0,
            "participant_occurrences": 0,
            "distinct_personas": 0,
        },
        "chips": {
            "daily_check_ins": {
                "count": 0,
                "distinct_subjects": 0,
                "by_subject_type": {
                    "human": {"count": 0, "distinct_subjects": 0},
                    "ai": {"count": 0, "distinct_subjects": 0},
                },
            },
            "settlements": {
                "rooms": 0,
                "total_stake": 0,
                "median_stake": 0.0,
                "participants": {"human": 0, "bound_machine": 0},
            },
            "exchange": {
                "requests": 0,
                "distinct_pairs": 0,
                "status_counts": {key: 0 for key in EXCHANGE_STATUSES},
                "items": [],
            },
            "loans": {
                "created": 0,
                "accepted": 0,
                "repaid": 0,
                "status_counts": {key: 0 for key in LOAN_STATUSES},
            },
            "achievements": {
                "unlocks": 0,
                "distinct_subjects": 0,
                "reward_chips": 0,
                "automatic": True,
            },
            "wallets_current": {
                "bankruptcy_badges": {"total": 0, "human": 0, "ai": 0},
                "negative_balances": {"total": 0, "human": 0, "ai": 0},
            },
        },
        "definitions": {
            "active": "仍在进行或等待中的房间，且在所选时间内有房间更新或对局动作。",
            "started": "所选时间内新开的房间中，有实际对局动作或已经结束的房间。",
            "participants": "所选时间内真正开始过的房间中，实际加入过的人类和绑定小机去重；不含仅受邀者与系统 NPC。",
            "settlements": "筹码结算按已完成结算的对局房间去重；下注量按房间记录统计，不累加收支流水。",
        },
    }
    if error is not None:
        result["error"] = error
    return result


def _empty_turtle(error: str | None = None) -> dict:
    result = {
        "ok": error is None,
        "realtime": {
            "active_rooms": 0,
            "active_humans": 0,
            "active_ai": 0,
        },
        "range": {
            "new_rooms": 0,
            "completed_rooms": 0,
            "solved_rooms": 0,
            "question_count": 0,
            "ended_without_winner": 0,
            "participants": {"human": 0, "ai": 0},
            "status_distribution": [],
            "finished_duration_minutes": {"average": 0.0, "median": 0.0, "sample": 0},
        },
        "definitions": {
            "active": "仍未结束，且在所选时间内有创建、在场活动或游戏操作证据的房间。",
            "participants": "所选时间内有在场活动、游戏操作、创建或胜出证据的参与者去重，并区分人类和小机。",
            "completed": "在所选时间内完成；有胜者记为答出，否则记为无胜者结束。",
        },
    }
    if error is not None:
        result["error"] = error
    return result


def _active_duel_room_cte() -> str:
    # UNION keeps the two activity clocks independently indexable.  On the
    # current data SQLite chooses the selective status index for updated_at and
    # idx_rooms_last_move_at for the second branch.
    return """
        active_room_ids AS (
            SELECT room_id
            FROM rooms
            WHERE status IN ('playing', 'pending')
              AND updated_at >= ? AND updated_at < ?
            UNION
            SELECT room_id
            FROM rooms
            WHERE status IN ('playing', 'pending')
              AND last_move_at >= ? AND last_move_at < ?
        )
    """


def _collect_duel(path, window: dict) -> dict:
    result = _empty_duel()
    start = _duel_ts(window["start"])
    end = _duel_ts(window["end"])
    active_params = (start, end, start, end)
    started_sql = "(r.revision > 0 OR r.terminal_at IS NOT NULL OR r.status IN ('finished', 'archived'))"

    conn = _read_only_connect(path)
    try:
        active_rooms = conn.execute(
            f"WITH {_active_duel_room_cte()} SELECT COUNT(*) FROM active_room_ids",
            active_params,
        ).fetchone()[0]
        active_participants = conn.execute(
            f"""
            WITH {_active_duel_room_cte()}
            SELECT p.participant_kind AS name, COUNT(DISTINCT p.player_id) AS count
            FROM active_room_ids a
            JOIN room_participants p ON p.room_id = a.room_id
            WHERE p.join_status = 'joined'
              AND p.participant_kind IN ('human', 'bound_machine')
            GROUP BY p.participant_kind
            """,
            active_params,
        ).fetchall()
        active_by_kind = _group_counts(active_participants, ("human", "bound_machine"))
        active_npc_rooms = conn.execute(
            f"""
            WITH {_active_duel_room_cte()}
            SELECT COUNT(DISTINCT a.room_id)
            FROM active_room_ids a
            JOIN room_participants p ON p.room_id = a.room_id
            WHERE p.participant_kind = 'system_npc' AND p.join_status = 'joined'
            """,
            active_params,
        ).fetchone()[0]
        result["realtime"] = {
            "active_rooms": _int(active_rooms),
            "current_open_rooms": _int(active_rooms),
            "active_humans": active_by_kind["human"],
            "active_bound_machines": active_by_kind["bound_machine"],
            "active_npc_rooms": _int(active_npc_rooms),
        }

        room_funnel = conn.execute(
            f"""
            SELECT
                COUNT(*) AS new_rooms,
                COALESCE(SUM(CASE WHEN {started_sql} THEN 1 ELSE 0 END), 0) AS started_rooms
            FROM rooms r
            WHERE r.created_at >= ? AND r.created_at < ?
            """,
            (start, end),
        ).fetchone()
        completed_rooms = conn.execute(
            """
            SELECT COUNT(*)
            FROM rooms
            WHERE status IN ('finished', 'archived')
              AND terminal_at >= ? AND terminal_at < ?
            """,
            (start, end),
        ).fetchone()[0]
        participants = conn.execute(
            f"""
            SELECT p.participant_kind AS name, COUNT(DISTINCT p.player_id) AS count
            FROM rooms r
            JOIN room_participants p ON p.room_id = r.room_id
            WHERE r.created_at >= ? AND r.created_at < ?
              AND {started_sql}
              AND p.join_status <> 'invited'
              AND p.participant_kind IN ('human', 'bound_machine')
            GROUP BY p.participant_kind
            """,
            (start, end),
        ).fetchall()
        participant_counts = _group_counts(participants, ("human", "bound_machine"))
        game_distribution = [
            {"game_type": str(row["game_type"]), "rooms": _int(row["rooms"])}
            for row in conn.execute(
                f"""
                SELECT r.game_type, COUNT(*) AS rooms
                FROM rooms r
                WHERE r.created_at >= ? AND r.created_at < ?
                  AND {started_sql}
                GROUP BY r.game_type
                ORDER BY rooms DESC, r.game_type ASC
                """,
                (start, end),
            ).fetchall()
        ]
        result["range"] = {
            "new_rooms": _int(room_funnel["new_rooms"]),
            "started_rooms": _int(room_funnel["started_rooms"]),
            "completed_rooms": _int(completed_rooms),
            "participants": participant_counts,
            "game_distribution": game_distribution,
        }

        npc = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT r.room_id) AS rooms,
                COUNT(*) AS occurrences,
                COUNT(DISTINCT p.npc_persona_id) AS personas
            FROM rooms r
            JOIN room_participants p ON p.room_id = r.room_id
            WHERE r.created_at >= ? AND r.created_at < ?
              AND {started_sql}
              AND p.participant_kind = 'system_npc'
              AND p.join_status <> 'invited'
            """,
            (start, end),
        ).fetchone()
        npc_rooms = _int(npc["rooms"])
        started_rooms = _int(room_funnel["started_rooms"])
        result["npc"] = {
            "rooms": npc_rooms,
            "started_room_share": round(npc_rooms / started_rooms, 4) if started_rooms else 0.0,
            "participant_occurrences": _int(npc["occurrences"]),
            "distinct_personas": _int(npc["personas"]),
        }

        _collect_chips(conn, result["chips"], start, end)

        return result
    finally:
        conn.close()


def _collect_chips(conn, result: dict, start: str, end: str) -> None:
    check_in_rows = conn.execute(
        """
        SELECT
            w.subject_type AS name,
            COUNT(*) AS count,
            COUNT(DISTINCT w.subject_id) AS distinct_subjects
        FROM chip_ledger l
        JOIN chip_wallets w ON w.id = l.wallet_id
        WHERE l.transaction_type = 'daily_check_in'
          AND l.created_at >= ? AND l.created_at < ?
        GROUP BY w.subject_type
        """,
        (start, end),
    ).fetchall()
    check_ins = {
        "human": {"count": 0, "distinct_subjects": 0},
        "ai": {"count": 0, "distinct_subjects": 0},
    }
    for row in check_in_rows:
        subject_type = str(row["name"])
        if subject_type in check_ins:
            check_ins[subject_type] = {
                "count": _int(row["count"]),
                "distinct_subjects": _int(row["distinct_subjects"]),
            }
    result["daily_check_ins"] = {
        "count": sum(item["count"] for item in check_ins.values()),
        "distinct_subjects": sum(item["distinct_subjects"] for item in check_ins.values()),
        "by_subject_type": check_ins,
    }

    settlement = conn.execute(
        """
        WITH settled AS (
            SELECT DISTINCT r.room_id, r.stake
            FROM chip_settlement_batches b
            JOIN rooms r ON r.room_id = b.reference_id
            WHERE b.reference_type = 'duel_room'
              AND b.created_at >= ? AND b.created_at < ?
        ),
        ranked AS (
            SELECT
                stake,
                ROW_NUMBER() OVER (ORDER BY stake) AS row_number,
                COUNT(*) OVER () AS total_count
            FROM settled
        )
        SELECT
            COUNT(*) AS rooms,
            COALESCE(SUM(stake), 0) AS total_stake,
            COALESCE(AVG(
                CASE WHEN row_number IN ((total_count + 1) / 2, (total_count + 2) / 2)
                     THEN stake END
            ), 0) AS median_stake
        FROM ranked
        """,
        (start, end),
    ).fetchone()
    settlement_participants = conn.execute(
        """
        WITH settled_rooms AS (
            SELECT DISTINCT reference_id AS room_id
            FROM chip_settlement_batches
            WHERE reference_type = 'duel_room'
              AND created_at >= ? AND created_at < ?
        )
        SELECT p.participant_kind AS name, COUNT(DISTINCT p.player_id) AS count
        FROM settled_rooms s
        JOIN room_participants p ON p.room_id = s.room_id
        WHERE p.join_status <> 'invited'
          AND p.participant_kind IN ('human', 'bound_machine')
        GROUP BY p.participant_kind
        """,
        (start, end),
    ).fetchall()
    result["settlements"] = {
        "rooms": _int(settlement["rooms"]),
        "total_stake": _int(settlement["total_stake"]),
        "median_stake": round(float(settlement["median_stake"] or 0), 1),
        "participants": _group_counts(settlement_participants, ("human", "bound_machine")),
    }

    exchange_summary = conn.execute(
        """
        SELECT
            COUNT(*) AS requests,
            COUNT(DISTINCT human_id || X'1f' || ai_id) AS distinct_pairs
        FROM exchange_requests
        WHERE created_at >= ? AND created_at < ?
        """,
        (start, end),
    ).fetchone()
    exchange_statuses = _group_counts(
        conn.execute(
            """
            SELECT status AS name, COUNT(*) AS count
            FROM exchange_requests
            WHERE created_at >= ? AND created_at < ?
            GROUP BY status
            """,
            (start, end),
        ).fetchall(),
        EXCHANGE_STATUSES,
    )
    exchange_items = [
        {
            "item_key": str(row["item_key"]),
            "item_title": str(row["item_title"]),
            "requests": _int(row["requests"]),
        }
        for row in conn.execute(
            """
            SELECT item_key, item_title, COUNT(*) AS requests
            FROM exchange_requests
            WHERE created_at >= ? AND created_at < ?
            GROUP BY item_key, item_title
            ORDER BY requests DESC, item_key ASC
            LIMIT 10
            """,
            (start, end),
        ).fetchall()
    ]
    result["exchange"] = {
        "requests": _int(exchange_summary["requests"]),
        "distinct_pairs": _int(exchange_summary["distinct_pairs"]),
        "status_counts": exchange_statuses,
        "items": exchange_items,
    }

    loan_summary = conn.execute(
        """
        SELECT
            COUNT(*) AS created,
            COALESCE(SUM(CASE WHEN accepted_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS accepted,
            COALESCE(SUM(CASE WHEN status = 'repaid' OR repaid_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS repaid
        FROM loans
        WHERE created_at >= ? AND created_at < ?
        """,
        (start, end),
    ).fetchone()
    loan_statuses = _group_counts(
        conn.execute(
            """
            SELECT status AS name, COUNT(*) AS count
            FROM loans
            WHERE created_at >= ? AND created_at < ?
            GROUP BY status
            """,
            (start, end),
        ).fetchall(),
        LOAN_STATUSES,
    )
    result["loans"] = {
        "created": _int(loan_summary["created"]),
        "accepted": _int(loan_summary["accepted"]),
        "repaid": _int(loan_summary["repaid"]),
        "status_counts": loan_statuses,
    }

    achievements = conn.execute(
        """
        SELECT
            COUNT(*) AS unlocks,
            COUNT(DISTINCT subject_type || X'1f' || subject_id) AS distinct_subjects,
            COALESCE(SUM(reward), 0) AS reward_chips
        FROM achievement_unlocks
        WHERE unlocked_at >= ? AND unlocked_at < ?
        """,
        (start, end),
    ).fetchone()
    result["achievements"] = {
        "unlocks": _int(achievements["unlocks"]),
        "distinct_subjects": _int(achievements["distinct_subjects"]),
        "reward_chips": _int(achievements["reward_chips"]),
        "automatic": True,
    }

    wallets = conn.execute(
        """
        SELECT
            subject_type AS name,
            COALESCE(SUM(CASE WHEN bankruptcy_badge_active = 1 THEN 1 ELSE 0 END), 0) AS badges,
            COALESCE(SUM(CASE WHEN balance < 0 THEN 1 ELSE 0 END), 0) AS negative
        FROM chip_wallets
        GROUP BY subject_type
        """
    ).fetchall()
    badges = {"total": 0, "human": 0, "ai": 0}
    negative = {"total": 0, "human": 0, "ai": 0}
    for row in wallets:
        subject_type = str(row["name"])
        if subject_type in ("human", "ai"):
            badges[subject_type] = _int(row["badges"])
            negative[subject_type] = _int(row["negative"])
    badges["total"] = badges["human"] + badges["ai"]
    negative["total"] = negative["human"] + negative["ai"]
    result["wallets_current"] = {
        "bankruptcy_badges": badges,
        "negative_balances": negative,
    }


def _collect_turtle(path, window: dict) -> dict:
    result = _empty_turtle()
    start = _soup_ts(window["start"])
    end = _soup_ts(window["end"])

    conn = _read_only_connect(path)
    try:
        active_rooms = conn.execute(
            """
            WITH active_room_ids AS (
                SELECT id AS room_id
                FROM rooms
                WHERE status NOT IN ('finished', 'archived')
                  AND created_at >= ? AND created_at < ?
                UNION
                SELECT rp.room_id
                FROM room_presence rp
                JOIN rooms r ON r.id = rp.room_id
                WHERE r.status NOT IN ('finished', 'archived')
                  AND rp.last_active_at >= ? AND rp.last_active_at < ?
                UNION
                SELECT gl.room_id
                FROM game_logs gl
                JOIN rooms r ON r.id = gl.room_id
                WHERE r.status NOT IN ('finished', 'archived')
                  AND gl.created_at >= ? AND gl.created_at < ?
            )
            SELECT COUNT(*) FROM active_room_ids
            """,
            (start, end, start, end, start, end),
        ).fetchone()[0]
        active_people = conn.execute(
            """
            WITH window_evidence AS (
                SELECT rp.room_id, rp.player_id
                FROM room_presence rp
                WHERE rp.last_active_at >= ? AND rp.last_active_at < ?
                UNION
                SELECT gl.room_id, gl.player_id
                FROM game_logs gl
                WHERE gl.player_id IS NOT NULL
                  AND gl.created_at >= ? AND gl.created_at < ?
                UNION
                SELECT id AS room_id, created_by AS player_id
                FROM rooms
                WHERE created_by IS NOT NULL
                  AND created_at >= ? AND created_at < ?
            )
            SELECT p.is_ai AS name, COUNT(DISTINCT e.player_id) AS count
            FROM window_evidence e
            JOIN rooms r ON r.id = e.room_id
            JOIN players p ON p.id = e.player_id
            WHERE r.status NOT IN ('finished', 'archived')
            GROUP BY p.is_ai
            """,
            (start, end, start, end, start, end),
        ).fetchall()
        active_by_type = {0: 0, 1: 0}
        for row in active_people:
            active_by_type[int(row["name"] or 0)] = _int(row["count"])
        result["realtime"] = {
            "active_rooms": _int(active_rooms),
            "active_humans": active_by_type[0],
            "active_ai": active_by_type[1],
        }

        new_rooms = conn.execute(
            "SELECT COUNT(*) FROM rooms WHERE created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()[0]
        completions = conn.execute(
            """
            SELECT
                COUNT(*) AS completed,
                COALESCE(SUM(CASE WHEN winner_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS solved,
                COALESCE(SUM(CASE WHEN winner_id IS NULL THEN 1 ELSE 0 END), 0) AS no_winner
            FROM rooms
            WHERE finished_at >= ? AND finished_at < ?
            """,
            (start, end),
        ).fetchone()
        question_count = conn.execute(
            "SELECT COUNT(*) FROM game_logs WHERE type = 'ask' AND created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()[0]
        participants = conn.execute(
            """
            WITH evidence AS (
                SELECT player_id
                FROM room_presence
                WHERE last_active_at >= ? AND last_active_at < ?
                UNION
                SELECT player_id
                FROM game_logs
                WHERE player_id IS NOT NULL
                  AND created_at >= ? AND created_at < ?
                UNION
                SELECT created_by AS player_id
                FROM rooms
                WHERE created_by IS NOT NULL
                  AND created_at >= ? AND created_at < ?
                UNION
                SELECT winner_id AS player_id
                FROM rooms
                WHERE winner_id IS NOT NULL
                  AND finished_at >= ? AND finished_at < ?
            )
            SELECT p.is_ai AS name, COUNT(DISTINCT e.player_id) AS count
            FROM evidence e
            JOIN players p ON p.id = e.player_id
            GROUP BY p.is_ai
            """,
            (start, end, start, end, start, end, start, end),
        ).fetchall()
        participant_counts = {"human": 0, "ai": 0}
        for row in participants:
            participant_counts["ai" if int(row["name"] or 0) else "human"] = _int(row["count"])
        statuses = [
            {"status": str(row["status"]), "rooms": _int(row["rooms"])}
            for row in conn.execute(
                """
                SELECT status, COUNT(*) AS rooms
                FROM rooms
                WHERE created_at >= ? AND created_at < ?
                GROUP BY status
                ORDER BY rooms DESC, status ASC
                """,
                (start, end),
            ).fetchall()
        ]
        duration = conn.execute(
            """
            WITH completed AS (
                SELECT (julianday(finished_at) - julianday(created_at)) * 1440.0 AS minutes
                FROM rooms
                WHERE finished_at >= ? AND finished_at < ?
                  AND finished_at >= created_at
            ),
            ranked AS (
                SELECT
                    minutes,
                    ROW_NUMBER() OVER (ORDER BY minutes) AS row_number,
                    COUNT(*) OVER () AS total_count
                FROM completed
            )
            SELECT
                COUNT(*) AS sample,
                COALESCE(AVG(minutes), 0) AS average,
                COALESCE(AVG(
                    CASE WHEN row_number IN ((total_count + 1) / 2, (total_count + 2) / 2)
                         THEN minutes END
                ), 0) AS median
            FROM ranked
            """,
            (start, end),
        ).fetchone()
        result["range"] = {
            "new_rooms": _int(new_rooms),
            "completed_rooms": _int(completions["completed"]),
            "solved_rooms": _int(completions["solved"]),
            "question_count": _int(question_count),
            "ended_without_winner": _int(completions["no_winner"]),
            "participants": participant_counts,
            "status_distribution": statuses,
            "finished_duration_minutes": {
                "average": round(float(duration["average"] or 0), 1),
                "median": round(float(duration["median"] or 0), 1),
                "sample": _int(duration["sample"]),
            },
        }

        return result
    finally:
        conn.close()


def build_activity_dashboard(
    duel_db_path: Path | str,
    turtle_db_path: Path | str,
    range_key: str = "1h",
    *,
    now: datetime | None = None,
) -> dict:
    """Build both modules while isolating database failures from each other."""
    window = range_window(range_key, now)
    response = {
        "generated_at": _iso_utc(window["end"]),
        "range": {
            "key": window["key"],
            "label": window["label"],
            "start_at": window["start_at"],
            "end_at": window["end_at"],
        },
    }
    try:
        response["duel"] = _collect_duel(duel_db_path, window)
    except Exception:
        logger.exception("Failed to collect Duel admin dashboard metrics")
        response["duel"] = _empty_duel("双弈与筹码数据暂时不可用")
    try:
        response["turtle"] = _collect_turtle(turtle_db_path, window)
    except Exception:
        logger.exception("Failed to collect Turtle Soup admin dashboard metrics")
        response["turtle"] = _empty_turtle("海龟汤数据暂时不可用")
    return response
