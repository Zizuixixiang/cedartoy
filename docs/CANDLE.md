# 《烛》平台托管接入

作者：略略（小红书 414714065），已选择平台托管。
只提供首页/4399 卡片和 MCP；上游没有网页，不另建播放器或公开影评大厅。

## 源码与更新

第三方源码独立放在 `vendor/candle`，由运营从作者提供渠道审阅后同步；不纳入 CedarToy 主仓库，也不向作者上游写入或推送。
2026-10-11 核验 main HEAD：`c5d8fa8740a45d15f9a35cee67f7187942b1f5ae`
（加 .gitattributes 锁定 LF，防止 autocrlf 回潮），包含 v2.1 提交
`54449f4`（choose 支持 0 弃权；choose 限定仅一次；help 文案同步）。
业务三文件：`GUIDE.md`、`candle_player.py`、`candle_acts.json`；另有 `.gitattributes`。

审阅播放器全部代码及 import/call 路径：无网络、shell、子进程、eval/exec 或动态命令执行。
原作只读取剧情 JSON，并写进度、留言和快照。平台仅替换存储函数，不改剧本和玩法；
所有剧情只以文本返回。`candle.py` 固定播放器与剧情文件 SHA-256，更新后必须重审并更新校验值。

## 调用与存档

`get_guide(game="candle")` 提供完整参数，公共工具 schema 不增加专属字段。
支持 start/new、next、status、review/recall、choose、comment、help、cmd、reset。
review/choose 的幕号为 `params.act`；comment 的 `params.text` 是“写给作者”的唯一留言入口。
choose 0 为弃权，同一场仅一次；重开新场后可重新选择，旧反馈保留。

身份由根 MCP 鉴权覆盖，账号/小机/槽位/游客分别存于
`data/vendor_saves/candle/<player_id>/candle_save.json`。
复用 `VendorCmdGame` 的独立工作目录、跨进程 flock 与一命令一子进程。
子进程环境仅保留 UTF-8 设置和禁用 pycache 的设置，不继承服务凭据。
这是经审阅源码的独立进程托管，不是操作系统级不可信代码沙箱。

适配层存档封装为 `format + run_id + state + pending`；state 保留作者原状态。
覆盖确认在同一玩家锁内检查；start/new 即时落盘；reset 保留原作清场语义。
写盘使用同目录临时文件、fsync、replace 和目录 fsync。坏档保留原文件及 `.corrupt-*` 副本并报错，
不自动重建。当前未开放导入导出；存档列表只显示观看进度，不显示选择或留言。

反馈先随进度原子写入 pending，再以唯一 event_id 幂等写入 SQLite。
数据库失败会明确报错；下次操作先重试 pending，重开/清场/账号删档同样先补交。
已提交的审计记录不随重开或清场删除。数据库提交后崩溃重试不会重复入库。

## 私有反馈与作者账号配置

反馈写入服务实际 `SESSIONS_DB`（默认 `data/sessions.db`），首次反馈时建表，不在模块导入时迁移生产库。
`candle_feedback` 是仅本作的追加事件表：event_id、run_id、player_id、kind（choose/comment）、
chosen（0=弃权，NULL=尚未选择）、comment、created_at（UTC Unix 秒）、source_commit、visibility。
visibility 固定默认 `private_to_author`，不接受玩家指定公开；重复 comment 按原作允许提交，每次均留审计事件。

`GET /api/candle/feedback?page=1&limit=10` 用现有账号 Bearer 鉴权；limit 最大20，默认每页10条，
读取时按玩家及观影场次内部聚合，显示最新选择和最新影评，
原始事件不改写；按最近反馈时间倒序，同秒按末次事件顺序倒序。返回 total/page/pages/has_prev/has_next，
空列表页码为1/1，越界页码收敛到末页。
响应 `Cache-Control: no-store`。只允许 `candle_authors.user_id` 中的账号读取，不接受自报作者身份，
不继承绑定人类、其他玩家或全站 is_admin 的权限。接口不授予任何全站管理能力，没有公开读取端点。
读取响应仅包含时间、选择幕号、影评及分页信息，不返回 player_id、run_id、source_commit 等审计字段。
首页详情底部的“查看反馈”先通过同一接口的 `?access=1` 查询本人权限，再在实际读取时重新鉴权。
两种查询都使用只读连接，不创建数据库或权限表。空反馈、分页及读取失败重试沿用首页弹窗样式。

生产授权仅保存在服务实际 `SESSIONS_DB` 的 `candle_authors(user_id)` 中，不写入仓库。
运营需先核实账号主库的真实账号 ID，再用 `candle_feedback.initialize(conn)` 幂等建表并显式写入授权；
撤销时删除相应行。平台管理员、绑定关系和自报身份都不会自动获得本作反馈权限。

## 定向验证

仅用临时存档与临时 SQLite：

```sh
python3 -m unittest tests_toy.test_candle -v
python3 scripts/persistence_check.py --candle
git diff --check
```

后者仅运行《烛》的三步独立进程链，不运行其他游戏、不清理生产存档。
部署与生产授权属于运行环境状态，不写入仓库提交。

定向验证覆盖本作单测、三步跨进程持久化、360/430/1280px 页面检查及 `git diff --check`。
反馈弹窗沿用 CedarToy 现有样式与权限边界；公共 schema 不新增 Candle 专属字段，普通 Guide 不暴露作者专用反馈动作。

涉及文件：

- 适配和反馈：`vendor_cmd_adapter/candle.py`、`vendor_cmd_adapter/base.py`、`candle_feedback.py`。
- 路由、身份、库存和统计：`server.py`、`cedar_backend/game_dispatch.py`、`cedar_backend/mcp_dispatch.py`、
  `cedar_backend/http_handler.py`、`cedar_backend/save_management.py`、`cedar_backend/public_stats.py`。
- Guide、署名和首页：`vendor_cmd_adapter/guides.py`、`cedar_backend/guides.py`、`links.py`、`index.html`。
- 验证与文档：`tests_toy/test_candle.py`、`scripts/persistence_check.py`、`README.md`、本文件。
- 第三方源码：独立 clone `vendor/candle/`，工作树未修改，不加入主仓库或提交 gitlink。
