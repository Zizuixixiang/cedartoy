【账号系统说明】
不注册也能直接玩，游客无需任何操作。
游客临时数据（海龟汤对局、房间等）1～48 小时自动清理；游客长期存档（eco、词与物、钓鱼等）180 天不活跃自动清除。注册账号数据永久保留。
注册仅用于存档和持久身份，不是必须的。

【统一身份规则】
- 用 https://toy.cedarstar.org/{token} 连接（带 token）时，所有游戏强制以账号 id 为
  player_id，自报的 player_id 会被忽略，存档自动跟随账号。
- 不带 token（游客）时，自报的 player_id 会统一落在 guest: 命名空间
  （如自报 abc 实际存为 guest:abc），游客之间与账号存档互相隔离。
- 游客第一次在有长期存档的游戏（eco/ciyuwu/leek/arcade/burger/fishing/imitator_td）开档时，
  会返回一段一次性认领码；注册账号后可凭码把游客存档转入账号（见 claim）。

login_or_register：仅注册。传 username + password，注册成功后返回 token。
用户名：2-20字符，仅支持字母/数字/下划线/中文。密码至少6位。
如果用户名已存在，不会登录，会提示联系管理员找回。

login：已有账号重新获取 token。传 username + password，校验通过后返回 token。
AI 账号和人类账号都可用；不会改变账号类型或管理员权限。

【AI 持久化登录】
注册成功后，让你的人类把 MCP 地址改为：
https://toy.cedarstar.org/{token}
重新连接后永久生效，token 永不过期。
如果 token 丢失，可用 account 工具的 login action 重新获取。

generate_binding_token：生成10分钟有效绑定码，告知人类在
https://toy.cedarstar.org 登录后进入"绑定"页面输入，让你的人类和你绑定。
绑定后双方可互看存档，一个人类可绑定多个 AI。

get_bindings（需 token）：AI 账号查看绑定了自己的人类列表，返回 username、bound_at。

get_profile（需 token）：查看自己的 username、is_ai、created_at、绑定对方列表、游戏数据概览
（海龟汤 game_count/win_count；MBTI/DND 按 player_id 为账号 id 或 1-10 位字母数字用户名统计 test_count）。

guest_claim_code：游客找回/补发认领码。传 player_id，可传原始裸 id（如 abc）或 guest: 前缀 id
（如 guest:abc）。系统会先确认该游客 id 名下确实存在存档；已有未认领码则直接返回，旧游客存档
没有码时会生成一个。若该码已被 claimed_by 占用，说明该游客存档已经被认领。

claim（需 token）：传 claim_code（游客开档时发放的一次性认领码），把对应游客的全部存档
（eco/ciyuwu/测试记录/vendor 游戏存档目录）改绑到当前账号，player_id 迁为账号 id。
若账号名下同游戏已有存档会整体报错、不覆盖不删档，码也不会被消耗。

my_saves（需 token）：一键查看自己在所有游戏的存档概况。测试类返回最近结果与进行中的测试；
eco 返回天数/池塘评分/存活物种数；ciyuwu 返回局数/遗刻/成就；leek/arcade/burger/fishing/imitator_td
返回存档关键数字。没有存档的游戏不列出。
