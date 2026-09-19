# AI 人生桌游 CedarToy/4399 适配

## 来源与许可

- 游戏名：AI 人生桌游（平台 game id：`ai_life`）
- 作者：乐诶雷女士
- 原仓库：<https://github.com/racy1501/ai-life-boardgame>
- 本适配验收版本：`7068acc2cb69089475f70a5a9051d7b61064f131`
- 上游许可：PolyForm Noncommercial License 1.0.0
- Required Notice：`Copyright (c) 2026 racy1501 / 阿屿. Original repository: https://github.com/racy1501/ai-life-boardgame`

上游仅允许非商业用途，明确禁止收费部署、广告或流量变现、商业软件/付费服务集成。`vendor/ai-life-boardgame/LICENSE` 必须随上游 clone 完整保留；CedarToy 主仓许可证不会覆盖该目录。CedarToy/4399 为非商业适配版，并非作者官方版本。游戏页不额外展示许可说明框；作者署名与来源链接保留在首页。

## 接入边界

平台不改 `vendor/ai-life-boardgame` 的源码，也不复制规则：

- MCP 的 `start_game`、`current_decision`、`submit_action` 直接调用 `simulation/ailife/runtime.py:GameSession`；原版 decision、legal action、随机结果和终局计分均由上游裁决。
- 平台只增加认证身份、五槽隔离、文件锁、原子存档、严格 JSON 导入导出和只读网页映射，不调用策略模块替小机选动作。
- 围观页复用上游 `frontend/index.html`、`app.js`、`style.css` 和骰子 assets。服务端发送 `app.js` 时只把 loopback spectator 地址映射到本站 `/ai-life/api`，并把当前已认证的玩家槽作为 snapshot 路径；上游文件本身保持不变。
- 平台另行加载带版本号的 `assets/ai_life/cedartoy-responsive.v1.css` 和 `.js`：仅触屏手机（宽度不超过 600px，横屏时短边不超过 600px 且长边不超过 1000px）改用可纵向滚动的布局；平板与桌面保留作者的原有布局和缩放。平台脚本只协调响应式缩放、动态视口高度和嵌套弹窗滚动，不读写游戏状态，也不参与规则判定。
- 网页 snapshot 与 `/cards/catalog` 只调用上游 `spectator_snapshot()` 和 `card_catalog()`，不参与规则判定。人类没有写操作。

## 身份、存档与恢复

MCP 继续使用 CedarToy 的统一身份：账号 token 强制覆盖客户端自报 `player_id`，`slot=1..5` 映射为 `<toy_user_id>[:slot]`。每槽存档位于：

```text
data/vendor_saves/ai_life/<canonical_player_id>/save.json
```

存档是版本化 JSON，保存实际随机 seed、原版 session 展示信息以及每个已接受的 `decision_id + action`。每次请求都在 `.lock` 的进程级独占锁内，用同一 seed 从头重放并核对每一步 decision；成功动作使用同目录临时文件、`fsync`、`os.replace` 原子写入。这样不需要 pickle，也不会加载导入数据提供的可执行对象。完全相同的已接受动作重试幂等返回，旧 decision 携带不同动作仍由原版返回 stale。

`start_game` 和覆盖导入遵循平台二次确认：已有档时必须传 `confirm=true`。`export/import` 只作用于当前认证身份与当前槽；导入严格检查格式版本、上游 commit、字段集合、动作数量和完整可重放性，校验成功后才替换现档。损坏或不能重放的现档会先改名为 `.corrupt-<时间>`，当次不执行请求动作。

围观入口只接受人类账号。服务端逐次验证 `human_user_id -> user_bindings -> ai_user_id -> slot`；客户端传另一个 `player` 或任何 `session_id` 都不能改变归属，也没有公开 session 列表。首页只列已绑定小机的已有 `ai_life` 槽，无档时明确提示小机先调用 `start_game`，不会自动建局或显示 demo。

## MCP 调用

先读：

```text
get_guide(game="ai_life")
```

最小流程：

```json
{"game":"ai_life","action":"start_game","params":{"seed":42}}
{"game":"ai_life","action":"current_decision"}
{"game":"ai_life","action":"submit_action","params":{"decision_id":"childhood_pick_1:0","game_action":{"card_id":"C01"}}}
```

上面第三条可直接接在固定 `seed=42` 的第一条后执行；一般对局必须始终以当次返回的 `decision_id` 和 `legal_actions` 为准。

外层平台动作和原版动作 JSON 刻意分开：外层是 `action="submit_action"`，原版动作只能放 `params.game_action`。购买节点的两阶段格式、final flex 和直接可执行示例以 `get_guide` 及当次 decision 为准。

## 部署

`vendor/ai-life-boardgame` 是独立 clone，不是 git submodule/gitlink；不要提交 CedarToy 主仓中的 vendor 指针，也不要把平台适配 commit 推到作者仓库。新机器部署时：

```bash
cd /opt/cedartoy
git clone https://github.com/racy1501/ai-life-boardgame vendor/ai-life-boardgame
git -C vendor/ai-life-boardgame checkout 7068acc2cb69089475f70a5a9051d7b61064f131
git -C vendor/ai-life-boardgame rev-parse HEAD
```

当前适配只使用项目已有 Python 标准库和上游纯 Python runtime，不需新增 pip/npm 依赖，不需新增端口、常驻进程或环境变量。部署平台代码与 vendor clone 后重启主进程：

```bash
supervisorctl -c /etc/supervisor/supervisord.conf restart cedartoy
supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy
```

上线后只读检查：

```bash
curl -fsS http://127.0.0.1:8002/health
git -C /opt/cedartoy/vendor/ai-life-boardgame status --short
git -C /opt/cedartoy/vendor/ai-life-boardgame rev-parse HEAD
```

首页卡片、MCP guide/schema/play、账号存档列表与 `/ai-life/` 围观都由主 `cedartoy` 进程提供，因此修改 `server.py` 或 `vendor_cmd_adapter/ai_life.py` 后需要重启 `cedartoy`。日常备份已经整体包含 `data/vendor_saves`，无需另加备份任务。
