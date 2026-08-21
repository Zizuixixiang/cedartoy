# 坩埚余响（crucible_echoes）接入说明

## 来源与版本

- 上游仓库：`https://github.com/megabaka404/crucible-echoes.git`
- 平台作者署名：athok（联系方式 5583289470；仓库作者 megabaka404）
- 许可证：上游 `LICENSE`，MIT License，Copyright (c) 2026 Crucible Echoes contributors
- 本地位置：`vendor/crucible-echoes`，保留独立 `.git` 历史；CedarToy 根仓库忽略整个 `vendor/`
- 首次接入版本：`f2bc4e8cc355ca8325b96c34edf991364d6c79b1`（2026-08-20）。克隆时镜像的分支已前进一提交，因此本地以 detached HEAD 明确固定到本次验收版本；完整上游历史和远端分支仍保留，可由更新检测报告后续提交

`origin` 始终保留上游 GitHub URL。VPS 直连 GitHub 失败时，另配 fetch remote：

```text
mirror = https://ghfast.top/https://github.com/megabaka404/crucible-echoes.git
```

`scripts/check_vendor_updates.sh` 会先 fetch `origin`，失败时尝试已存在的 `mirror`。平台不向上游或镜像 push，也不把 vendor commit/pointer 提交进 CedarToy 主仓库。

## 平台结构

接入代码位于 `vendor_cmd_adapter/crucible_echoes.py`。上游源码没有修改；每个 action 都在短命 Python 子进程中导入上游 `src/crucible_echoes`，并由 `VendorCmdGame` 对同一 `player_id` 持排他文件锁。

存档路径为：

```text
data/vendor_saves/crucible_echoes/<player_id>/state.json
```

平台 token 会把身份改写为数值账号 ID，槽 2–5 使用 `id:2` 至 `id:5`；游客使用 `guest:<id>`。因此不同 AI、不同玩家和不同槽位不共享世界。该游戏已加入平台的身份、游客认领、迁档、存档概览、删除、备份、游客清理和防沉迷集合。

完整 `GameState`（包括确定性 RNG 状态）只保存在上述 JSON。写盘使用同目录临时文件、flush/fsync、`os.replace` 和目录 fsync；坏档先改名为带纳秒后缀的 `state.json.corrupt-*`，当次不继续执行原动作，并明确返回恢复警告。`new` 和覆盖式 `import` 必须显式确认已有存档。

## MCP action 与返回边界

调用统一走：

```text
play(game="crucible_echoes", action="...", params={...})
```

支持 `new`、`state`/`status`、`spin`、`choose`、`skip`、`reroll`、`remove`、`inventory`、`use`、`help`、`export` 和 `import`。实际下一步必须以响应 `actions` 为准；`choose`/`remove` 使用 1 起始 `index`，`use` 使用 `item_id`。完整规则由 `get_guide(game="crucible_echoes")` 返回。

上游 `agent_payload()` 的完整状态会随着成分池和数据定义增长，不直接透传给 MCP。平台每轮只返回：

- `state`：回合、金币、订单、token、剩余次数等摘要；
- `decision`：当前待选项和能否跳过；
- `last_board` / `last_log`：当前决策所需的最近结算信息；
- `actions`：此刻可执行的结构化 action；
- 已拥有的物品/精粹摘要。

成分明细只在显式 `inventory` 或当前可 `remove` 时返回；RNG、完整定义目录和完整存档不进入日常响应。需要迁移存档时才使用 `export`/`import`。

## 人类入口

首页 `index.html` 使用现有卡片数据与样式注册该游戏，入口为 `/crucible-echoes/`。`crucible_echoes.html` 沿用平台单文件页面模式，通过 `localStorage.cedartoy_token` 的 Bearer token 调根 MCP，支持五槽、新局、当前动态 actions、存档导入导出与玩法说明。

上游仓库没有封面或图标素材，因此卡片沿用 CedarToy 的默认文字 glyph 机制，没有生成或引入风格不一致的图片。

## 验证

合入或更新后至少执行：

```bash
python3 -m py_compile server.py vendor_cmd_adapter/crucible_echoes.py
python3 -m unittest tests_toy.test_crucible_echoes_integration tests_toy.test_homepage_order
python3 scripts/persistence_check.py
cd vendor/crucible-echoes && python3 run_tests.py
```

另外通过 `127.0.0.1:8002` 根 MCP 实测 `list_games`、guide、play schema、`new -> spin -> reroll/choose/remove/use -> state`，再用第二身份确认隔离；测试使用一次性 `guest:*`，结束后删除对应目录与认领码。
