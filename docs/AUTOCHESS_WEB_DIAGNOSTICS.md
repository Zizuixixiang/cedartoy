# 弈间网页故障诊断

网页 API 使用独立限流桶：每个已鉴权人类账号每 60 秒最多 240 次，所有四个网页 API 共用。与 Duel 网页及根 MCP 配额隔离；身份、绑定和路由授权不变。通过服务器既有带锁滑动窗口实现，不复用 Duel 专属路由白名单。

## 工单 16 的临时诊断

默认关闭。需要主助手显式写入 `data/autochess_web_diagnostics_activation.json`：

```json
{"account_id":12635,"enabled_at":1793820000,"expires_at":1793906400}
```

以上时间仅为格式示例，不可照抄。部署时使用当前 Unix 秒，期限至多为开始后 86400 秒。文件每次目标请求读取，绝对到期后自动停止；重启不延长期限。

- 仅正常通过现有人类鉴权的账号 12635 会记录，玩家提交的 ID 不参与归属。
- 文件为 `data/autochess_web_diagnostics.jsonl`，0600；最大 1 MiB，保留一份 `.1` 轮转，总计约 2 MiB。
- 每分钟至多 120 次目标请求，每次通常两条：`authenticated`、`response`。未知内部异常额外使用 `exception`；`response` 表示服务端准备响应，不保证客户端收到。
- 字段仅服务端随机 request_id、Unix 时间、目标账号 ID、四条固定 API 路由、固定阶段、耗时、HTTP 状态及固定错误类别。绝不记录 Token、请求头、查询串、请求正文、姓名、IP 或异常正文。
- 鉴权前错误、浏览器网络失败、静态资源请求以及其他用户请求不记录，也不强行归属。无目标记录不能证明玩家未请求。文件不可写时不影响业务请求。
- 修复已上线并完成校验后，再请玩家重试、报告失败日期时间与时区、设备/浏览器及截图。不要索取 Token 或完整带凭据地址。

修改由主服务导入，部署只需按现有流程重启 `cedartoy`，不必重启 AutoChess 卫星或 Duel 网关。启用与清理本次诊断均由主助手操作；排障结束确认不再需要证据后，移除激活文件和这两个日志文件，不碰其他 data 文件。

测试：

```sh
python3 -m unittest tests_toy.test_autochess_web_diagnostics
AUTOCHESS_TEST_NODE=/opt/cedartoy-runtime/node22/bin/node python3 -m unittest tests_toy.test_autochess_adapter tests_toy.test_autochess_web tests_toy.test_autochess_admin_stats tests_toy.test_autochess_async_edge tests_toy.test_autochess_homepage_stats tests_toy.test_duel_web_rate_limit
```

所有测试使用临时数据、合成凭据或本机隔离测试服务，不使用生产玩家凭据。
