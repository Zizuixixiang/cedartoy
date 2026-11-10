# MCP 连接诊断

用于判断连接失败发生在应用入口、解析、转发还是应用响应。正式 POST 最早进入
8003 gateway，因此同时覆盖 gateway 和 8004 主服务；GET event-stream 在主服务记录。
不改鉴权、initialize 返回、活跃时间、协议响应、公开工具 schema 或转发内容。

## 开关与部署

默认关闭。保留原 24 小时排障模式，另提供必须明确授权的持久轻量模式。
**旧激活文件过期不会转为常驻授权**，重启也不会延长旧期限。
配置只在进程启动时读取，不在每个请求中访问配置文件；启用、停用、更换账号都需重启两层。

主助手审阅代码后，最少部署步骤为：

1. 核实 `cedartoy` 与 `cedartoy-duel-gateway` 的实际 `TURTLE_SOUP_DB` 指向同一账号主库，
   两者使用本仓库的诊断模块，服务身份可写 `data/`。只支持现行单 gateway worker。
2. 显式创建 `/opt/cedartoy/data/mcp_diagnostics_config.json`，建议以 0600 权限原子替换，内容为：

   ```json
   {"mode":"persistent","enabled":true,"account_id":10604}
   ```

3. 重启两个服务使代码与配置一起生效：

   ```sh
   supervisorctl -c /etc/supervisor/supervisord.conf restart cedartoy cedartoy-duel-gateway
   supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy cedartoy-duel-gateway
   ```

持久配置不含凭据，不设自动到期。将 `account_id` 改为另一个正整数并重启可更换目标，
将 `enabled` 改为 JSON `false` 并重启可显式停用。不要仅删除新配置来停用：删除后会重新
考虑旧 24 小时激活文件；若旧授权尚未过期，它仍会生效。停用不删除已有证据，更换目标后
旧记录仍按容量轮转，分析时应筛选 `account_id`。

新配置文件存在时优先于旧文件，任何无效新配置均关闭诊断，绝不退回旧授权。
只接受上述三个键，`mode` 必须为 `persistent`，启用必须为布尔 `true`，账号必须为
`0 < account_id < 2^63` 的整数（不接受字符串、浮点、布尔）。文件不可读、重复键、坏 JSON、
超过 4096 字节或悬空符号链接均关闭诊断。格式正确不代表账号真实存在或是 AI，身份仍需只读关联。

本次开发仅交付代码、测试和文档，未创建生产配置、改写旧激活文件/日志或重启服务。
不要使用真实凭据做测试，部署后等待用户本人正常调用，再只读检查日志时间、账号与阶段。

## 持久模式的开销与限量

以下配额均为**每层、每进程**，两层采用同一个模块与配置规则，各自计数和写文件。

| 项目 | 上限与行为 |
| --- | --- |
| 目标入口采样 | 每 10 秒最多 1 个，每分钟最多 6 个；读取 body 前写 `entered` |
| 目标正常请求 | 仅入口入选请求写阶段及结束记录；其余只保留请求内存中的少量状态 |
| 目标异常补录 | 未入选入口的异常请求独立每分钟最多 12 条结束摘要；正常采样不消耗此配额 |
| 异常条件 | `exception` / `disconnected` / `cancelled`，HTTP >= 400，RPC/tool/transport error，或总耗时 >= 5000ms |
| 未知身份 | 独立每 300 秒最多 1 个请求，独立 `.unknown` 文件；包括无凭据、未知凭据或查询失败，绝不归属目标 |
| 身份缓存 | 每层最多 256 项，固定 5 秒有效期；只在内存保存数据库标识、凭据摘要和关联结果 |
| 身份查库 | 新/过期非目标缓存总计每分钟最多 60 次；已缓存目标的过期刷新另有每分钟 12 次保留配额 |
| 日志空间 | 每文件最多 1 MiB，当前文件 + `.1` + `.2`；目标和未知各自轮转，每层最多约 6 MiB，两层约 12 MiB |

