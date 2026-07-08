"""词与物适配层最小验证：开一局、走几步、存读档各验一次。

用临时 sqlite 库，不碰生产 data/sessions.db。运行：
    cd /opt/cedartoy && python3 -m ciyuwu_adapter.smoke_test
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ciyuwu_adapter import handler


def _call(name, arguments):
    resp = handler.handle_mcp({
        "jsonrpc": "2.0",
        "id": f"smoke-{name}",
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert "result" in resp, f"{name} 返回错误：{resp}"
    result = resp["result"]
    text = result["content"][0]["text"]
    assert not result.get("isError"), f"{name} 工具报错：{text}"
    return text


def main():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="ciyuwu_smoke_")
    os.close(fd)
    handler.DB_PATH = db_path
    vendor_saves = [
        os.path.join(handler._VENDOR_DIR, "ciyuwu_save.json"),
        os.path.join(handler._VENDOR_DIR, "dark_save.json"),
    ]
    pre_existing = {p: os.path.exists(p) for p in vendor_saves}

    try:
        # 1. 开一局
        text = _call("ciyuwu_new", {"player_id": "smoke1", "seed": 42})
        assert "新局已开" in text and "seed=42" in text, text[:200]
        print("[1] 开局 OK")

        # 2. 走几步：建角色 -> 确认 -> 出镇 -> 批量前进 -> 串联说话
        text = _call("ciyuwu_cmd", {"player_id": "smoke1", "command": "新角"})
        assert "角色创建" in text, text[:200]
        text = _call("ciyuwu_cmd", {"player_id": "smoke1", "command": "确认"})
        text = _call("ciyuwu_cmd", {"player_id": "smoke1", "command": "出镇 灰林"})
        text = _call("ciyuwu_cmd", {"player_id": "smoke1", "command": "前进3"})
        last_line = text.strip().splitlines()[-1]
        bar = json.loads(last_line)
        assert "phase" in bar and "hp" in bar, f"末行不是状态栏：{last_line}"
        text = _call("ciyuwu_cmd", {"player_id": "smoke1", "command": "说 我在"})
        print(f"[2] 走几步 OK（当前 phase={json.loads(text.strip().splitlines()[-1]).get('phase')}）")

        # 3. 存档已持久化：ciyuwu_info 走的是「从 DB 读档 -> 执行 -> 写回」全链路
        text = _call("ciyuwu_info", {"player_id": "smoke1", "action": "status"})
        assert "HP" in text or "hp" in text, text[:200]
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT save_data, meta_data, user_id FROM ciyuwu_sessions WHERE player_id='smoke1'"
            ).fetchone()
        state = json.loads(row[0])
        meta = json.loads(row[1])
        assert state.get("phase") == "explore", f"当局层 phase 异常：{state.get('phase')}"
        assert "echoes" in meta and "runs" in meta, f"跨局层缺字段：{list(meta)}"
        assert row[2] is None, "user_id 预留列应为 NULL"
        print(f"[3] 两层持久化 OK（run 层 phase={state['phase']}，meta 层 keys={sorted(meta)[:4]}...）")

        # 4. 存档：export
        text = _call("ciyuwu_save", {"player_id": "smoke1", "action": "export"})
        blob_b64 = text.strip().splitlines()[-1]
        print(f"[4] export OK（{len(blob_b64)} 字符）")

        # 5. 读档：import 到另一玩家，状态应完全一致
        _call("ciyuwu_save", {"player_id": "smoke2", "action": "import", "save_data": blob_b64})
        s1 = _call("ciyuwu_info", {"player_id": "smoke1", "action": "words"})
        s2 = _call("ciyuwu_info", {"player_id": "smoke2", "action": "words"})
        assert s1 == s2, "import 后词库不一致"
        print("[5] import OK（词库一致）")

        # 6. 重开新局保留跨局 meta；跨局字段注入新局 state
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE ciyuwu_sessions SET meta_data=? WHERE player_id='smoke1'",
                (json.dumps(dict(meta, echoes=7)),),
            )
        text = _call("ciyuwu_new", {"player_id": "smoke1"})
        assert "跨局进度保留" in text, text[:200]
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT save_data, meta_data FROM ciyuwu_sessions WHERE player_id='smoke1'"
            ).fetchone()
        assert json.loads(row[1])["echoes"] == 7, "meta 未跨新局保留"
        assert json.loads(row[0])["echoes"] == 7, "meta 未注入新局 state"
        print("[6] 新局保留跨局 meta OK（echoes=7 存活）")

        # 7. 空跑一局不得结算跨局收益：新角 -> 确认 -> 脱出
        _call("ciyuwu_new", {"player_id": "empty1", "seed": 7})
        _call("ciyuwu_cmd", {"player_id": "empty1", "command": "新角"})
        _call("ciyuwu_cmd", {"player_id": "empty1", "command": "确认"})
        text = _call("ciyuwu_cmd", {"player_id": "empty1", "command": "脱出"})
        assert "尚无实质进度" in text and "遗刻+1" not in text, text
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT save_data, meta_data FROM ciyuwu_sessions WHERE player_id='empty1'"
            ).fetchone()
        state = json.loads(row[0])
        meta = json.loads(row[1])
        assert state["runs"] == 1 and meta["runs"] == 1, "空跑应保留局数统计"
        assert state["echoes"] == 0 and meta["echoes"] == 0, "空跑不应结算遗刻"
        print("[7] 空跑脱出不结算遗刻 OK")

        # 8. 上游文件存档已屏蔽：vendor 目录不应新增存档文件
        for p, existed in pre_existing.items():
            assert os.path.exists(p) == existed, f"vendor 存档文件被写入：{p}"
        print("[8] vendor 无文件写入 OK")

        print("\n全部通过 ✓")
    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    main()
