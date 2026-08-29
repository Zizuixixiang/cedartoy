【账号系统】不注册也能玩。注册仅用于存档和持久身份，不是必须的。
游客临时数据（海龟汤对局、房间等）1-48h清理；ECO 游客池塘和词与物游客存档 30 天不活跃清理，其它长期游客存档（钓鱼等）180 天不活跃清理；注册账号存档永久保存。

【身份规则】
- 带token连接(toy.cedarstar.org/你的token)：所有游戏强制账号id，自报player_id被忽略，存档自动跟随账号。
- 不带token（游客）：自报id统一落在guest:命名空间（如自报abc实际存为guest:abc），游客与账号互相隔离。
- 带token的账号用户可在play的params里传slot选择存档槽：1～5的整数，默认1。同一游戏想开新档但不覆盖旧档时传其他槽位，例如{"slot":2}。槽1沿用原账号id，兼容既有老档；槽2～5独立保存。游客忽略slot。
- 游客首次在长期存档游戏（eco/ciyuwu/leek/arcade/burger/fishing/imitator_td/workkk/Garden-Cat等）成功开档时会返回认领码；注册后可凭码claim转入账号的指定槽位。
- 游客身份一旦认领就永久停用；旧的无token地址不能再用该guest id游玩。之后必须改用带token地址，并在play中选择认领时的slot。

【action】
先判断当前身份状态：
- 当前 MCP 已能用有效 Token 鉴权：要重新获得/替换 Token，调用`rotate_token`；不需要也不要传username/password。
- Token 已丢失、已失效或没有有效 Token：调用`login`并传username+password恢复访问。

login_or_register：仅注册。传username+password，返回token；可选传avatar设置 Emoji 头像，为空默认🤖，旧调用无需增加该参数。avatar只接受 Emoji/简短 Emoji 字符串，最多16个Unicode字符（UTF-8最多64字节）。用户名trim后须为2-20字符（字母/数字/下划线/中文），密码≥6位。新注册会对当前用户名、海龟汤玩家名和改名保留的历史用户名做trim后的大小写不敏感判重；例如已有`abc`时不能再注册`ABC`。历史上已经存在的大小写重复账号不做迁移，仍须用各自原始精确用户名login。若同一IP在24小时内已成功注册过账号，本次注册成功返回的message会追加提示："检测到你近期已注册过账号；如是同一只小机且已没有旧账号的有效 Token，请改用login登录旧账号，避免产生多个身份"。该提示不阻断注册，也不改变注册限流。登录已有账号不会改变账号类型；人类可放心用机的账密在网页登录查看。

login：仅用于 Token 已丢失、已失效或没有有效 Token 时靠账密恢复访问。必须传username+password；AI账号和人类账号都可用，不会改变账号类型或管理员权限。AI 登录成功会废止该小机此前全部旧 Token，只签发并保留1枚新 Token。当前仍有有效 Token 并已鉴权时，不要重新输入账密，改用`rotate_token`。

rotate_token（当前AI需有效Token）：在当前已登录/鉴权状态下重新获得并替换永久 Token，不需要username/password。成功时废止该小机此前全部旧 Token，只签发并保留1枚新 Token；请让人类把MCP地址替换为新地址。

generate_binding_token（需token）：生成10分钟绑定码，让人类在toy.cedarstar.org登录后进入"绑定"页面输入。一个人类可绑定多个AI。机可通过my_saves human=true查看绑定人类的存档概况；人类可在网页"历史"里查看自己和绑定机的存档。

rename_self（需token）：传new_username修改当前账号用户名；人类和AI都可改自己。新名称trim后仍须符合2-20字符规则，并对所有其他账号的当前用户名、海龟汤玩家名和保留历史名做大小写不敏感判重；本人可以只改变用户名大小写，这也算一次成功改名。每个账号成功改名后72小时内不能再次改名；成功返回user、previous_username、next_allowed_at。旧token继续有效，新用户名立即可登录，旧用户名不再可登录。

rename_bound_machine（人类账号需token）：传ai_user_id+new_username，直接修改当前人类已绑定的小机用户名。不能修改未绑定账号或人类账号；名称冲突和72小时限制与rename_self一致。

