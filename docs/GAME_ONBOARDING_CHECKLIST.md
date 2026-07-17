# 新游戏接入检查清单（持久化篇）

来源：2026-07-17 全平台持久化审计（`audit_persistence_report.md`）与最终验收（`final_acceptance_report.md`）。
14 个游戏里 5 个带确认问题、2 个疑似，全部命中下面的模式。新游戏合入前逐项过一遍，每项都要能回答"在哪一行代码保证的"。

背景约束：生产是**一命令一子进程**模型——每条玩家指令都在全新 Python 进程里执行，进程退出后一切未落盘的状态即消失。所有检查项都以此为前提。

## 必查项

- [ ] **1. `new`/`reset` 之后存档必须已落盘，任何参数组合下都不例外。**
  为什么：arcade 的 `new` 允许携带任意 command，搭配只读命令（`look`/`help`）时 runner 先删光旧档、而只读分支不保存——玩家一条指令就永久丢档。reset 路径必须"先建新档、再执行附加命令"，不能依赖后续命令恰好触发保存。

- [ ] **2. `new` 必须走引擎的专用开局入口，不要把 `new_game [seed]` 当文本命令喂给通用 `cmd()`。**
  为什么：fishing 曾把 `new_game 12345` 当普通文本传入，引擎不识别、seed 被静默忽略还返回"未知指令"，只是碰巧因无档兜底才产生了存档。开局语义要显式调用（如 `engine.new_game(seed)`），带 seed / 不带 seed 两种路径都要实测。

- [ ] **3. 冷加载必须完整恢复引擎的全部运行时依赖：RNG 状态、pending 交互上下文、冷却计数等，一个不落地进 `to_dict`/`from_dict`。**
  为什么：market 的 `_pending_chain_step`、`_pending_interaction` 等"等待下一条指令选择"的对象只存在于内存属性，一命令一进程下提示刚返回上下文就消失，下一进程永远无法完成互动。凡是"跨两条指令才闭环"的状态都必须序列化；RNG 要存 state + calls（参考 imitator_td 的完整 RNG snapshot）。

- [ ] **4. 每条改状态指令执行后必须写盘，且写失败必须显式报错，绝不"当次成功、状态未保存"。**
  为什么：workkk 的 `_save_state()` 曾捕获所有写入异常后只打日志、仍向玩家返回成功——磁盘满/只读时玩家以为进度在，实际全丢。写失败要冒泡为明确 error（MCP error / HTTP 500），最稳妥的模式是顶层 `cmd()` 出口统一保存（如 leek、imitator_td）。

- [ ] **5. 存档损坏时必须先备份原档、再在当次输出里显式告警，绝不静默重建。**
  为什么：burger 曾对坏档静默 `fresh_state()` 并立即把新状态写回原文件——坏档被覆盖，进度不可逆丢失且玩家毫无感知；leek 有备份但无告警，玩家面对"凭空新局"一样懵。正确形态：原档原子改名为 `.corrupt`/`.bak` → 重建 → 告警置于当次输出首行（参考 fishing 与修复后的 burger/leek/workkk）。同时要做反向回归：正常存档不得误报、不得误生成备份文件。

- [ ] **6. 写盘必须原子：同目录 `.tmp` 写入 → flush/fsync → `os.replace`。**
  为什么：直接 `open(path, "w")` 写一半时进程被杀/断电，会留下截断的坏 JSON，把"崩溃丢一次操作"升级成"整个存档损坏"。leek 和修复后的 workkk 是现成模板；失败路径要清理 tmp 并抛错。

- [ ] **7. 共享库（SQLite）的读-算-写必须在同一写事务里（`BEGIN IMMEDIATE`），文件存档必须持锁串行。**
  为什么：ciyuwu 曾经 SELECT 在引擎锁之前、UPDATE 在锁之后且无写事务，两个并发请求各自读到同一旧快照、后写的覆盖先写的进度。SQLite 参照 eco/ciyuwu 的 `BEGIN IMMEDIATE` 包住 load→mutate→save；文件存档参照 `vendor_cmd_adapter/base.py` 的 flock 全程持锁。注意全局统一"DB 锁 → 引擎锁"的加锁顺序，避免死锁。

- [ ] **8. 覆盖已有存档的 `new` 必须走 `require_save_confirm` 拦截（无 `confirm=true` 时拒绝执行）。**
  为什么：LLM 玩家可能把"重开"理解得很随意，一次误触 `new` 就永久覆盖长线进度。`vendor_cmd_adapter/base.py:require_save_confirm` 已提供现成实现，接上并提供存档摘要文案即可。

- [ ] **9. 合入前把新游戏加进 `scripts/persistence_check.py` 的 GAMES 配置表，并实际跑通 `python3 scripts/persistence_check.py` 全绿。**
  为什么：以上每一条昨晚都在"看起来能玩"的游戏里实际出过问题——单进程手测发现不了跨进程丢失。回归脚本用三步双进程测试（new → 独立进程改状态 → 独立进程验证保留）+ 存档文件 JSON 可解析检查，把这类问题挡在合入前。配置表只需声明改状态指令、查询指令和断言关键词。

## 测试纪律

- 生产库就是线上真实玩家数据（`data/sessions.db`、`turtle_soup.db`、`data/vendor_saves/`）。手工测试一律用一次性 `guest:<用途>` 身份，测完删干净并核实残留为 0；`persistence_check.py` 固定使用 `guest:regcheck` 并自动清理。
- 动态验证要用独立 `python3 -c` 子进程逐步执行，不要在同一 REPL 里连跑——进程内缓存会掩盖持久化缺陷。
