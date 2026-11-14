# MCP 连接诊断

覆盖 8003 gateway 的 MCP POST 入口及 8004 主服务（含 GET event-stream）。
只做 Token 的只读归属观察，不修改鉴权、活跃时间、协议返回、工具 schema 或转发内容。
这是有界、best-effort 的本机诊断；没有日志不能证明请求没有抵达服务器。

## 配置与热更新

配置文件为 `data/mcp_diagnostics_config.json`，由本机运营维护，不提供玩家/MCP 管理接口。
推荐格式如下（`expires_at` 是 **Unix 秒整数**；示例时间不能直接当作新的七天授权）：

```json
{
  "mode": "allowlist",
  "enabled": true,
  "accounts": [
    {"account_id": 10604, "expires_at": 1800604800},
    {"account_id": 13410, "expires_at": 1800604800}
  ]
}
```

- 最多 8 项；ID 必须是 `0 < id < 2^63` 的整数，不接受布尔、浮点、字符串或重复 ID。
- 每项必须显式填写绝对到期时间，范围 `1..253402300799`，不接受相对时长或缺省值。
  过期项仍可保留在配置中，但不采集；重启、重新读取同一文件不会续期。
- 严格检查所有字段，包括 `enabled=false` 时的条目。未知字段、重复 JSON 键、坏 JSON、
  超过 4096 字节、非普通文件、不可读或悬空链接均关闭诊断，不退回旧授权。
  `enabled=false` 或空 `accounts` 可关闭全部采集。
- 每层在有诊断候选请求/在途写入时，**最多每 5 秒读取一次配置**，能识别原子替换；
  没有每请求 stat/read，也没有后台轮询。正常新增/移除/续期在下一次到期检查时生效，
  通常不超过 5 秒（无流量则等下一次请求）。无效/不可读配置立即撤销缓存授权，
  重试间隔 10、20、30 秒封顶；有效配置恢复后重置为 5 秒。
  显式关闭也可进入最长 30 秒退避，因此恢复采集可能等到 30 秒后的下次请求。
- 单一刷新线程读取文件，其他线程不排队且跳过诊断。刷新期间不沿用旧授权；
  账号绝对到期时间在内存中逐次检查，不受五秒轮询间隔影响。
  移除/无效配置被发现后，在途记录也停止；已开始的文件写入不能撤回。
  两层独立检查，切换时可能有一个检查间隔的差异。

## 日常操作

仓库根下运行本机脚本；它严格校验、以 0600 临时文件原子替换配置，并用本机锁串行化修改。
默认新增/续期为操作时刻起 **7 天**，`--days 1..30` 可显式调整；服务不会自动生成或延长授权。
不要与手工编辑同时操作。脚本只打印名单和期限，不接触 Token、数据库或日志。

```sh
# 首次启用/迁移：set 明确替换完整名单，以下同时保留 13410、恢复 10604
python3 scripts/mcp_diagnostics_config.py set 10604 13410
# 查看原始绝对期限
python3 scripts/mcp_diagnostics_config.py show
# 新增一个尚未列入的账号（已有项即使过期也必须显式 renew）
python3 scripts/mcp_diagnostics_config.py add 12345
# 仅续期指定账号，其他账号到期时间保持不变
python3 scripts/mcp_diagnostics_config.py renew 10604
python3 scripts/mcp_diagnostics_config.py --days 14 renew 13410
python3 scripts/mcp_diagnostics_config.py remove 10604
```

`set` 会重新授权所列的全部账号，不能当作日常查看/单项续期操作。
`add/remove/renew` 只接受已存在的多账号配置；旧单账号迁移必须明确 `set` 完整名单。
停用时移除全部条目或原子写入 `enabled=false`，不要依赖删除配置文件。

多账号日志按层与账号分开，示例：

```sh
tail -n 30 data/mcp_edge_diagnostics.jsonl.account-10604
tail -n 30 data/mcp_main_diagnostics.jsonl.account-13410
```

每个文件独立轮转 `.1` / `.2`；未知身份共用每层的
`mcp_edge_diagnostics.jsonl.allowlist.unknown` / `mcp_main_diagnostics.jsonl.allowlist.unknown`。
旧基名、旧 `.unknown` 及它们的 `.1/.2` 在多账号模式中**不迁移、不删除、不追加**。
已移除账号的证据也保留。到期不删除日志；容量轮转不承诺保留七天证据。

