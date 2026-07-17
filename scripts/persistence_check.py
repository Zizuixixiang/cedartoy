#!/usr/bin/env python3
"""一键持久化回归脚本：vendor_cmd_adapter 全部子进程系游戏的三步双进程测试。

对每个游戏执行：
  ① 独立子进程 new 开局
  ② 另一独立子进程执行一条改状态指令
  ③ 再一独立子进程查询状态，断言变更跨进程保留
每步之后核对该游戏声明的存档文件全部存在且 JSON 可解析。

- 测试身份统一 guest:regcheck（一次性 id，跑前跑后均清理并校验为 0）。
- 每步都是全新 python3 子进程，模拟生产的一命令一进程模型。
- 任一 FAIL 退出码非 0。
- 不碰 git、不重启服务、不修改任何业务代码。

新游戏接入时：在 GAMES 配置表加一条（参见 docs/GAME_ONBOARDING_CHECKLIST.md），
指令与断言关键词参考 audit_persistence_report.md / final_acceptance_report.md 中实测用例。

用法：python3 scripts/persistence_check.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAVE_ROOT = ROOT / "data" / "vendor_saves"
PLAYER_ID = "guest:regcheck"
STEP_TIMEOUT = 60

# ---------------------------------------------------------------------------
# 配置表：每个游戏的三步指令与断言关键词（均来自昨晚审计/验收实测用例）。
# 字段说明：
#   game         vendor_cmd_adapter 模块名，同时是 data/vendor_saves/ 下目录名
#   label        汇总表显示名（memoria 按关卡拆成多条）
#   new_args     ① 开局 play() 参数（统一带 confirm=true，避免残档拦截）
#   mutate_args  ② 改状态指令
#   query_args   ③ 状态查询指令
#   mutate_expect / query_expect  对应步骤输出必须包含的关键词列表
#   save_files   每步之后必须存在且可 json.loads 的存档文件（相对玩家存档目录）
# ---------------------------------------------------------------------------
GAMES = [
    {
        "game": "memoria",
        "label": "memoria-L1",
        "new_args": {"action": "new", "level": "1", "confirm": "true"},
        "mutate_args": {"action": "cmd", "level": "1", "command": "go 厨房"},
        "mutate_expect": ["厨房"],
        "query_args": {"action": "cmd", "level": "1", "command": "status"},
        "query_expect": ["厨房"],
        "save_files": ["level1/detective_save.json", "progress.json"],
    },
    {
        "game": "memoria",
        "label": "memoria-L2",
        "new_args": {"action": "new", "level": "2", "difficulty": "normal", "confirm": "true"},
        "mutate_args": {"action": "cmd", "level": "2", "command": "go 餐车"},
        "mutate_expect": ["餐车"],
        "query_args": {"action": "cmd", "level": "2", "command": "status"},
        "query_expect": ["餐车"],
        "save_files": ["level2/detective_save_l2.json", "progress.json"],
    },
    {
        "game": "arcade",
        "label": "arcade",
        "new_args": {"action": "new", "confirm": "true"},
        "mutate_args": {"action": "cmd", "command": "slots help"},
        "mutate_expect": [],
        "query_args": {"action": "cmd", "command": "help"},
        "query_expect": ["slots"],
        "save_files": [
            "arcade_save.json",
            "slots_save.json",
            "blackjack_save.json",
            "roulette_save.json",
        ],
    },
    {
        "game": "burger",
        "label": "burger",
        "new_args": {"action": "new", "confirm": "true"},
        "mutate_args": {"action": "cmd", "command": "difficulty 忙碌"},
        "mutate_expect": ["忙碌"],
        "query_args": {"action": "cmd", "command": "status"},
        "query_expect": ["忙碌"],
        "save_files": ["save.json"],
    },
    {
        "game": "fishing",
        "label": "fishing",
        "new_args": {"action": "new", "seed": 12345, "confirm": "true"},
        "mutate_args": {"action": "cmd", "command": "cast 1"},
        "mutate_expect": [],
        "query_args": {"action": "cmd", "command": "status"},
        "query_expect": ["总抛竿 1"],
        "save_files": ["fishing_save.json"],
    },
    {
        "game": "leek",
        "label": "leek",
        "new_args": {"action": "new", "seed": 12345, "confirm": "true"},
        "mutate_args": {"action": "cmd", "command": "wait 1"},
        "mutate_expect": [],
        "query_args": {"action": "cmd", "command": "status"},
        "query_expect": ["第 2 天"],
        "save_files": ["leek_save.json"],
    },
    {
        "game": "market",
        "label": "market",
        "new_args": {"action": "new", "seed": 12345, "confirm": "true"},
        "mutate_args": {"action": "cmd", "command": "记得 她爱吃土豆"},
        "mutate_expect": ["她爱吃土豆"],
        "query_args": {"action": "cmd", "command": "口味"},
        "query_expect": ["她爱吃土豆"],
        "save_files": ["market_save.json"],
    },
    {
        "game": "imitator_td",
        "label": "imitator_td",
        "new_args": {"action": "new", "level": 1, "seed": "regcheck", "confirm": "true"},
        "mutate_args": {"action": "cmd", "command": "note regcheckmarker"},
        "mutate_expect": ["复盘"],
        "query_args": {"action": "cmd", "command": "note"},
        "query_expect": ["regcheckmarker"],
        "save_files": [
            "random_imitator_td_save.json",
            "random_imitator_td_records.json",
        ],
    },
]

# 每步在全新解释器进程中调用 vendor_cmd_adapter.<game>.play()，
# 与生产的一命令一子进程模型一致；本进程不复用任何游戏状态。
STEP_RUNNER = r"""
import importlib
import json
import sys

