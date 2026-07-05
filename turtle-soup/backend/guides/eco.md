# 瓶中生态 🌿 说明

这是一个文字生态模拟游戏。你是造物主，面前是一个**空池塘**——往里放什么、什么时候放、放多少，全由你决定。生态会按自己的规律演化（种群增长、捕食、季节、环境变化），你只需观察、干预、承受后果。没有积分，没有通关，鱼会死、水会臭、乌龟可能在暴雨后出现。

通过根 MCP 聚合层的 `play(game="eco", action=...)` 调用，`cedartoy/server.py` 会在本进程内转换为 JSON-RPC 并调用 eco handler。存档按 `player_id` 持久保存在服务端。

## 怎么玩

1. 先 `eco_new` 开一局，得到一个空池塘。
2. 然后用下面四个语义化工具推进：`eco_observe`（观察）、`eco_act`（干预）、`eco_info`（查看）、`eco_save`（存档）。
3. 别急着查攻略——自己摸索食物链是最有趣的部分。

## 可用 action

- `eco_new`：开一局新池塘（会重置已有存档）。
  - `player_id`（1-10 位字母数字）、`seed`（可选整数随机种子）。
- `eco_observe`：观察池塘。
  - `action`：`observe`(推进一天) / `wait`(连续推进) / `gaze`(凝望不推进) / `look`(查看详情)。
  - `days`：wait 推进天数 1-7，默认 1。
  - `target`：look 的物种名或季节名。
- `eco_act`：干预池塘。
  - `action`：`summon`(投放) / `remove`(取走) / `feed`(投喂) / `clean`(换水) / `crack`(凿冰·冬季) / `shelter`(铺落叶·冬季) / `choose`(做选择) / `name`(给定居者取名)。
  - `species`：物种名（summon/remove 用）。
  - `quantity`：数量（summon/remove/feed 用，默认 10/10/1）。
  - `option`：1 或 2（choose 用）。
  - `settler`：定居者标识（name 用）：物种名如「翠鸟」、[D-N] 编号如「[D-5]」、或「[D-5] 翠鸟」均可；同物种有多位定居者时须带 [D-N]（编号见 status）。
  - `nickname`：要取的昵称，如「小蓝」（name 用）。
- `eco_info`：查看信息。
  - `action`：`status`(数据面板) / `folio`(万物志) / `chronicle`(年鉴) / `encyclopedia`(图鉴与成就) / `trends`(趋势图)。
  - `scope`：chronicle 范围 `recent` / `all`，默认 recent。
- `eco_save`：存档管理。
  - `action`：`export`(导出) / `import`(导入)。
  - `mode`：export 模式 `full`(完整) / `lite`(精简) / `story`(年鉴故事)，默认 full。
  - `save_data`：import 用的 base64 存档字符串。

## 示例

```json
{"game":"eco","action":"eco_new","player_id":"u123","seed":42}
```

```json
{"game":"eco","action":"eco_act","params":{"player_id":"u123","action":"summon","species":"水藻","quantity":50}}
```

```json
{"game":"eco","action":"eco_observe","params":{"player_id":"u123","action":"wait","days":7}}
```

```json
{"game":"eco","action":"eco_info","params":{"player_id":"u123","action":"status"}}
```
