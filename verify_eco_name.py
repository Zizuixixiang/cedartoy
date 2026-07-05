# -*- coding: utf-8 -*-
"""最小验证：eco 引擎 name 命令的三种写法与歧义分支。

仿照 eco_adapter/handler.py 的做法直接注入 engine._STATE，
屏蔽 engine 的文件读写，不碰真实存档 eco_save.json。

用法：python3 verify_eco_name.py
"""
import sys

from eco import engine

engine.save_state = lambda state: None  # 不落盘


def make_state(settlers):
    state = engine.fresh_state(1)
    for name, day, nick in settlers:
        s = engine._new_settler_dict(name)
        s["arrive_day"] = day
        s["nickname"] = nick
        state["settlers"].append(s)
    return state


def run(state, command):
    engine._STATE = state
    try:
        return engine.cmd(command)
    finally:
        engine._STATE = None


CASES = [
    # (说明, 定居者列表[(物种, arrive_day, 昵称)], 指令, 期望出现的文字, 不应出现的文字)
    ("无昵称+仅一位该物种：纯物种名应成功（原 bug 场景）",
     [("流浪乌龟", 25, None)],
     "name 流浪乌龟 礼拜", "有了名字：礼拜", "用法："),
    ("完整格式 [D-N] 物种 昵称",
     [("流浪乌龟", 25, None)],
     "name [D-25] 流浪乌龟 礼拜", "有了名字：礼拜", "用法："),
    ("省略物种：[D-N] 昵称（该天仅一位）",
     [("流浪乌龟", 25, None)],
     "name [D-25] 礼拜", "有了名字：礼拜", "用法："),
    ("已有昵称也能用纯物种名改名（与无昵称行为一致）",
     [("流浪乌龟", 25, "旧名")],
     "name 流浪乌龟 礼拜", "有了名字：礼拜", "用法："),
    ("同物种多位：纯物种名应要求带 [D-N]",
     [("流浪乌龟", 25, None), ("流浪乌龟", 40, None)],
     "name 流浪乌龟 礼拜", "请带上编号", "有了名字"),
    ("同物种多位：带 [D-N] 应成功",
     [("流浪乌龟", 25, None), ("流浪乌龟", 40, None)],
     "name [D-40] 流浪乌龟 礼拜", "有了名字：礼拜", "用法："),
    ("同日多位不同物种：[D-N] 昵称应要求写明物种",
     [("流浪乌龟", 25, None), ("翠鸟", 25, None)],
     "name [D-25] 礼拜", "请写明物种", "有了名字"),
    ("不存在的定居者：应提示查 status",
     [("流浪乌龟", 25, None)],
     "name 苍鹭 大长腿", "池塘里没有定居者「苍鹭」", "有了名字"),
    ("[D-N] 对不上：应提示没有该天的定居者",
     [("流浪乌龟", 25, None)],
     "name [D-99] 流浪乌龟 礼拜", "没有第99天来的「流浪乌龟」", "有了名字"),
]

failed = 0
for desc, settlers, command, want, forbid in CASES:
    out = run(make_state(settlers), command)
    ok = want in out and forbid not in out
    print("%s %s" % ("PASS" if ok else "FAIL", desc))
    print("     > %s" % command)
    print("     < %s" % out.replace("\n", " / "))
    if not ok:
        failed += 1

print()
if failed:
    print("共 %d 例失败" % failed)
    sys.exit(1)
print("全部 %d 例通过 ✅" % len(CASES))
