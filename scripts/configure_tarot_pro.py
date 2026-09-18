#!/usr/bin/env python3
"""Prepare the managed Tarot Pro row without displaying credentials.

The database path is intentionally required and writes require ``--apply`` so
this script can be exercised safely against a copied database before rollout.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any


FLASH_MODEL = "gemini-3.5-flash"
PRO_MODEL = "gemini-3.1-pro-preview"
TARGET_NAME = "gg · 3.1 Pro · 塔罗解读"


class ConfigurationError(RuntimeError):
    pass


def _endpoint(value: Any) -> str:
    endpoint = str(value or "").strip().rstrip("/").lower()
    suffix = "/chat/completions"
    return endpoint if endpoint.endswith(suffix) else endpoint + suffix


def _node(row: sqlite3.Row, model: str | None = None) -> tuple[str, str, str]:
    return (
        _endpoint(row["api_url"]),
        str(row["api_key"] or "").strip(),
        str(model if model is not None else row["model"] or "").strip(),
    )


def configure(
    database: str | Path,
    *,
    source_id: int = 19,
    apply: bool = False,
) -> dict[str, Any]:
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise ConfigurationError(f"数据库不存在：{path}")

    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(judge_api_configs)")
        }
        required = {
            "id", "name", "api_url", "api_key", "model", "purpose", "enabled", "priority",
        }
        if not required.issubset(columns):
            raise ConfigurationError("judge_api_configs 表结构不完整")
        if apply:
            conn.execute("BEGIN IMMEDIATE")

        source = conn.execute(
            "SELECT * FROM judge_api_configs WHERE id=?", (int(source_id),)
        ).fetchone()
        if source is None:
            raise ConfigurationError(f"未找到 Flash 源配置 #{source_id}")
        if (
            str(source["model"] or "").strip() != FLASH_MODEL
            or str(source["purpose"] or "").strip().lower() != "tarot"
            or int(source["enabled"] or 0) != 1
            or not str(source["api_url"] or "").strip()
            or not str(source["api_key"] or "").strip()
        ):
            raise ConfigurationError(
                f"源配置 #{source_id} 必须是启用的 tarot / {FLASH_MODEL} 记录"
            )

        rows = conn.execute(
            "SELECT * FROM judge_api_configs ORDER BY id ASC"
        ).fetchall()
        target_node = _node(source, PRO_MODEL)
        node_rows = [row for row in rows if _node(row) == target_node]
        active = [row for row in node_rows if int(row["enabled"] or 0) == 1]
        if len(active) > 1:
            ids = ", ".join(f"#{int(row['id'])}" for row in active)
            raise ConfigurationError(f"Pro 模型节点已有多条启用记录：{ids}")
        if active:
            row = active[0]
            if str(row["purpose"] or "").strip().lower() != "tarot":
                raise ConfigurationError(
                    f"Pro 模型节点与非 tarot 启用配置 #{int(row['id'])} 冲突"
                )
            if conn.in_transaction:
                conn.rollback()
            return {
                "action": "unchanged",
                "config_id": int(row["id"]),
                "model": PRO_MODEL,
                "purpose": "tarot",
            }

        reusable = next(
            (
                row for row in node_rows
                if str(row["purpose"] or "").strip().lower() == "tarot"
            ),
            None,
        )
        action = "enable" if reusable is not None else "insert"
        if not apply:
            return {
                "action": f"would_{action}",
                "config_id": int(reusable["id"]) if reusable is not None else None,
                "model": PRO_MODEL,
                "purpose": "tarot",
            }

        if reusable is not None:
            config_id = int(reusable["id"])
            conn.execute(
                """
                UPDATE judge_api_configs
                SET name=?, purpose='tarot', enabled=1, priority=?
                WHERE id=?
                """,
                (TARGET_NAME, int(source["priority"] or 0), config_id),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO judge_api_configs(
                    name,api_url,api_key,model,purpose,enabled,priority
                ) VALUES(?,?,?,?,'tarot',1,?)
                """,
                (
                    TARGET_NAME,
                    source["api_url"],
                    source["api_key"],
                    PRO_MODEL,
                    int(source["priority"] or 0),
                ),
            )
            config_id = int(cursor.lastrowid)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ConfigurationError("写入后数据库 integrity_check 未通过")
        conn.commit()
        return {
            "action": action,
            "config_id": config_id,
            "model": PRO_MODEL,
            "purpose": "tarot",
            "integrity": "ok",
        }
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用已有 Flash 塔罗通道准备独立的 Pro 塔罗配置",
    )
    parser.add_argument("--database", required=True, help="明确的 sqlite 数据库路径")
    parser.add_argument("--source-id", type=int, default=19)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入；不加时只做检查和预演",
    )
    args = parser.parse_args()
    try:
        result = configure(
            args.database,
            source_id=args.source_id,
            apply=args.apply,
        )
    except ConfigurationError as exc:
        parser.error(str(exc))
    action = result["action"]
    config_id = result.get("config_id")
    suffix = f" #{config_id}" if config_id is not None else ""
    print(f"{action}{suffix}: purpose=tarot model={PRO_MODEL}")
    if result.get("integrity"):
        print("integrity_check=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