## 上线、兼容与回滚

本次开发不修改生产配置/日志，不重启或部署。主助手审阅后执行：

1. 核实两个服务实际 `TURTLE_SOUP_DB` 为同一账号主库，服务可读配置、写诊断目录；
   仅支持每层一个写入进程（现行单 gateway worker）。保存现行配置的受限权限副本供回滚。
2. **先保留当前 `{"mode":"persistent","enabled":true,"account_id":13410}`，部署代码并重启一次两层**，
   新代码兼容该授权，13410 不因先换配置而停采：

   ```sh
   supervisorctl -c /etc/supervisor/supervisord.conf restart cedartoy cedartoy-duel-gateway
   supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy cedartoy-duel-gateway
   ```

3. 再执行 `python3 scripts/mcp_diagnostics_config.py set 10604 13410`，生成当时起七天的授权。
   此后新增、移除、续期都不再需要重启。等待用户本人正常请求，只读核对两层新文件中的
   时间、账号、阶段；不登录/换 Token 做测试。
4. 回滚时先原子恢复保存的单账号 persistent 配置，再回滚模块并重启上述两个服务。
   旧代码不认识 allowlist，不能只回滚代码却留下新配置。多账号日志保留，不合并到旧日志。

兼容规则：单账号 `persistent` 的三字段配置继续表示显式无限期授权，保持旧日志路径和轻量采样。
仅在进程尚未见过显式配置且配置不存在时，考虑旧 `data/mcp_diagnostics_activation.json`：
严格三个键 `account_id=10604`、整数 `enabled_at`、`expires_at=enabled_at+86400`，且当前在时间窗内。
旧 24h 到期后停止身份查询和在途写入，重启不延长，也不会转为 persistent。
有效旧激活文件是默认关闭的唯一兼容例外。显式配置一旦被进程观察到，随后删除也不回退；
但重新启动时文件不存在仍会检查旧激活文件，因此停用应保留显式关闭配置。
旧 24h 保留逐请求只读查询、目标每分钟 120 请求、未知每分钟 10 请求的采样方式。

## 生产开销上限

以下均为**每层、每进程**；两层各自计数，配额不会随账号数无界增长。

| 项目 | 上限 / 行为 |
| --- | --- |
| 名单 | 最多 8 个账号；共享 256 项身份缓存，固定 5 秒 TTL |
| 每账号入口 | 每 10 秒最多 1 请求，每分钟最多 6 请求 |
| 每账号异常补录 | 未入选入口的异常每分钟最多 12 条结束摘要，独立于正常采样 |
| 每账号记录 | 入口样本每分钟最多 48 条，异常摘要最多 12 条 |
| 未知身份 | 每 300 秒最多 1 请求、8 条记录；不归属任何目标 |
| 总记录 | 常驻/多账号每分钟最多 128 条；旧 24h 最多 1040 条 |
| 只读查库 | 新/过期非目标缓存合计每分钟 60 次；已缓存目标每账号刷新 12 次、总刷新 48 次 |
| 文件容量 | 每个当前文件最多 1 MiB，加 `.1/.2` 共最多 3 MiB；各账号独立轮转 |
| 总磁盘 | 每层最多 24 MiB，两层共 48 MiB，计入所有旧日志与已移除账号文件 |
| 写失败 | 文件分别退避 30 秒；总量盘点失败全层退避 30 秒，不泄露错误正文 |

总量写锁不排队，缓存/配额锁不覆盖文件 I/O。每条已准入记录在总锁内核算新增/轮转回收字节，
最多每 30 秒重新盘点一次目录；目录超过 4096 个条目或无法盘点时停止写入。
达到总磁盘上限就丢弃记录，不自动删除旧证据；需运营另行确认后归档相关诊断文件。
若历史文件已超过上限，不再扩大占用。禁止额外进程/外部脚本同时追加这些文件；
外部改动在下次盘点识别，额度不承担其他进程写入的磁盘增长。

