# 主站卡五星接入交接（2026-11-04）

> 本文记录2026-11-04阶段的验收快照；现行可选房规与完整玩法见 [卡五星 Guide](../vendor/duel/docs/KA_WU_XING.md)。

工作区 `/tmp/cedartoy-kawuxing-20261104`，分支 `feat/kawuxing-platform-20261104`。未commit/push/部署。
配套引擎 `/tmp/cedarduet-kawuxing-20261104`，完整功能、房规、未完成项与游戏文件清单见该仓库 `docs/KA_WU_XING_ACCEPTANCE.md`。

## 主站改动文件

- `cedar_backend/guides.py`：按需 `duel:ka_wu_xing` Guide 路由，复用已安装的 `vendor/duel/docs/KA_WU_XING.md`；公共duel Guide仅增加发现指针，不增加常驻schema业务字段。
- `index.html`、`admin.html`：既有房间游戏名映射增加「襄阳卡五星」，不增加首页卡片。
- `tests_toy/test_duel_ka_wu_xing.py`（新）：真实临时账号token→根MCP→临时Duel ASGI，三位跨账号AI邀请/加入/开局/状态/行动/旧revision拒绝，0底注、NPC补位与按需Guide。
- `scripts/persistence_check.py`：增加 `--duel-ka-wu-xing` 专项；`DUEL_TEST_ROOT` 可指向隔离引擎工作区，原默认路径不变。
- `README.md`：入口与Guide指针。
- 本交接文件（新）。

## 检查结果

主站 `test_duel_ka_wu_xing` 3项 + 既有 `test_duel_ai_only`、`test_mcp_schema_selection` + `RootMcpProtocolTests.test_root_schemas_keep_gemini_numeric_enum_compatibility`，合计19项通过。Path token/Bearer均经过真实临时账号认证，未读取生产账号凭据。

按游戏专项运行 `scripts/persistence_check.py --duel-ka-wu-xing` 通过；该测试使用临时SQLite，实际新Python进程重载手牌/合法动作，终局重复结算不再扣款。新增荒庄余额裁剪测试也单独通过。Python AST语法及 `git diff --check` 通过。

开发环境缺失vendor模块：测试前仅复制生产只读目录的ci-yu-wu与eco `.py`到隔离worktree用于import（不复制数据库/存档），测试后已删除。测试运行使用 `PYTHONDONTWRITEBYTECODE=1`，引擎代码通过 `PYTHONPATH` 指向隔离工作区；真实NPC库存缺失时只注入临时persona，不替换游戏或鉴权逻辑。

示例：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/cedarduet-kawuxing-20261104:/opt/cedartoy/vendor/duel/.venv/lib/python3.10/site-packages python3 -m unittest tests_toy.test_duel_ka_wu_xing tests_toy.test_duel_ai_only tests_toy.test_mcp_schema_selection -q
DUEL_TEST_ROOT=/tmp/cedarduet-kawuxing-20261104 DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python python3 scripts/persistence_check.py --duel-ka-wu-xing
```

上述主站import仍需要完整vendor依赖；主助手的集成环境应复用既有依赖。工作区测试用的临时源码副本未保留。

常驻schema完全未修改；公共DUEL_GUIDE从4452字符到4539字符（+87字符，不是token数），只增加一行玩法与按需链接，不把亮倒/杠分参数放进play。详细规则只在按需Guide及游戏首次bootstrap/显式full_state中出现。

## 主助手集成注意

- 2026-11-05 已补跑本地Chromium 145的360/430px触控与1280px桌面专项及截图核对：普通/邀请下拉与3/4人牌桌通过。真实设备、其他浏览器、生产账号链路与线上外部字体未验收。脚本 `scripts/check_ka_wu_xing_ui.cjs` 可重跑；临时工具/截图已清理。
- 一并集成两仓改动与游戏Guide文件；前端缓存版本已更新；不要覆盖生产data、NPC人设或其他花园worktree改动。
- 独立game_type、无SQL schema迁移，旧mahjong链接与国标8番保持；已有根邀请/组队路径足够支持三个不同账号AI，不需要人类、不应放宽服务端身份验证。
- 游戏仍为单手单和制；一炮双响、杠上炮、连续轮庄、买马/漂/癞子未实现且规则明确。亮倒荒庄采用每个亮倒者赔每个未亮倒者1分，属于此次明确默认房规。
- 主助手完成核验后涉及 `cedartoy-duel` 与 `cedartoy`；`cedartoy-duel-gateway` 仅转发且本次无改动，无需为此重启。此任务未操作任何生产进程。

## 2026-11-05 入口产品调整

双弈普通新建/邀请组局的「牌」类下拉独立列出「麻将 · 4人」（mahjong）和「襄阳卡五星 · 3人」（ka_wu_xing），固定人数4/3，无二级麻将玩法选择器。NPC补位和自带小机/邀约席位按目标人数联动，继续共用麻将renderer。此方案替代此前的「统一入口」描述。

本轮主站仅改 `README.md` 和本记录。首页/管理页原房间名映射已正确，独立game_type、钱包、邀请、根MCP与按需Guide接入保留，没有额外schema字段或服务端变更。

引擎工作区本轮24项针对性回归、DOM实际POST载荷/人数/席位检查，以及360/430/1280px浏览器专项通过；细节见引擎验收记录。本轮不重跑主站全量或上一轮19项，主站差量为文档，`git diff --check`通过。没有commit/push/部署或生产数据/进程操作。
