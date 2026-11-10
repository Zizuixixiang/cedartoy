# 花园网页性能诊断（CedarToy 本地接入补丁）

代码分属主仓 `cedar_backend/garden_diagnostics.py`、`server.py`，以及
`vendor/Garden-Cat-Engine` 的 `garden_web_diagnostics.py`、`game_api.py`、网页资源。
不依赖 MCP middleware，不修改 MCP 诊断目标或 schema，不启动新服务。
vendor 改动只在本地提交，注明「勿推上游」；主仓不提交 gitlink 指针变化。

## 启停及范围

新增详细诊断默认关闭。主服务按请求读取
`data/garden_diagnostics_activation.json`（不入库），仅对**鉴权后的人类 13016**生效。
由部署人员在需要复现的窗口创建配置；本补丁不创建或自动启用它：

```json
{"account_id":13016,"enabled_at":<UTC epoch 整数秒>,"expires_at":<UTC epoch 整数秒>}
```

要求 `enabled_at <= 当前时间 < expires_at`，时长大于零且不超过 48 小时。
缺失、损坏、过期、账号不符均关闭；重启不会延长绝对截止时间。
删除该文件即可停止接收/转发新增详细诊断。已打开页面至多再发送到本页 15 分钟
或原截止时间，服务端丢弃其详情。原有 `garden_picker`、基础 `garden_boot` 保持兼容。

主服务分别限制服务端请求和浏览器详情：每进程每类 60 条/分钟、每个截止时间
最多 3000 条；独立服务同样 60/分钟、3000 条。进程重启重置计数，但不重置截止时间。
浏览器每页最多 30 条详情、12 个 API 样本，其中 status 最多 3 个；每个请求
最多一次 15 秒 pending 报告和一次结束报告。不会重试/取消业务命令。
记录进现有轮转日志，不创建新日志文件。主服务过滤外来 `X-Garden-*`，仅在鉴权、
绑定校验后向 loopback 花园服务注入诊断期限及 request_id。

## 字段与测量边界

- `garden_picker`：沿用选择器 request_id 与既有阶段。
- `garden_boot`：沿用页内 request_id 和阶段；经过时间改用 `performance.now()`，
  不可用时才回退 Date。资源详情另记 `garden_browser`，共用该 ID。
- `garden_browser`：只记录 JS/CSS 固定资源名、Resource Timing 的加载时间/字节数、
  script_start 和首轮同步执行时间、可见性，以及 catalog/status/cmd 的响应头、
  读体并解析 JSON、总耗时。API request_id 同时发到主服务；page_id 关联本页启动。
  本地缓存只在 transferSize=0 且 decodedBodySize>0 时标记；全零/缺失、跨域不可测
  时标 unknown。不推测 CDN HIT/MISS、磁盘/内存缓存或浏览器挂起时间。
- `garden_proxy`：从花园 handler 入口开始，记录 auth、binding、body_read、
  upstream_send、upstream_headers、upstream_body、activity、response_write 及 total。
  **不含 handler 前的 Nginx/线程池排队**；upstream_headers 包含本机传输、独立服务
  接入排队及应用处理。response_write 只证明写给下游 socket，不证明浏览器收到。
- `garden_engine`：从 Flask before_request 开始；pre_lock 是至锁请求前的累计时间，
  lock_wait 是原共享 RLock.acquire 的实际等待，body_parse 是 JSON 请求读取/解析，
  load_settle 包括读取/复制/离线推进，note_dispatch 是便签分支判断/处理，engine 是
  execute_command，response_build 包括公开状态摘要和 jsonify。
  save 是 db_save_state 总耗时，persist 是内部 STORE.save（包括序列化、原子写、fsync
  及它自己的整理工作），均按请求累加。**save 包含 persist；load_settle 也可能包含
  自动保存，不能把这些字段直接相加。** response_ready 在原 after_request 完成后，
  total 在原锁释放后、WSGI 发送响应前记录；不包括 Gunicorn 接入排队和实际网络发送。
  catalog/status 只补总时长、已有锁/保存计时，不拆完整业务阶段。

日志仅使用固定枚举、经过范围校验的数值、格式受限的 request_id 和服务端已验证
账号 ID；不记录 token、cookie、完整 URL、Referer、异常文本、请求/响应正文、命令或存档。
浏览器报告属于客户端观测，不作为鉴权或业务事实。未匹配到结束记录不等于成功。

## 静态资源

主脚本原 3,421,939 字节（gzip 2,523,970），42 张仍使用的内嵌 PNG 按原字节外置到
`static/assets/embedded/<内容 SHA256 前16位>.png`；移除 7 个被后续映射覆盖的旧 PNG
及未引用的 2 个 SVG 常量。图片使用现有 `<base>` 和同域路径，不附带账号参数。
没有 canvas、Service Worker、离线页面或存档格式改动。尚未缓存的图片仍需要网络；
已加载图片可使用浏览器缓存，游戏接口本来就需要联网。

JS/CSS URL 各自使用独立内容哈希，只有实际内容变化才改变版本。匹配当前哈希的
JS/CSS 及哈希文件名图片返回 `public, max-age=31536000, immutable`；旧/未知版本沿用
重新验证策略。HTML、API 继续 no-store。不得把长期缓存扩展到带身份的页面/API。
新增图片不可原地改字节，替换时使用新内容哈希文件名。

## 验证与部署边界

主仓针对性测试：`python3 -B -m unittest tests_toy.test_garden_request_diagnostics
tests_toy.test_garden_boot_diagnostics tests_toy.test_garden_static_proxy
tests_toy.test_garden_picker_diagnostics tests_toy.test_garden_cat_phase2_proxy`。
花园仓库：`.venv/bin/python -B -m unittest tests_toy.test_web_performance tests_toy.test_phase1`，
以及 `node tests/check_boot_diagnostics.cjs`、`node --check static/app.js`。
这些测试使用临时数据库/存档。不得用生产用户访问 status/cmd 作 smoke，status 也会推进并保存。

上线需主服务和花园独立服务均加载新代码；不涉及网关进程或数据库迁移。
图片、JS、模板与花园 Python 模块应配套发布。模板为旧 Python 渲染保留默认版本字段。
本地通过不代表弱网/Cloudflare 改善已证实；部署后仅开启有限复现窗口。
回滚应成组恢复模板/JS/服务端代码，保留已发布哈希图片供旧页面使用。
