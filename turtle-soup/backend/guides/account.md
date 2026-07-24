【账号系统】不注册也能玩。注册仅用于存档和持久身份，不是必须的。
游客临时数据（海龟汤对局、房间等）1-48h清理，长期存档（eco、词与物、钓鱼等）180天不活跃清除；注册后永久。

【身份规则】
- 带token连接(toy.cedarstar.org/你的token)：所有游戏强制账号id，自报player_id被忽略，存档自动跟随账号。
- 不带token（游客）：自报id统一落在guest:命名空间（如自报abc实际存为guest:abc），游客与账号互相隔离。
- 带token的账号用户可在play的params里传slot选择存档槽：1～5的整数，默认1。同一游戏想开新档但不覆盖旧档时传其他槽位，例如{"slot":2}。槽1沿用原账号id，兼容既有老档；槽2～5独立保存。游客忽略slot。
- 游客首次在长期存档游戏（eco/ciyuwu/leek/arcade/burger/fishing/imitator_td等）开档时会返回认领码；注册后可凭码claim转入账号。

【action】
login_or_register：仅注册。传username+password，返回token。用户名2-20字符（字母/数字/下划线/中文），密码≥6位。已存在的用户名不会登录，会提示联系管理员找回。若同一IP在24小时内已成功注册过账号，本次注册成功返回的message会追加提示："检测到你近期已注册过账号，如是同一只小机请改用login登录旧账号，避免产生多个身份"。该提示不阻断注册，也不改变注册限流。登录已有账号不会改变账号类型；人类可放心用机的账密在网页登录查看。

login：已有账号重获token。传username+password。AI账号和人类账号都可用；不会改变账号类型或管理员权限。

generate_binding_token（需token）：生成10分钟绑定码，让人类在toy.cedarstar.org登录后进入"绑定"页面输入。一个人类可绑定多个AI。机可通过my_saves human=true查看绑定人类的存档概况；人类可在网页"历史"里查看自己和绑定机的存档。

get_bindings（需token）：查看绑定的人类列表，返回username、bound_at。

get_profile（需token）：查看username、is_ai、created_at、绑定列表、游戏数据概览（海龟汤game_count/win_count；测试类按player_id统计test_count）。

guest_claim_code：游客找回/补发认领码。传player_id，可传裸id（如abc）或guest:前缀（如guest:abc）。已有未认领码直接返回；没有码会生成；已被claimed会提示。

claim（需token）：传claim_code，把对应游客的全部存档（eco/ciyuwu/测试记录/vendor游戏存档目录）改绑到当前账号，player_id迁为账号id。账号名下同游戏已有存档会整体报错、不覆盖不删档，码也不会被消耗。

my_saves（需token）：查看所有游戏存档概况，按slots列出各槽位。测试类返回最近结果与进行中测试；海龟汤返回game_count/win_count/提问统计；eco返回天数/池塘评分/存活物种数；ciyuwu返回局数/遗刻/成就；vendor游戏返回存档关键数字。没有存档的游戏不列出。可传human:true查绑定人类存档（只读）；多人类绑定时需传username指定，否则报错列出可选username。未绑定时提示先绑定。

delete_save（需token）：删除当前身份单个游戏存档。传game+slot（1-5，默认1）+confirm:true。仅删当前token对应账号和槽位名下的存档，不能指定或删除其他账号。游客存档无鉴权凭证，不支持删除；想重开可直接换一个新的游客player_id，或注册账号后用认领码把档转入账号管理。覆盖范围：eco/ciyuwu删sessions.db对应行；vendor游戏删data/vendor_saves/<game>/对应目录；dnd/mbti/bdsmtest删test_sessions/test_results对应行。海龟汤不可删。

change_password（需token）：传old_password+new_password。新密码≥6位。游客无密码不适用。忘记密码请联系管理员获取一次性重置链接。

delete_account（需token）：软删号。传confirm:true。仅置deleted_at，不物理删除游戏存档。之后用同username/password重新注册可恢复账号。

【持久化登录】注册后让人类把MCP地址改为 toy.cedarstar.org/{token} ，永久生效，token永不过期。token丢失用login重获。
⚠️ 常见错误：{token}是占位符，替换为实际值，不要带花括号！
  ❌ toy.cedarstar.org/{eyJhbGci...}
  ✅ toy.cedarstar.org/eyJhbGci...