payload = json.load(sys.stdin)
sys.path.insert(0, payload["root"])
mod = importlib.import_module("vendor_cmd_adapter." + payload["game"])
result = mod.play(payload["args"])
print(json.dumps({"text": result.get("text", "")}, ensure_ascii=False))
"""


class StepError(Exception):
    pass


def run_step(game, args):
    """在独立子进程里执行一次 play()，返回输出文本。"""
    full_args = dict(args)
    full_args["player_id"] = PLAYER_ID
    payload = json.dumps({"root": str(ROOT), "game": game, "args": full_args}, ensure_ascii=False)
    proc = subprocess.run(
        [sys.executable, "-c", STEP_RUNNER],
        input=payload,
        text=True,
        capture_output=True,
        timeout=STEP_TIMEOUT,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise StepError((proc.stderr or proc.stdout or "").strip()[-800:] or "子进程非零退出")
    try:
        return json.loads(proc.stdout)["text"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise StepError(f"子进程输出无法解析: {exc}: {proc.stdout[:200]!r}")


def check_saves(game, save_files, step_name):
    """核对声明的存档文件全部存在且可解析为 JSON。"""
    player_dir = SAVE_ROOT / game / PLAYER_ID
    for rel in save_files:
        path = player_dir / rel
        if not path.exists():
            raise StepError(f"[{step_name}] 存档缺失: {path.relative_to(ROOT)}")
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StepError(f"[{step_name}] 存档不可解析: {path.relative_to(ROOT)}: {exc}")


def check_keywords(text, keywords, step_name):
    for kw in keywords:
        if kw not in text:
            raise StepError(f"[{step_name}] 输出缺少关键词 {kw!r}，实际输出（截断）: {text[:300]!r}")


def run_case(case):
    game = case["game"]
    for step_name, args_key, expect_key in (
        ("new", "new_args", None),
        ("mutate", "mutate_args", "mutate_expect"),
        ("query", "query_args", "query_expect"),
    ):
        text = run_step(game, case[args_key])
        if expect_key:
            check_keywords(text, case[expect_key], step_name)
        check_saves(game, case["save_files"], step_name)


def cleanup():
    """删除本次测试身份的全部存档目录，返回残留目录列表（应为空）。"""
    for game in sorted({c["game"] for c in GAMES}):
        target = SAVE_ROOT / game / PLAYER_ID
        if target.exists():
            shutil.rmtree(target)
    return [str(p.relative_to(ROOT)) for p in SAVE_ROOT.glob(f"*/{PLAYER_ID}")]


def main():
    print(f"持久化回归 | 身份 {PLAYER_ID} | 存档根 {SAVE_ROOT.relative_to(ROOT)}")
    residue = cleanup()
    if residue:
        print(f"预清理失败，残留: {residue}")
        return 1

    results = []
    try:
        for case in GAMES:
            try:
                run_case(case)
                results.append((case["label"], True, ""))
                print(f"  PASS  {case['label']}")
            except (StepError, subprocess.TimeoutExpired) as exc:
                results.append((case["label"], False, str(exc)))
                print(f"  FAIL  {case['label']}: {exc}")
    finally:
        residue = cleanup()

    print("\n===== 汇总 =====")
    for label, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL':4}  {label}" + (f"  ({detail[:120]})" if detail else ""))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")

    if residue:
        print(f"清理校验失败，残留目录: {residue}")
        return 1
    print(f"清理校验：{PLAYER_ID} 存档目录残留 0")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
