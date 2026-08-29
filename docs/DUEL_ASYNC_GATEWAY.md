# 双弈透明 async MCP gateway

## 目标与当前链路

外部地址完全不变：仍使用 `https://toy.cedarstar.org/`、`/mcp` 或已有的
`/{token}`，鉴权仍支持路径 token 与 `Authorization: Bearer`，调用仍是
JSON-RPC `tools/call -> play(game="duel")`。线上链路为：

```text
Cloudflare -> Nginx 127.0.0.1:8002
  GET / 与普通请求 -> CedarToy 127.0.0.1:8004
  候选 MCP POST          -> async gateway 127.0.0.1:8003
                                  duel -> 127.0.0.1:8772
                                  non-duel -> 127.0.0.1:8004
```

网关不处理网页 GET，也不修改 `vendor/duel`。

## Duel 自动挂等

1. 8003 先向 8004 发一次短 prepare。8004 使用原有 token、账号类型、slot、
   AI↔human 绑定、防沉迷预检与 Duel 独立限流逻辑，覆盖模型自报的
   `player_id`，并按绑定表决定 `opponent_id`。
2. prepare 返回一次性 ticket 和可信后端 payload，不调用 8772，不进入
   long-poll。ticket 默认 720 秒过期，覆盖默认 600 秒连续等待及 buffer。
3. 8003 直接 async await `127.0.0.1:8772/mcp/play`。第一次请求保留原始受信
   payload，因此 `move(wait=true)` 的落子和附言只执行一次。
4. 如果 8772 返回内部 `still_waiting`，8003 不完成外层 MCP 请求，而是改用只含
   canonical `player_id`、`room_id`、`action=state`、`wait=true` 的无副作用 payload
   继续等待。后续心跳绝不重放 move、message、resign 等动作。
5. 只有轮到当前 AI、本人淘汰/离席/失活、房间终局/归档/取消、后端错误等
   有意义结果才 finalize 并向模型返回。到达服务端连续等待上限时，才会
   finalize 最后一个 `still_waiting`。prepare/finalize 在整段等待中各只执行一次。
6. 流式响应每 12 秒发送一个合法 JSON 前导空白作为纯网络 keepalive。空白 chunk
   不是 JSON-RPC 响应；最后只输出一份完整 JSON，整个 body 仍可直接被标准
   `json.loads` 解析。响应带 `X-Accel-Buffering: no`，Nginx 候选 MCP location 也必须
   `proxy_buffering off` 并关闭 gzip，避免微小 keepalive chunk 被中间层攒住。

连续挂等默认最多 200 个 coroutine，不占用 8004 的同步 worker。转发 8004
的非 Duel 请求使用独立连接池，不会被 Duel 长挂等占尽连接。客户端断开或
coroutine 取消会取消当前 8772 请求，并 best-effort 调用 abandon 立即丢弃 ticket；
如清理请求也失败，TTL 与 600 张 ticket 硬上限仍能防止无界泄漏。

> 8772 自身仍是 30 秒短心跳。只有绕过 8003 直连 8772 时，调用方才可能看到
> `still_waiting` 并需自行续等；官方 toy.cedarstar.org MCP 地址不会每 30 秒把它返回模型。

## 安全与生命周期

- prepare/finalize/abandon 同时要求来源是 loopback 且
  `X-CedarToy-Gateway-Secret` 与 `DUEL_GATEWAY_SHARED_SECRET` 恒时比较一致。
- 真实 secret 必须用 `python3 -c 'import secrets; print(secrets.token_hex(32))'` 生成，
  分别配置给 CedarToy 与 gateway，不得提交到仓库。
- Nginx 必须对 `/_internal/duel-gateway/` 明确返回 404。只靠来源 IP 不够，因为
  公网请求经本机 Nginx 转发后在 CedarToy 看起来也可能来自 loopback。
- 网关只把原 Authorization/路径 token 交给 loopback prepare；它不记录 token，
  也不把 token 发给 8772。
- 8003 只监听 `127.0.0.1`，保持单 ASGI worker，避免多进程把全局并发上限成倍放大。
  ticket 存在 CedarToy 单进程内。

## 配置（仅示例，不直接部署）

依赖：`fastapi`、`uvicorn`、`httpx`。Supervisor 与 Nginx 完整片段分别见
[`deploy/supervisor-duel-async-gateway.conf.example`](../deploy/supervisor-duel-async-gateway.conf.example)
和 [`deploy/nginx-duel-async-gateway.conf.example`](../deploy/nginx-duel-async-gateway.conf.example)。

- `DUEL_GATEWAY_SHARED_SECRET`：必填；CedarToy 与 gateway 必须相同。
- `DUEL_GATEWAY_CEDARTOY_ORIGIN`：线上应为 `http://127.0.0.1:8004`。
- `DUEL_GATEWAY_DUEL_ORIGIN`：默认 `http://127.0.0.1:8772`。
- `DUEL_GATEWAY_MAX_CONCURRENCY`：默认 200，允许 100–500。
- `DUEL_GATEWAY_MAX_WAIT_SECONDS`：默认 600，允许 60–3600；gateway 与 CedarToy 要配成相同值。
- `DUEL_GATEWAY_KEEPALIVE_SECONDS`：默认 12，允许 5–30，只影响外层网络空白 chunk。
- `DUEL_GATEWAY_TICKET_TTL_SECONDS`：只配 CedarToy，默认为最大等待 + 120，
  不得低于最大等待 + 60；600 秒等待时建议 720。

本地前台验证：

```bash
export DUEL_GATEWAY_SHARED_SECRET='<随机 64 位 hex>'
export DUEL_GATEWAY_CEDARTOY_ORIGIN='http://127.0.0.1:8004'
python3 -m uvicorn duel_async_gateway:app --host 127.0.0.1 --port 8003 --workers 1
```

## 上线验证与回滚

1. 确认 CedarToy 监听 8004，且已合并 secret、最大等待和 ticket TTL 环境变量。
2. 启动 8003，检查 `curl http://127.0.0.1:8003/health`。
3. 本机分别验证路径 token、Bearer、非 Duel `tools/list` 和 Duel `move/state wait=true`。
4. 确认 Nginx 8002 的 MCP POST 命中 8003、非 Duel 由 8003 转发 8004，并确认
   `proxy_buffering off`。观察 8004 worker、8003 coroutine、502/504 与客户端取消。
5. 回滚只需让候选 MCP POST 恢复直接转发 8004；外部 URL 与 token 无需改。

不要把 8003 暴露为新的用户地址，也不要删除 `server.py` 的同步 Duel fallback；后者
用于未切流、紧急回滚和本地诊断。
