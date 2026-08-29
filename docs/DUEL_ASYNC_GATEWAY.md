# 双弈透明 async MCP gateway（第一阶段）

## 目标与边界

外部地址完全不变：仍使用 `https://toy.cedarstar.org/`、`/mcp` 或已有的
`/{token}`，鉴权仍支持路径 token 与 `Authorization: Bearer`，调用仍是
JSON-RPC `tools/call -> play(game="duel")`。网关不处理网页 GET，也不修改
`vendor/duel`。

请求链路：

1. Nginx 只把候选 MCP `POST` 送到 `127.0.0.1:8003`；GET/静态资源仍去 8002。
2. 8003 检查请求形状。非 duel 请求按原 method/path/query/body/必要 headers
   转发到 8002，并透传上游 status、body 与响应 headers。
3. duel 请求先向 8002 发一个短 prepare。8002 使用原有 token、账号类型、slot、
   AI↔human 绑定、防沉迷预检与 duel 独立限流逻辑，覆盖模型自报的 `player_id`，
   并按绑定表决定允许出现的 `opponent_id`。
4. prepare 返回一次性、120 秒有效的内存 ticket 与可信后端 payload。它不会调用
   8772，也不会进入 long-poll。
5. 8003 用独立的 `httpx.AsyncClient` 连接池直接 await
   `127.0.0.1:8772/mcp/play`。默认最多 200 个并发后端请求；多出的请求
   只排队等待 asyncio semaphore，不占 8002 worker。prepare/finalize 另以 40
   个短调用为上限。转发 8002 的非 duel 请求也使用独立连接池，不会被
   200 个长挂等占尽连接而阻塞。
6. 后端结束后，8003 用 ticket 向 8002 发短 finalize，由原平台逻辑完成防沉迷
   成功计数、通知拼接、slot 标记及原 JSON-RPC/MCP 包装。

因此长等待发生在单进程 ASGI event loop 中，8002 的 50 个同步 worker 只处理两次
短事务。网关不会把用户 token 发给 8772，Duel 只收到 prepare 生成的 canonical
player/opponent payload。

## 安全约束

- prepare/finalize 同时要求来源是 loopback 且
  `X-CedarToy-Gateway-Secret` 与 `DUEL_GATEWAY_SHARED_SECRET` 恒时比较一致。
- 真实 secret 必须用 `python3 -c 'import secrets; print(secrets.token_hex(32))'`
  生成，分别配置给 `cedartoy` 与 `cedartoy-duel-gateway`，不得提交到仓库。
- Nginx 必须对 `/_internal/duel-gateway/` 明确返回 404。只靠来源 IP 不够，因为
  公网请求经本机 Nginx 转发后在 8002 看起来也可能来自 loopback。
- 网关只把原 Authorization/path token 交给 loopback prepare；它不记录 token，
  prepare 响应也不回显 token。
- 8003 只监听 `127.0.0.1`；这台 2 核机器保持单 ASGI worker 即可承载挂等，
  且避免多进程把全局并发上限成倍放大。一次性 ticket 存在 8002 的 CedarToy
  进程内，因此 8002 也必须继续保持当前的单进程模型。

## 配置与启动（仅示例，不能直接部署）

依赖：`fastapi`、`uvicorn`、`httpx`，已写入根目录 `requirements.txt`。

环境变量：

- `DUEL_GATEWAY_SHARED_SECRET`：必填；8002 与 8003 必须完全相同。
- `DUEL_GATEWAY_MAX_CONCURRENCY`：默认 200，允许 100–500。
- `DUEL_GATEWAY_CEDARTOY_ORIGIN`：默认 `http://127.0.0.1:8002`。
- `DUEL_GATEWAY_DUEL_ORIGIN`：默认 `http://127.0.0.1:8772`。

本地前台验证命令：

```bash
export DUEL_GATEWAY_SHARED_SECRET='<随机 64 位 hex>'
python3 -m uvicorn duel_async_gateway:app --host 127.0.0.1 --port 8003 --workers 1
```

Supervisor 示例见
[`deploy/supervisor-duel-async-gateway.conf.example`](../deploy/supervisor-duel-async-gateway.conf.example)，
Nginx 示例见
[`deploy/nginx-duel-async-gateway.conf.example`](../deploy/nginx-duel-async-gateway.conf.example)。
这些只是待审核文件；本次不修改 `/etc`、不启动进程、不部署。

公网入口必须真的经过含上述规则的 Nginx。若 Cloudflare Tunnel 仍直接指向 8002，
则现有 URL 虽然仍可用，但请求会绕开 8003，不能释放同步 worker。上线前必须确认
Tunnel/Nginx 的真实第一跳，并用访问日志验证 duel POST 命中 8003。

## 建议上线顺序与回滚

1. 给 8002 配置共享 secret 并重启 `cedartoy`；此时旧同步路径仍可用。
2. 启动 8003，检查 `curl http://127.0.0.1:8003/health`。
3. 本机分别验证路径 token、Bearer、非 duel `tools/list` 和 duel `state wait=true`。
4. `nginx -t` 后才切候选 POST 路由；观察 8002 worker、8003 coroutine、502/504。
5. 回滚只需让候选 POST 的 `proxy_pass` 全部恢复 8002；外部 URL 与 token 无需改。

不要把 8003 暴露为新的用户地址，也不要删除 `server.py` 的同步 duel fallback；后者
用于未切流、紧急回滚和本地诊断。