分钟配额按 monotonic 固定分钟窗口计数，临近边界允许相邻窗口的短时突发；入口和未知采样
另有最小间隔。进程重启重置内存配额、缓存，但不重置旧授权的绝对期限或扩大日志空间上限。
每个采样请求每种固定阶段最多写一次，最多 7 个阶段标记加 1 个结束标记；异常补录仅 1 条。
即每分钟目标最多 60 条记录，未知最多每 300 秒 8 条；正常调用远低于此上限。
记录超出单文件容量时丢弃，不能靠一条过大记录突破容量限制。按容量淘汰，不承诺保留固定天数。

新凭据洪峰可能耗尽查库配额，使新轮换 Token 暂时不能归属；不会回用过期身份。
无关凭据不会优先挤掉已缓存的目标，也不能消耗目标的刷新配额、异常配额或轮转目标文件。
并发身份查询不排队：锁忙直接省略该请求的诊断；因查库限频/锁忙而没查询的请求不会冒充
“未知身份”写入日志。重复使用凭据时，5 秒内通常只查一次；不会对无关 MCP 流量逐请求查库。
已知非目标账号不写日志，网关还会跳过其额外 JSON 解析和后续诊断工作线程调用。

日志不可写时静默放弃，目标/未知各自退避 30 秒后才重试；常驻写锁忙也直接丢弃该次记录。
缓存/配额锁不覆盖日志 I/O。没有后台定时扫描、无界队列、按 Token 无限增长的状态或逐请求 fsync。
网关身份查询及诊断文件写入都在工作线程执行，主服务本来就是同步 worker。
这是 best-effort 本地文件诊断，不保证故障磁盘下的写入时延或证据持久性。

## 记录、身份与隐私

- 文件仍为 `data/mcp_edge_diagnostics.jsonl`、`data/mcp_main_diagnostics.jsonl`。
  未知文件在基名后加 `.unknown`，轮转分别再加 `.1` / `.2`，新文件权限 0600。
- 基础字段只有服务端随机 `request_id`、时间、固定 transport/http_method/method、scope、stage、
  elapsed_ms、http_status、rpc_result、rpc_error_code、identity，以及目标匹配时的 account_id。
  常驻记录另有 `mode=persistent`、`identity_age_ms`（关联缓存使用时的年龄）、`entry_sampled`、
  `phases_ms`（固定阶段首次出现的相对毫秒数）；结束记录还有固定枚举的 `reasons`。
  不记录客户端 RPC ID、账号名、IP、UA、Token/Token 哈希、密码、Cookie、路径、查询串、参数或 body。
  凭据摘要仅用于内存缓存/数据库索引比较，绝不输出。
- 方法名仅保留代码固定白名单：initialize、ping、tools/list/call、resources/list/read/subscribe/unsubscribe、
  resources/templates/list、prompts/list/get、completion/complete、logging/setLevel 及已知标准通知；
  其他任意方法一律 other。RPC 错误码只接受有符号 32 位整数，不转换字符串/布尔/浮点，
  非法或缺失值为 null；从不记录异常或 RPC 错误的 message/data/堆栈。
- 身份关联只读查询 `ai_access_tokens` / `legacy_ai_token_hashes`，不解读 JWT 自报 user_id，
  不调用鉴权函数，不更新活跃时间。`current_hash_match` 只表示查询时与当前记录匹配，绝不等于
  鉴权成功；`revoked`、`generation_mismatch`、`deleted`、`pending_deletion` 等仅描述账号/凭据状态。
  归属来自 Token 记录，不来自 RPC 中自报的 player_id 或绑定人类账号。
- Token 轮换、撤销、删除或归属变化后，常驻关联结果可能短暂滞后最多 5 秒；请求后续阶段保留
  该请求开始时的观察，不承诺跟随长请求持续刷新。`identity_age_ms` 不表示鉴权有效期。
  已过期缓存且查库失败/配额耗尽时不继续归属；真实鉴权始终独立执行、绝不使用此缓存。
  未知、重复匹配或无法读取时不归属账号。
- DB 使用 `TURTLE_SOUP_DB` 或既有默认账号库，`mode=ro`、`query_only`、SQLite 锁等待上限 20ms。
  两层必须使用同一实际账号库；诊断库/日志错误不抛到正常调用，也不输出错误正文。

