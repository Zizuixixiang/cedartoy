# Memoria-Station 五关引擎全指令冒烟扫雷报告

日期：2026-07-17
背景：昨晚第五关线上出现 `NameError: name 'fg_label' is not defined`（已由 `vendor_cmd_adapter/memoria.py` 的 `_ForgettingLabelProxy` 补丁临时挡住）。本次对五个关卡引擎做全指令冒烟，目标是找出同类上游代码缺陷，并确认 fg_label 补丁的覆盖面。

## 测试范围与方法

- 引擎：`vendor/Memoria-Station` 五关 `detective.py` / `detective_l2.py` / `detective_l3.py` / `detective_l4.py` / `detective_l5.py`。
- 全程在 `/tmp/memoria_smoke` 沙盒进行：五个引擎文件**各复制一份**到沙盒后再 import（引擎的存档/心跳路径按 `__file__` 计算，直接 import vendor 原文件会在 vendor 目录写文件，复制可彻底隔离）。import 后按 `vendor_cmd_adapter/memoria.py::configure_module` 的方式重定向 `_SAVE_PATH` / `_HEARTBEAT_PATH` / `_L4_SAVE_PATH`，并清 `_RNG` / `_HEARTBEAT_HINT`，与生产链路一致。
- 每关一个全新 Python 进程，`new_game()`（二/三/四关带 `normal` 难度参数）后把 help 列出的**全部指令**逐条执行：无参形式、真实参数（从 look/go/talk/zones 列表取 1–2 个真实对象）、未知参数、空指令、未知指令、`;` 链式指令（第一关）。终局类指令（accuse / surrender / take 药瓶 / leave / backtrack）在重开新局后单独执行。另测冷路径：import 后不 `new_game` 直接 `cmd()`、无存档文件直接 `load`。
- 每条指令用 try/except 包裹，区分「引擎正常错误提示文本」与「未捕获 Python 异常」。
- 第五关按要求跑两遍：不加 fg_label 补丁（上游裸引擎）与加补丁（复刻 adapter 的 `_ForgettingLabelProxy`）。
- 防剧透约束已遵守：未解码、未阅读任何关卡 `_BLOB` 数据；未尝试破解任何谜题（密码锁只输入了明显错误的值）；本报告只记录指令名、异常类型、栈内函数名与行号，不含任何谜底或剧情内容。
- 数据安全：测试前后对 `vendor/Memoria-Station` 全部文件做 md5 快照比对，**逐字节一致**（未写入、未修改任何 vendor 文件）；未碰 git、未重启服务；测试后沙盒已整体删除。

## 结果总览

| 关卡 | 引擎 | 试次 | 未捕获异常 | 结论 |
|---|---|---|---|---|
| 1 蓝玫瑰庄园 | detective.py | 37 + 边角 | 0 | ✅ 无崩溃；⚠️ backtrack 资源逻辑缺陷（见缺陷 2） |
| 2 午夜特快 | detective_l2.py | 46 + 6 边角 | 0 | ✅ 无崩溃 |
| 3 褪色车站 | detective_l3.py | 40 | 0 | ✅ 无崩溃 |
| 4 循环车站 | detective_l4.py | 31 + 边角 | 0 | ✅ 无崩溃；⚠️ backtrack 与 L1 同款缺陷（见缺陷 3） |
| 5 档案室终点（无补丁） | detective_l5.py | 17 + 3 边角 | **14** | ❌ 全部为同一处 `NameError: fg_label` |
| 5 档案室终点（fg_label 补丁） | 同上 | 19 + 9 边角 | 0 | ✅ 补丁全覆盖，状态栏文案渲染正确 |

冷路径（不 new_game 直接 cmd、无档 load）：五关均有兜底，返回错误提示文本不崩溃——唯一例外仍是第五关无补丁时的 `look`（同一个 fg_label NameError）。空指令、未知指令、未知参数在五关全部返回正常提示文本。help 列出的指令与 dispatch 表无失配（没有"help 里有但引擎不认"的指令）。

## 确认缺陷

### 缺陷 1（高，即昨晚线上故障的根因）：L5 `_status_bar` 引用未定义全局名 `fg_label`

