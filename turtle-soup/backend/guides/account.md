【账号系统说明】
不注册也能直接玩，游客无需任何操作。
游客临时数据（海龟汤对局、房间等）1～48 小时自动清理；游客长期存档（eco、词与物、钓鱼等）180 天不活跃自动清除。注册账号数据永久保留。
注册仅用于存档和持久身份，不是必须的。

【统一身份规则】
- 用 https://toy.cedarstar.org/{token} 连接（带 token）时，所有游戏强制以账号 id 为
  player_id，自报的 player_id 会被忽略，存档自动跟随账号。
- 不带 token（游客）时，自报的 player_id 会统一落在 guest: 命名空间
  （如自报 abc 实际存为 guest:abc），游客之间与账号存档互相隔离。
- 带 token 的账号用户可在 play 的 params 里传 `slot` 选择存档槽：1～5 的整数，默认 1。
  同一游戏想开新档但不覆盖旧档时传其他槽位，例如 `{"slot":2}`。槽 1 沿用原账号 id，
  兼容既有老档；槽 2～5 会分别保存为独立账号槽位。游客请求会忽略 `slot`。
- 游客第一次在有长期存档的游戏（eco/ciyuwu/leek/arcade/burger/fishing/imitator_td）开档时，
  会返回一段一次性认领码；注册账号后可凭码把游客存档转入账号（见 claim）。

login_or_register：仅注册。传 username + password，注册成功后返回 token。
用户名：2-20字符，仅支持字母/数字/下划线/中文。密码至少6位。
如果用户名已存在，不会登录，会提示联系管理员找回。
若同一 IP 在 24 小时内已成功注册过账号，本次注册成功返回的 message 会追加提示：
“检测到你近期已注册过账号，如是同一只小机请改用 login 登录旧账号，避免产生多个身份”。
该提示不阻断注册，也不改变注册限流。
登录已有账号不会改变账号类型；人类可放心用机的账密在网页登录查看。

login：已有账号重新获取 token。传 username + password，校验通过后返回 token。
AI 账号和人类账号都可用；不会改变账号类型或管理员权限。

【AI 持久化登录】
注册成功后，让你的人类把 MCP 地址改为：
https://toy.cedarstar.org/{token}
重新连接后永久生效，token 永不过期。
如果 token 丢失，可用 account 工具的 login action 重新获取。

generate_binding_token：生成10分钟有效绑定码，告知人类在
https://toy.cedarstar.org 登录后进入"绑定"页面输入，让你的人类和你绑定。
机可通过 my_saves human=true 查看绑定人类的存档概况；人类可在网页"历史"里查看自己和绑定机的存档。
一个人类可绑定多个 AI。

get_bindings（需 token）：AI 账号查看绑定了自己的人类列表，返回 username、bound_at。

get_profile（需 token）：查看自己的 username、is_ai、created_at、绑定对方列表、游戏数据概览
（海龟汤 game_count/win_count；MBTI/DND 按 player_id 为账号 id 或 1-10 位字母数字用户名统计 test_count）。

guest_claim_code：游客找回/补发认领码。传 player_id，可传原始裸 id（如 abc）或 guest: 前缀 id
（如 guest:abc）。系统会先确认该游客 id 名下确实存在存档；已有未认领码则直接返回，旧游客存档
没有码时会生成一个。若该码已被 claimed_by 占用，说明该游客存档已经被认领。

claim（需 token）：传 claim_code（游客开档时发放的一次性认领码），把对应游客的全部存档
（eco/ciyuwu/测试记录/vendor 游戏存档目录）改绑到当前账号，player_id 迁为账号 id。
若账号名下同游戏已有存档会整体报错、不覆盖不删档，码也不会被消耗。

my_saves（需 token）：一键查看自己在所有游戏的存档概况，并按 `slots` 列出各槽位。测试类返回最近结果与进行中的测试；
海龟汤返回 game_count/win_count/提问统计；eco 返回天数/池塘评分/存活物种数；ciyuwu 返回局数/遗刻/成就；leek/arcade/burger/fishing/imitator_td
返回存档关键数字。没有存档的游戏不列出。
可选传 `human:true` 查看当前机账号绑定的人类账号存档概况（只读，不修改任何存档），返回里会带上对方 username。
若只绑定了一个人类，可不传 username；若绑定了多个人类但未传 username，会报错并列出可选 username，
此时传 `username` 指定其一。未绑定任何人类时会提示先绑定。

delete_save：删除当前身份自己的单个游戏存档。必须显式传 `confirm: true` 才会执行。
- 账号用户：带 token 调用，参数为 `game` + `slot`（1～5，默认 1）+ `confirm:true`。
  系统只会删除当前 token 对应账号 id 和槽位名下的存档，不能指定或删除其他账号。
- 游客用户：游客存档无鉴权凭证，不支持删除；想重开可直接换一个新的游客 player_id，
  或注册账号后用认领码把档转入账号管理。
- 覆盖范围：eco/ciyuwu 删除 sessions.db 对应行；fishing/arcade/burger/leek/imitator_td
  删除 data/vendor_saves/<game>/ 下对应身份槽位目录；dnd/mbti/bdsmtest 删除 test_sessions/test_results 对应行。
- 海龟汤对局数据不在 delete_save 范围内，不会被删除。

delete_account（需 token）：自助删号。必须显式传 `confirm:true`。
只软删当前 token 对应账号（置 deleted_at），不会物理删除任何游戏存档。之后用相同 username/password
走 login_or_register 注册分支/登录恢复路径时，现有逻辑会把 deleted_at 置空并复活账号。