分钟计数使用 monotonic 固定窗口，边界可能短时突发；重启重置内存额度，但不重置磁盘上限或绝对期限。
全局额度耗尽可能省略某个账号的记录，这是总开销保护，不保证所有目标每分钟都写满独立额度。
单账号洪峰不能动用其他账号的入口/异常/刷新额度，也不能轮转其他账号文件。
未知身份的限频在配置失效、恢复或模式切换后也不重置。
新 Token 洪峰可能耗尽共享查库额度；查库配额不足/锁忙直接跳过，不冒充未知、不沿用过期身份。
非目标账号不写日志，网关跳过其额外 JSON 解析和后续工作线程调用。

网关配置读取、身份查询和日志写入都在工作线程执行；主服务是同步 worker。
并发触发配置检查只安排一次工作线程任务，其余请求在刷新期间跳过诊断。
无无界队列、后台扫描或逐请求 fsync；没有故障磁盘下的 I/O 时延保证。

## 身份、隐私与证据解释

- 归属只读查询 `ai_access_tokens` / `legacy_ai_token_hashes`，支持路径 Token 优先、其次 Bearer。
  不信任 RPC 自报 ID/JWT 自报 user_id，不使用绑定人类账号；SQLite `mode=ro`、`query_only`，
  锁等待最多 20ms。未知、重复匹配、查询失败均不归属账号。
- 不记录 Token、Token 哈希、原始路径、IP、UA、Cookie、请求参数/body、RPC ID、错误正文或堆栈。
  凭据摘要只作为有界内存缓存和 DB 比较键。只记录服务端随机 request_id、时间、固定 transport/
  http_method/method/scope/stage、elapsed_ms、http_status、rpc_result、rpc_error_code、identity，
  目标匹配时附 account_id。常驻记录附 `mode=persistent`、identity_age_ms、entry_sampled、phases_ms、
  结束 reasons；allowlist 使用同一轻量记录结构。
- method 仅保留代码中的 MCP 标准方法白名单，其他为 `other`；RPC 错误码仅接受有符号 32 位整数。
  `current_hash_match` 不等于鉴权成功；撤销、删除、generation_mismatch 等只描述只读关联状态。
  Token 轮换/归属变化最多滞后缓存 TTL 5 秒，请求后续阶段保留入口观察；真实鉴权独立执行。
- 入选请求在读取 body 前写 entered / identity_checked；parsed、proxy_dispatch / duel_dispatch /
  autochess_dispatch、response_ready 标记观测位置。phases_ms.response_started 仅表示设置响应状态，
  completed 仅表示处理结束，都不证明客户端收到了响应。
- 异常含 exception/disconnected/cancelled、HTTP >= 400、RPC/tool/transport error，或总耗时 >= 5000ms。
  未入选入口的异常按额度补录；无 finish 可能是仍在途、进程退出、取消或写失败。
  未采样且永不结束的请求可能没有记录；Duel 正常长等待也可能出现 slow。
- 网关最多额外解析 64 KiB 请求/代理响应用于分类，原流量不裁剪；流式响应不复制/解析 body。
  两层各生成 ID，不新增转发 header；只可按时间与账号对照，不能将时间接近当作唯一关联证据。
  DNS/TLS/Cloudflare/Nginx 等入口前失败仍需相应上游观测。

## 验证

测试只用合成账号/Token、临时目录与数据库、模拟 HTTP，不使用生产凭据：

```sh
python3 -m unittest tests_toy.test_mcp_diagnostics tests_toy.test_mcp_diagnostics_allowlist tests_toy.test_duel_async_gateway tests_toy.test_http_shell_extraction
git diff --check
```

覆盖多目标并发、非目标/未知隔离、到期/续期/热增删、无效/不可读/原子替换/刷新竞争、
旧格式与期限、配置读取次数、身份缓存、独立与总额度、轮转/权限/磁盘总量、旧文件保留、
两层路径/Bearer 实际调用点、响应与数据库无副作用。
受限沙箱如禁止 asyncio 内部 socketpair 唤醒，可在测试进程临时增加定时 loop callback；
同时将未显式替换的全局诊断 sink 设为关闭，避免其他传输测试使用生产配置。
此适配不修改生产循环，也不等于真实网络部署验证。
