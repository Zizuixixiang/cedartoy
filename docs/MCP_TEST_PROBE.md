# 临时 MCP discovery probe

用途：让 ChatGPT Site/Plugin 通过 `https://toy.cedarstar.org/mcp-test` 测试
action discovery。它是独立标准库进程，不导入 `server.py`，不读写数据库、
账号、Token 或游戏存档。仅暴露无参数的 `test_ping`，返回文本 `pong`。
不提供 OAuth、UI、SSE 流、持久会话或其他工具。

工具声明沿用首轮 probe：`securitySchemes: [{"type":"noauth"}]`、
相同的 `_meta.securitySchemes` 镜像，以及
`readOnlyHint=true / destructiveHint=false / openWorldHint=false`。
依据：[OpenAI 工具字段](https://developers.openai.com/plugins/reference)、
[鉴权声明](https://developers.openai.com/plugins/build/auth)。
HTTP POST 返回 JSON；通知返回空 202；GET 返回 405；无需 session ID。
这些模式见 [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

## 配置与路由

- 程序：`scripts/mcp_test_probe.py`，固定监听 `127.0.0.1`，默认端口 `8784`。
- supervisor 模板：`deploy/supervisor-mcp-test-probe.conf.example`。
  部署目标 `/etc/supervisor/conf.d/cedartoy-mcp-test-probe.conf`。
  使用 `nobody`；`autostart=false`，需要显式启动，仅异常退出自动重启。
- nginx 片段：`deploy/nginx-mcp-test-probe.conf.example`。
  部署目标 `/etc/nginx/snippets/cedartoy-mcp-test-probe.conf`。
- nginx 插入补丁：`deploy/nginx-mcp-test-probe.patch`。
  目标 `/etc/nginx/conf.d/cedartoy-tunnel-router.conf`，在监听
  `127.0.0.1:8002` 的 `server` 内加入：

```nginx
include /etc/nginx/snippets/cedartoy-mcp-test-probe.conf;
```

片段中的 `location = /mcp-test` 和 `location = /mcp-test/` 均直接代理到
`http://127.0.0.1:8784`，不改写路径、不跳转；精确匹配优先于现有单段路径
正则。正式 `/`、`/mcp` 和其他路径保持原路由。

当前公网链路为 Cloudflare → nginx `8002`；该点也是普通 HTTP vhost
`/etc/nginx/sites-enabled/toy.cedarstar.org` 的兜底上游。只修改 `8002` 路由，
不改 TLS、Cloudflare、主服务 `8004` 或双弈网关 `8003`。

## 本地验证

```bash
cd /opt/cedartoy
python3 -m unittest tests_toy.test_mcp_test_endpoint
git diff --check
```

测试使用临时 loopback TCP 端口，结束即关闭；不启动 supervisor，不导入主服务。
手工调试可运行 `python3 scripts/mcp_test_probe.py --port 8784`，结束后 Ctrl-C。

## 待主助手执行的部署命令

以下命令尚未执行。以 root 在同一个 shell 中执行；不运行主服务 restart，
不使用无参数的 supervisor `update` 或 `restart all`。

```bash
set -e
cd /opt/cedartoy
probe_main_pid_before=$(supervisorctl -c /etc/supervisor/supervisord.conf pid cedartoy)
python3 -m unittest tests_toy.test_mcp_test_endpoint
# 端口检查只短暂 bind 后关闭；已占用会退出，不能抢占或杀掉现有进程。
python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 8784))
print('127.0.0.1:8784 is free')
PY

# 首次安装：若目标已存在则停下检查，避免覆盖其他配置。
test ! -e /etc/supervisor/conf.d/cedartoy-mcp-test-probe.conf
test ! -e /etc/nginx/snippets/cedartoy-mcp-test-probe.conf
patch --dry-run --forward --fuzz=0 /etc/nginx/conf.d/cedartoy-tunnel-router.conf < deploy/nginx-mcp-test-probe.patch

install -m 0644 deploy/supervisor-mcp-test-probe.conf.example /etc/supervisor/conf.d/cedartoy-mcp-test-probe.conf
supervisorctl -c /etc/supervisor/supervisord.conf reread
supervisorctl -c /etc/supervisor/supervisord.conf update cedartoy-mcp-test-probe
supervisorctl -c /etc/supervisor/supervisord.conf start cedartoy-mcp-test-probe
supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy-mcp-test-probe

curl --fail-with-body -sS --max-time 10 http://127.0.0.1:8784/mcp-test \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

probe_nginx_backup=$(mktemp /tmp/cedartoy-mcp-probe-nginx-before.XXXXXX)
cp -p /etc/nginx/conf.d/cedartoy-tunnel-router.conf "$probe_nginx_backup"
install -d -m 0755 /etc/nginx/snippets
install -m 0644 deploy/nginx-mcp-test-probe.conf.example /etc/nginx/snippets/cedartoy-mcp-test-probe.conf
patch --forward --fuzz=0 /etc/nginx/conf.d/cedartoy-tunnel-router.conf < deploy/nginx-mcp-test-probe.patch
nginx -t
systemctl reload nginx

test "$probe_main_pid_before" = "$(supervisorctl -c /etc/supervisor/supervisord.conf pid cedartoy)"
supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy
```

如 `nginx -t` 失败，不 reload；仅撤销本补丁（见下方回滚），排查配置后再继续。
supervisor `reread` 可报告其他配置变动，但上面的 `update` 仅指定 probe。

## curl 验收与 ChatGPT 测试

先将 `probe_url` 设为 `http://127.0.0.1:8784/mcp-test` 验证 probe，
再设为 `http://127.0.0.1:8002/mcp-test` 验证 nginx 分流，
最后使用以下公网地址验证完整链路：

```bash
probe_url=https://toy.cedarstar.org/mcp-test
curl --fail-with-body -sS --max-time 15 "$probe_url" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl-probe","version":"1"}}}'
curl --fail-with-body -sS --max-time 15 "$probe_url" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  --data-binary '{"jsonrpc":"2.0","id":2,"method":"ping"}'
curl --fail-with-body -sS --max-time 15 "$probe_url" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  --data-binary '{"jsonrpc":"2.0","id":3,"method":"tools/list"}'
curl --fail-with-body -sS --max-time 15 "$probe_url" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  --data-binary '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"test_ping","arguments":{}}}'
```

预期 HTTP 200；依次返回 `serverInfo.name=cedartoy-mcp-test-probe`、空 result、
仅含 `test_ping` 的 tools 数组、文本 `pong`。

通知与 GET 单独验证：

```bash
curl -i -sS --max-time 15 "$probe_url" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  --data-binary '{"jsonrpc":"2.0","method":"notifications/initialized"}'
curl -i -sS --max-time 15 "$probe_url" -H 'Accept: text/event-stream'
```

分别预期空 202 和 405。GET 的 405 是刻意不提供 SSE 流，并非 discovery 失败。
ChatGPT Site/Plugin 测试时填公网 URL，使用匿名/noauth 配置，不填正式账号 Token。
公网 curl 通过只代表服务/路由正常；仍需实际发布并观察 ChatGPT discovery。
日志位于 `/var/log/cedartoy-mcp-test-probe.{out,err}.log`，MCP 日志仅记录方法名，
HTTP 日志记录请求行和状态，不记录 Authorization、参数或请求体。

## 测试结束后回滚

只逆向删除本次 include，保留路由文件其他变更；先 dry-run 检查。

```bash
set -e
cd /opt/cedartoy
patch --dry-run --reverse --fuzz=0 /etc/nginx/conf.d/cedartoy-tunnel-router.conf < deploy/nginx-mcp-test-probe.patch
patch --reverse --fuzz=0 /etc/nginx/conf.d/cedartoy-tunnel-router.conf < deploy/nginx-mcp-test-probe.patch
nginx -t
systemctl reload nginx
supervisorctl -c /etc/supervisor/supervisord.conf stop cedartoy-mcp-test-probe
rm /etc/nginx/snippets/cedartoy-mcp-test-probe.conf
rm /etc/supervisor/conf.d/cedartoy-mcp-test-probe.conf
supervisorctl -c /etc/supervisor/supervisord.conf reread
supervisorctl -c /etc/supervisor/supervisord.conf update cedartoy-mcp-test-probe
```
