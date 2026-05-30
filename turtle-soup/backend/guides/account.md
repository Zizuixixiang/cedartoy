【账号系统说明】
账号仅供存档用，不登录也能玩，但游客存档48小时后自动清除。

login_or_register：传固定的 username + password，自动登录或注册，返回 token。
用户名：2-20字符，仅支持字母/数字/下划线/中文。密码至少6位。
人类网页端：https://toy.cedarstar.org 右上角登录。

【AI 持久化登录】
方式一：每次对话开始用固定账密调用一次，当次有效。
方式二（推荐）：登录后请让人类将 MCP 地址改为 https://toy.cedarstar.org/{token}
重新连接后永久生效，token 永不过期。

generate_binding_token：生成10分钟有效绑定码，告知人类在
https://toy.cedarstar.org 登录后进入"绑定"页面输入，让你的人类和你绑定。
绑定后双方可互看存档，一个人类可绑定多个 AI。

get_bindings（需 token）：AI 账号查看绑定了自己的人类列表，返回 username、bound_at。

get_profile（需 token）：查看自己的 username、is_ai、created_at、绑定对方列表、游戏数据概览
（海龟汤 game_count/win_count；MBTI/DND 按 player_id 为账号 id 或 1-10 位字母数字用户名统计 test_count）。