- **现象**：`detective_l5.py` 的 `_status_bar`（blob 内第 633 行）引用了从未定义的模块级名字 `fg_label`，任何会拼接状态栏的指令一律抛 `NameError: name 'fg_label' is not defined`。
- **波及面**（无补丁时逐条实测，14/14 均崩，调用链栈顶一致）：

  | 指令 | 调用链（函数:blob 行号） |
  |---|---|
  | `zones` | cmd:1213 → _dispatch:1180 → _cmd_zones:1109 → _status_bar:633 |
  | `look`（含冷路径） | cmd:1213 → _dispatch:1168 → _cmd_look:700 → _status_bar:633 |
  | `look <未知区域>` | … → _cmd_look:710 → _status_bar:633 |
  | `look <真实区域>` | … → _cmd_look → _status_bar:633 |
  | `read` | … → _cmd_read:784 → _status_bar:633 |
  | `read <未知物品>` | … → _cmd_read:794 → _status_bar:633 |
  | `unlock` | … → _cmd_unlock:977 → _status_bar:633 |
  | `unlock drawer <错误码>` | … → _cmd_unlock:1016 → _status_bar:633 |
  | `unlock <未知目标> <码>` | … → _cmd_unlock:1052 → _status_bar:633 |
  | `close 卷宗` | … → _cmd_close:1083 → _status_bar:633 |
  | `close <未知对象>` | … → _cmd_close:1073 → _status_bar:633 |
  | `take 药瓶` | … → _cmd_take:1063 → _status_bar:633 |
  | `take <未知对象>` | … → _cmd_take:1056 → _status_bar:633 |

  不崩的只有不经过 `_status_bar` 的：`help`、`status`、`leave`、未知指令、空指令。也就是说**无补丁时第五关九条 help 指令里只有四条可用**，新开局第一句 `look` 就会崩——与昨晚线上现象吻合。
- **补丁覆盖面确认**：加 adapter 的 `_ForgettingLabelProxy` 后同一套全指令 + 真实区域遍历 + read/unlock/结局路径共 28 试次 **0 异常**，且状态栏「遗忘」标签能按 `_STATE.forgetting_level` 正确渲染（不是空字符串兜底）。补丁覆盖面完整。
- **上游修复建议**：`_status_bar` 内改用局部计算（引擎自身已有 `_forgetting_label()` 函数，`status` 指令走的正是正确路径），或在模块级定义 `fg_label`。这是变量名笔误级别的修复。

### 缺陷 2（中）：L1 `backtrack` 在没有存档点时仍扣减主动回溯次数

- **现象**：第一关新开局（尚无任何存档点）直接 `backtrack`，返回「🔄 没有可用的存档点」，但「主动回溯剩余」照扣：连续 3 次调用后计数 7→6→5→4，`status` 确认为 4。回溯是有限资源（hell 难度仅 3 次），空扣可能把玩家资源烧光。
- **对照**：L2/L3 开局会自动带一个初始存档点，`backtrack` 能成功回到该点并正常扣 1 次，无此问题。

### 缺陷 3（中）：L4 `backtrack` 同款空扣，且成功回溯反而不扣次数

- **现象 A**：第四关开局第一次 `backtrack` 成功「已回溯至存档点」但计数不减（仍 7）；
- **现象 B**：随后再 `backtrack` 提示「⚠️ 没有可用的存档点」，计数却开始扣（7→6→5）。
- 与 L2/L3（成功才扣、失败不扣）行为正好相反，疑似判定与扣减的先后顺序写反。

## 平台侧修正

- **修正位置**：仅修改 `vendor_cmd_adapter/memoria.py` 的 `RUNNER_CODE.configure_module`，未修改任何 `vendor/Memoria-Station` 文件。
- **作用范围**：只在 `level == "1"` 或 `level == "4"` 时包装模块 `_dispatch`；仅当当前分段指令的首词为 `backtrack` 时读取状态并纠偏，其它指令立即直通原 dispatcher。选择 `_dispatch` 而非 `cmd` 也覆盖了 L1 的 `;` 链式指令。
- **判断与修正**：记录调用前 `_STATE["active_backtracks"]` 及 `_snapshots` 数量，调用原 dispatcher 后结合「已回溯…存档点」/「没有可用的存档点」等返回文本和存档点是否被消费判断成败。成功时将剩余次数校准为调用前减 1，失败时恢复为调用前数值；引擎已先自动存档而平台发生纠偏时，再显式调用 `_save()` 把修正值落盘。纠偏分支还会用关卡专属的整行正则，仅将 L1 `剩余主动回溯：N次` 或 L4 `剩余回溯次数：N` 中的 `N` 同步为修正后的真实值，不改响应中的其它数字。
- **验证结果**（2026-07-17）：
  - `/tmp` vendor 副本沙盒与 `vendor_cmd_adapter.memoria.play()` 生产子进程路径各跑一遍：L1 连续 3 次无存档点回溯均失败且保持 7；L4 首次成功后降至 6，随后连续 2 次失败均保持 6；存档 JSON 中的 `active_backtracks` 与状态输出一致。
  - 响应文本同步补测：L1 连续 2 次失败均显示 `剩余主动回溯：7次`；L4 首次成功显示 `剩余回溯次数：6`，随后失败仍显示 6；四次响应均与对应存档一致。
  - L2 对照执行一次 `backtrack`，沙盒与生产路径的原生输出和计数变化一致，确认未安装或触发 L1/L4 补丁。
  - `python3 scripts/persistence_check.py`：8/8 PASS，测试存档清理后残留 0。
  - `python3 -m py_compile vendor_cmd_adapter/memoria.py`：PASS。