## 如何解释证据

入口先记内存开始时间，随后只读关联，入选才写 `entered` / `identity_checked`，均在读取 body 前。
`entered` 使用最初入口时间；`parsed`、`proxy_dispatch` / `duel_dispatch` / `autochess_dispatch`
表示解析和分发阶段。常驻模式还记录 `response_ready`（观察到响应对象），在结束摘要的
`phases_ms.response_started` 中记录响应状态设置时间。它们都不证明客户端收到了完整响应。
`completed` 只表示处理结束，不代表 RPC 成功；应同时看 HTTP 状态、rpc_result 和 reasons。

入口被采样的请求，即使之后阻塞或进程终止，也尽可能留下最后已写阶段。未入选入口但最终报错/
变慢的请求在配额内补录结束摘要（`entry_sampled=false`），其中包含内存中已观察的阶段。
无 finish 的入口记录可能来自在途阻塞、取消竞态、进程退出或末尾日志写入失败，不能单凭缺尾
判定具体原因。没有后台看门狗：未采样且永不结束的请求可能完全没有记录。
`slow` 的 5 秒阈值是排障线索，Duel 等正常长等待也可能触发，不应直接统计为真实掉线。

网关仅额外解析最多 64 KiB 的请求/代理响应用于分类；更大的消息可能 method=unparsed 或
rpc_result=unobserved，原流量不裁剪。Duel/自走棋流式回复不复制、不解析 body，只有 HTTP/阶段
结果。主服务与网关可能走不同路由；双弈内部接口不保证出现对应主服务 MCP 入口记录。

两层各自生成 ID，不信任外部请求 ID，不新增转发 header。可以按时间和已匹配账号对照，
不能把时间接近当成唯一关联证据。未知凭据不能靠申请中的名字强行归属。
**没有目标记录不能证明请求没到服务器**：也可能未启用/配置无效、凭据未归属、缓存刷新或查询
配额受限、入口未采样、异常配额耗尽、并发竞争、日志不可写/已轮转，或主服务尚未接收请求。
未抵达应用的 DNS、TLS、Cloudflare、Nginx 或网络失败，不能仅凭这些日志精确定位；需要用户
失败时间和相应上游观测配合。

## 原 24 小时模式兼容

仅在新配置不存在时读取 `data/mcp_diagnostics_activation.json`。旧格式仍为
`account_id=10604`、整数 `enabled_at` 和 `expires_at=enabled_at+86400`（Unix 秒）。
必须处于有效时间窗；启动时读取绝对期限，重启不延长。到期后停止新诊断、身份查询和在途记录
写入。再次启用必须重新明确授权、写入新的时间窗并重启两层；本次不更新旧激活文件。

临时模式沿用每层目标每分钟 120 个请求、未知每分钟 10 个请求的配额，逐请求只读关联，不使用
常驻缓存；入选请求记录原有阶段，目标/未知各自容量及权限与常驻模式相同。
既有旧日志保留供排查，不因模式切换清理。以后确需清理证据时，只删除确认不再需要的两个
日志基名及各自 `.1/.2/.unknown/.unknown.1/.unknown.2`，不要清理其他 data 文件。

## 验证

相关测试使用合成凭据、临时库/目录与模拟 HTTP 传输，自动清理，不使用生产凭据或真实网络：

```sh
python3 -m unittest tests_toy.test_mcp_diagnostics tests_toy.test_duel_async_gateway tests_toy.test_http_shell_extraction
```

覆盖新配置启停/更换目标/无效关闭、旧期限兼容、只读关联、缓存规模与查库次数、Token 轮换/
归属状态刷新、正常采样、异常与慢请求、在途入口、并发、独立配额、轮转容量/权限、I/O 故障及
两层真实调用点的响应不变。若测试沙箱禁止 asyncio 内部 socketpair 唤醒，可仅在测试进程用
定时 loop callback 唤醒事件循环；此为沙箱适配，不修改生产循环，也不等价于真实网络部署验证。
