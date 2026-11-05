# MCP 连接诊断

用于判断连接失败发生在应用入口、解析、转发还是应用响应。正式 POST 最早进入
8003 gateway，因此同时覆盖 gateway 和 8004 主服务；GET event-stream 在主服务记录。
不改鉴权、initialize 返回、活跃时间、协议响应、公开工具 schema 或转发内容。

## 记录及隐私

- 文件：data/mcp_edge_diagnostics.jsonl、data/mcp_main_diagnostics.jsonl。
- 本次仅记录目标账号 10604，目标每层每分钟上限 120 个请求。其他已知账号不写记录。
  未知/无凭据/关联不可用请求独立上限 10 个/分钟，写入独立 .unknown 文件，不归属目标。
  两类配额及文件互不占用，其他流量不能挤掉或轮转目标记录。每文件 1 MiB，
  各自最多当前及两份轮转文件（.1/.2），每层合计上限约 6 MiB。按容量淘汰，
  不承诺固定天数；此目标配置仅用于本次工单，排障结束应撤除。
- 新文件权限 0600。两个进程分别写自己的文件；只支持现行单 gateway worker。
- 每条记录只有服务端生成的随机 request_id、时间、固定 transport/http_method/method、
  scope、stage、elapsed_ms、http_status、rpc_result、rpc_error_code、identity，以及目标匹配时的 account_id。
  不记录客户端 RPC ID、账号名、IP、UA、凭据、凭据哈希、路径、查询串、参数或 body。
- 方法名仅保留代码固定白名单：initialize、ping、tools/list/call、resources/list/read/subscribe/unsubscribe、
  resources/templates/list、prompts/list/get、completion/complete、logging/setLevel 及已知标准通知；
  其他任意方法一律 other。RPC 错误码只接受有符号 32 位整数，不转换字符串/布尔/浮点，
  非法或缺失值为 null；从不记录错误 message/data。
- 入口先记内存开始时间，随后只读关联并按目标/未知隔离配额决定是否落盘，均在读 body 前完成。
  entered 使用最初入口时间，关联完成前中断可能没有记录。入选请求上传/转发中断仍有入口证据。
  parsed、proxy_dispatch/duel_dispatch/autochess_dispatch、completed/exception/
  disconnected/cancelled 表示阶段。completed 只是处理结束，不代表 RPC 成功。
- 身份关联仅在内存计算 SHA-256，以索引查询 ai_access_tokens 或
  legacy_ai_token_hashes。不解读 JWT 自报 user_id，不调用鉴权函数、不更新活跃时间。
  current_hash_match 只表示与当前记录匹配，不等于鉴权成功；revoked、
  generation_mismatch、deleted、pending_deletion 等记录状态不放宽任何权限。
  未知、重复匹配或无法读取时不归属账号。
- DB 使用 TURTLE_SOUP_DB 或既有默认账号库，mode=ro、query_only、锁等待上限 20ms。
  网关在工作线程做关联，不阻塞 event loop 等待 SQLite 锁。上线前核实两个进程指向
  同一账号库；日志不可写/库不可读不影响请求，也不输出异常正文。
- gateway 只额外解析最多 64 KiB 的请求/代理响应用于方法及结果分类；更大的消息可能
  method=unparsed 或 rpc_result=unobserved，原流量不裁剪。Duel/自走棋流式回复不复制、
  不解析其 body，最终只有 HTTP/阶段结果。
- 两层各自生成 ID，不信任/记录外部请求 ID，也不新增转发 header；跨层可按时间与已
  匹配账号对照，不应把时间接近当成唯一关联证据。

## 排障与部署

由主助手审阅后分别 restart cedartoy 与 cedartoy-duel-gateway，必须指定
/etc/supervisor/supervisord.conf。本开发任务没有执行重启。
不要使用真实凭据做测试，等用户本人重试后只读筛选时间及账号 ID。
未知凭据不能靠申请中的名字强行归属。没有目标记录可能是凭据未知而无法归属、身份关联中断、目标本身配额耗尽、文件不可写、
应用前的网络/TLS/Nginx 失败或主服务队列未接收，不能直接断言用户没请求。

测试：
python3 -m unittest tests_toy.test_mcp_diagnostics tests_toy.test_mcp_schema_selection tests_toy.test_http_shell_extraction tests_toy.test_root_mcp_protocol
python3 -m unittest tests_toy.test_duel_async_gateway tests_toy.test_mcp_module_compat tests_toy.test_mcp_dispatch_order

诊断测试仅用临时库及合成凭据，自动清理，不连接生产账号或网络。

## 本次 24 小时开关

默认关闭，没有激活文件不会做身份关联或落盘。审阅部署时显式创建
data/mcp_diagnostics_activation.json，内容为 account_id=10604、整数 enabled_at
和 expires_at=enabled_at+86400（Unix 秒），随后重启两个服务。两个服务启动时读取
同一绝对期限；重启不会重新计算或延长。到期后停止新诊断、身份查询和在途记录写入，
不需要另一个定时任务。再次启用必须是新的明确排障操作，重新写入激活文件并重启。

主助手可以在部署时运行以下一次性命令（不是开发测试步骤）：
python3 -c 'import json,time; from pathlib import Path; n=int(time.time()); Path("data/mcp_diagnostics_activation.json").write_text(json.dumps({"account_id":10604,"enabled_at":n,"expires_at":n+86400}))'

激活文件不含凭据，仅记录本次目标和绝对时间。到期后的现存日志不会继续增长，
按既定容量保留供排查。工单排查结束后核实不再需要证据，再删除两个日志基名、
它们的 .1/.2、.unknown/.unknown.1/.unknown.2 以及激活文件；不要清理其他 data 文件。
网关身份查询及所有诊断文件写入都在工作线程执行，主服务本来就是同步 worker。