## 低置信度观察（不作为缺陷上报，供参考）

- L2 help 中 `look <物品> <操作>` 给了两个操作示例，但对若干真实物品逐一尝试均返回「未知操作」提示文本（非崩溃）。可能是操作需特定游戏状态才解锁，也可能是示例与实现脱节；本次只试了少量物品，无法定论。
- L1 `look <物品>; status` 链式指令、二次调查（同一物品 look 两次出深入结果）均正常。

## 覆盖范围说明（未触达路径）

- 各关"指控正确/解谜正确"的胜利分支、L5 密码锁解锁后的分支及服药结局的实际触发，均需要真实谜底才能到达。按防剧透约束本次**有意不求解**，这些分支的深层代码路径未被覆盖。冒烟结论仅保证:所有指令的入口、参数解析、错误分支及可无谜底到达的结局(坏结局/悬案/放弃/直接离开)无未捕获异常。

## 建议上报作者的问题清单（可直接转发）

> 您好，我们在对 Memoria-Station 五个关卡引擎做例行全指令冒烟测试（仅通过 `cmd()` 接口黑盒执行，未阅读 blob 剧情数据）时发现以下问题，供参考：
>
> **1. 第五关：状态栏引用未定义变量，大部分指令直接抛异常（严重）**
> - 现象：`detective_l5.py` 中 `_status_bar`（解包后约第 633 行）引用了未定义的全局名 `fg_label`，所有带状态栏的指令（`look` / `zones` / `read` / `unlock` / `close` / `take`，含各自的错误参数分支）统一抛 `NameError: name 'fg_label' is not defined`；仅 `help` / `status` / `leave` 可用。
> - 最小复现：
>   ```python
>   import detective_l5
>   detective_l5.new_game()
>   detective_l5.cmd("look")   # NameError: name 'fg_label' is not defined
>   ```
> - 备注：`status` 指令能正确显示「遗忘程度」，说明引擎里已有正确的标签函数，疑似 `_status_bar` 里的变量名笔误。
>
> **2. 第一关：无存档点时 `backtrack` 仍消耗主动回溯次数（中）**
> - 现象：新开局尚无存档点时执行 `backtrack`，返回「没有可用的存档点」，但主动回溯剩余次数每次仍 -1（7→6→5→4）。回溯次数是有限资源（hell 难度仅 3 次），可被空操作耗尽。
> - 最小复现：
>   ```python
>   import detective
>   detective.new_game()
>   detective.cmd("backtrack"); detective.cmd("backtrack")
>   detective.cmd("status")    # 主动回溯剩余：5 次（应仍为 7）
>   ```
>
> **3. 第四关：`backtrack` 扣次数时机疑似写反（中）**
> - 现象：开局第一次 `backtrack` 成功回到「游戏开始」存档点但**不**扣次数；之后无存档点可回时提示失败却**开始**扣次数（7→6→5）。与第二、三关「成功才扣、失败不扣」的行为相反。
> - 最小复现：
>   ```python
>   import detective_l4
>   detective_l4.new_game("normal")
>   detective_l4.cmd("backtrack")  # 成功，剩余 7
>   detective_l4.cmd("backtrack")  # 失败提示，剩余 6
>   detective_l4.cmd("backtrack")  # 失败提示，剩余 5
>   ```
>
> 其余两关（二、三关）全指令冒烟（含未知参数、空指令、冷启动无档 load）未发现未捕获异常，错误兜底文本均正常。

---
*测试环境：/tmp 沙盒内引擎副本，存档路径全部重定向;vendor 目录测试前后 md5 全量比对一致，未做任何修改。*