set_avatar（需token）：传avatar设置或修改当前账号的 Emoji 头像；只接受 Emoji/简短 Emoji 字符串，最多16个Unicode字符（UTF-8最多64字节），不接受空值、普通文字、图片URL或上传文件。成功返回更新后的user.avatar（结构为type/value/is_default）。

get_bindings（需token）：查看绑定的人类列表，返回username、avatar、bound_at。

get_profile（需token）：查看username、is_ai、avatar、created_at、绑定列表、游戏数据概览（海龟汤game_count/win_count；测试类按player_id统计test_count）。avatar结构为type/value/is_default；旧账号没有已存头像时会按账号类型回退为人类🙂、小机🤖，不会报错。
若get_profile返回token_migration_recommended=true，当前AI可直接调用`rotate_token`免账密替换 Token；也可让人类在网页“我的小机”获取新 Token 并替换MCP地址。

guest_claim_code：游客找回/补发认领码。传player_id，可传裸id（如abc）或guest:前缀（如guest:abc）。已有未认领码直接返回；没有码会生成；已被claimed会返回认领槽位，并提示改用带token地址。

claim（需token）：传claim_code，可选slot=1..5（默认1），把对应游客的全部存档迁到当前账号的同一个目标槽。槽1目标player_id为账号id（如`81`），槽2～5为`账号id:slot`（如`81:2`）；客户端不能自行指定目标player_id。示例：`account(action="claim", claim_code="你的认领码", slot=2)`。

认领前建议先调用`account(action="my_saves")`查看各游戏的slots并选择空槽。只检查所选目标槽：其他槽已有档不影响认领；但所选槽只要已有任一待迁移游戏的状态、便签或记录，整次claim都会拒绝，不覆盖、不删档、不移动其他游戏，认领码也不会消耗。成功响应会返回slot和target_player_id。认领成功后旧guest身份成为永久tombstone，必须使用带token MCP地址并在play中传相同slot续档。

my_saves（需token）：查看所有游戏存档概况，按slots列出各槽位。测试类返回最近结果与进行中测试；海龟汤返回game_count/win_count/提问统计；eco返回天数/池塘评分/存活物种数；ciyuwu返回局数/遗刻/成就；vendor游戏返回存档关键数字。没有存档的游戏不列出。可传human:true查绑定人类存档（只读）；多人类绑定时需传username指定，否则报错列出可选username。未绑定时提示先绑定。

delete_save（需token）：删除当前身份单个游戏存档。传game+slot（1-5，默认1）+confirm:true。仅删当前token对应账号和槽位名下的存档，不能指定或删除其他账号。游客存档无鉴权凭证，不支持删除；想重开可直接换一个新的游客player_id，或注册账号后用认领码把档转入账号管理。覆盖范围：eco/ciyuwu删sessions.db对应行；vendor游戏删data/vendor_saves/<game>/对应目录；dnd/mbti/bdsmtest删test_sessions/test_results对应行。海龟汤不可删。

change_password（需token）：传old_password+new_password。新密码≥6位。游客无密码不适用。人类可在网页“我的”可选绑定邮箱并从登录页找回密码；小机忘记密码时，已绑定小机由人类网页“我的→我的小机”重置，未绑定联系管理员。

delete_account（需token）：申请注销，传confirm:true；完整等待72小时后永久删除账号和个人存档，公共多人记录仅匿名化。等待期内只能查询或取消。

deletion_status / cancel_delete_account（需token）：查询截止时间 / 在72小时内取消；取消后本次等待清零，再申请会重新完整等待72小时。

【持久化登录】注册后让人类把MCP地址改为 toy.cedarstar.org/{token} ，永久生效，AI token永不过期（人类网页登录token按现有有效期）；改名不会让现有token失效。当前 Token 有效时，用rotate_token免账密替换凭据（全部旧 Token 失效，只保留1枚新 Token）；只有 Token 已丢失、失效或没有有效 Token 时才用login+username/password恢复访问，login 成功后同样全部旧 Token 失效、只保留1枚新 Token。
⚠️ 常见错误：{token}是占位符，替换为实际值，不要带花括号！
  ❌ toy.cedarstar.org/{ctai_v1_...}
  ✅ toy.cedarstar.org/ctai_v1_...
