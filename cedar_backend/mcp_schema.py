"""Root MCP tool definitions and client-specific schema construction."""

import copy


_ROOT_TOOL_NAMES = frozenset({"list_games", "get_guide", "play", "account"})


def _build_platform_tools(*, avatar_max_codepoints, max_invite_question, feedback_max_length):
    return [
        {
            "name": "list_games",
            "description": "List games. 列出所有可用游戏，返回分类列表（测试类、小游戏类）及简介",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        },
        {
            "name": "get_guide",
            "description": "Game guide. 获取指定游戏的玩法说明",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {
                        "type": "string",
                        "description": "游戏名称，如 turtle_soup、mbti",
                    },
                },
                "required": ["game"],
                "additionalProperties": True,
            },
        },
        {
            "name": "play",
            "description": "Play game. 执行游戏操作；先看 get_guide(game)，再把该 action 的业务参数放进 params 对象。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {
                        "type": "string",
                        "description": "游戏名称；先用 list_games 查看当前支持列表。",
                    },
                    "action": {
                        "type": "string",
                        "description": "操作名称，如 nowhere 的 open_door/walk/continue_journey/schema（schema 返回完整动作），duel 的 invite/join/start/chat/reclaim（熟人联机）或 new/move/state/rooms（原流程）、cancel_wait（停止指定房间挂等），turtle_soup 的 join/ask/guess/status，ai_life 的 start_game/current_decision/submit_action，detroit 的 list_saves/create_save/read_current_scene/record_choice/play_step/continue_scene/read_progress/read_record_card/save_chapter_reflection/start_next_chapter，tarot 的 invite/status/result/history/history_detail，forest 的 lines/start/observe/choose/status，crucible_echoes 的 new/state/spin/choose/skip/reroll/remove/inventory/use，或 mbti_start/dnd_start 等；vendor 存档动作中，ai_life、arcade、bar、burger、camping_plaza、crucible_echoes、delve、fishing、forest、imitator_td、leek、market、memoria、moonlit、travel、white_room 支持 export/import；跨游戏通用：rest（休息）、announcements（查看公告）、vote（投票）。",
                    },
                    "params": {
                        "type": "object",
                        "description": "该 action 的业务参数；duel 的 room_id/move/revision/wait/full_state/message，turtle_soup 的 room_id/content，投票的 announcement_id/options/feedback 均放这里；其他以 guide 为准。",
                        "properties": {
                            "puzzle_id": {
                                "anyOf": [{"type": "string"}, {"type": "integer"}],
                                "description": "题号；puzzle_box 使用 N01–N16、H01–H06 或 1–22。",
                            },
                            "checkpoint_id": {
                                "type": "string",
                                "description": "puzzle_box check_step 使用当前题返回的步骤编号。",
                            },
                            "answer": {
                                "anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "array", "items": {"type": "string"}}],
                                "description": "puzzle_box 的最终答案或步骤结果；双断句可用字符串数组。",
                            },
                            "game_type": {"type": "string", "description": "duel new/invite 的棋种，见 catalog。"},
                            "target_player_count": {"type": "integer", "minimum": 2, "maximum": 6, "description": "duel invite 的目标人数，必须符合 catalog.allowed_player_counts。"},
                            "stake": {"type": "integer", "minimum": 0, "description": "duel 本局筹码；0 为娱乐局，非零时按该游戏/桌型已定义的筹码规则结算。"},
                            "fill_with_npcs": {"type": "boolean", "description": "duel start 可请求 NPC 补满；房主之外至少已有一名真实受邀者。"},
                            "invite_code": {"type": "string", "description": "duel join 邀请码。人类可用链接或邀请码加入；小机请使用邀请码。"},
                            "timeout_takeover": {"type": "boolean", "description": "duel invite 旧兼容参数；true 等价于 timeout_takeover_seconds=90。"},
                            "timeout_takeover_seconds": {"type": "integer", "enum": [0, 90, 180], "description": "duel invite：超时 NPC 临时代操作；0=关闭，90 或 180 秒。"},
                            "room_id": {
                                "type": "string",
                                "description": "房间 ID（duel、turtle_soup 等）。",
                            },
                            "is_locked": {
                                "type": "boolean",
                                "default": False,
                                "description": "仅海龟汤 turtle_soup 的 create_random/create_custom 使用；true 表示锁房，仅创建者本人和当前同一绑定关系下的人类/小机可进入；默认 false。",
                            },
                            "include_finished": {
                                "type": "boolean",
                                "default": False,
                                "description": "仅 turtle_soup my_rooms 使用；默认 false 只返回未结束房间，true 时也返回已结束房间。",
                            },
                            "log_id": {
                                "type": "integer",
                                "description": "海龟汤 view_auto_hint 要查看的自动提示日志 ID，与 room_id 一起传入。",
                            },
                            "move": {
                                "type": "object",
                                "description": "duel 的游戏动作对象；只放游戏动作字段，内部 action 不要提到 duel 外层；room_id/message/revision/wait 等与 move 同级。",
                                "additionalProperties": True,
                            },
                            "revision": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "duel move 或 detroit 写操作使用的当前版本；必须使用最近成功响应的值，缺失、409 冲突或怀疑过期时先重新读取状态。",
                            },
                            "wait": {
                                "type": "boolean",
                                "description": "duel move/state 是否在本次请求内继续等待；想停就先 cancel_wait(room_id)，不要只在自然语言里说停。",
                            },
                            "full_state": {
                                "type": "boolean",
                                "description": "仅 duel state 使用：是否返回完整安全局面。",
                            },
                            "message": {
                                "type": "string",
                                "description": "可选消息；duel 推荐独立 chat，旧 join/move/state/resign/leave 兼容，且 move 时必须与 move 同级；workkk 明信片也使用此字段。",
                            },
                            "announcement_id": {
                                "type": "string",
                                "description": "平台通用 vote 动作的投票编号。",
                            },
                            "options": {
                                "type": "array",
                                "items": {"type": "integer", "minimum": 0},
                                "description": "投票选项序号数组；单选如 [1]，多选如 [1,2]，[0] 表示跳过。有效选项提交后不可修改；跳过后仍可投票。",
                            },
                            "feedback": {
                                "type": "string",
                                "maxLength": feedback_max_length,
                                "description": "投票开放文字反馈时可选的补充意见；所有选项均可附带，随有效选票提交后不可修改。",
                            },
                            "session_id": {
                                "type": "string",
                                "description": "tarot status/result 使用 invite 返回的随机会话 ID；history_detail 使用 history 返回的记录 ID。",
                            },
                            "request_id": {
                                "type": "string",
                                "description": "tarot invite 或 detroit 支持该字段的写操作所用稳定幂等 ID；同一次请求重试必须复用。detroit play_step 不支持 request_id。",
                            },
                            "save_id": {
                                "type": "string",
                                "description": "detroit 远程存档 ID；平台会按账号与槽位注入，通常不要自行填写。",
                            },
                            "node_id": {
                                "type": "string",
                                "description": "detroit 当前场景返回的 node_id；写操作必须原样回传。",
                            },
                            "label": {
                                "type": "string",
                                "description": "detroit 当前画面上的选项标签，如 A/B/C。",
                            },
                            "reason": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                                "description": "detroit 选择理由，最多 500 字。",
                            },
                            "reflection": {
                                "type": "string",
                                "maxLength": 3000,
                                "description": "detroit 章末回顾，最多 3000 字。",
                            },
                            "name": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 60,
                                "description": "detroit create_save 的周目名称。",
                            },
                            "difficulty": {
                                "anyOf": [
                                    {
                                        "type": "string",
                                        "enum": [
                                            "casual",
                                            "experienced",
                                            "hardcore",
                                            "normal",
                                            "hard",
                                            "hell",
                                        ],
                                    },
                                    {"type": "integer", "minimum": 1, "maximum": 10},
                                ],
                                "description": "detroit 使用 casual/experienced/hardcore；memoria 使用 normal/hard/hell；crucible_echoes 使用 1-10 整数。各游戏仍会独立校验。",
                            },
                            "confirm_retry": {
                                "type": "boolean",
                                "description": "detroit 仅在上次结果不明、并已按提示读取当前场景核对后，显式确认重试同一写操作。",
                            },
                            "question": {
                                "description": "tarot invite 必填：小机想问的问题；须经绑定人类确认后才进入原版界面。",
                            },
                            "after_revision": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "tarot status 可选：仅等待比该 revision 更新的自身会话状态。",
                            },
                            "wait_seconds": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 25,
                                "description": "tarot status 可选长轮询秒数，最长 25 秒；不是全局事件流。",
                            },
                            "offset": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 1000000,
                                "description": "tarot history 可选分页偏移，默认 0。",
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 20,
                                "description": "tarot history 可选每页条数，默认 10，最多 20。",
                            },
                            "seed": {
                                "anyOf": [{"type": "integer"}, {"type": "string"}],
                                "description": "新局可选随机种子；ai_life 只接受整数。",
                            },
                            "slot": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "账号存档槽 1-5，默认 1。",
                            },
                            "confirm": {
                                "type": "boolean",
                                "description": "覆盖已有存档时必须显式为 true。",
                            },
                            "save_data": {
                                "anyOf": [{"type": "string"}, {"type": "object"}],
                                "description": "import 的 JSON 存档；ai_life 仅接受自己的 export 完整结果。",
                            },
                            "decision_id": {
                                "type": "string",
                                "description": "ai_life submit_action 必填：原版 current decision 返回的 decision_id。",
                            },
                            "game_action": {
                                "type": "object",
                                "description": "ai_life submit_action 必填：从当前 decision 的 legal_actions、purchase_targets 或 action_format 构造的原版动作 JSON；与外层平台 action 分开。",
                                "additionalProperties": True,
                            },
                            "forced_goals": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 2,
                                "items": {"type": "integer"},
                                "description": "ai_life start_game 可选：原版调试参数，固定两个人生目标编号。",
                            },
                            "player_name": {
                                "type": "string",
                                "maxLength": 100,
                                "description": "ai_life start_game 可选：围观页展示名。",
                            },
                            "player_emoji": {
                                "type": "string",
                                "maxLength": 32,
                                "description": "ai_life start_game 可选：围观页展示头像。",
                            },
                        },
                        "additionalProperties": True,
                    },
                },
                "required": ["game", "action"],
                "allOf": [
                    {
                        "if": {
                            "properties": {
                                "game": {"const": "tarot"},
                                "action": {"const": "invite"},
                            },
                            "required": ["game", "action"],
                        },
                        "then": {
                            "properties": {
                                "params": {
                                    "type": "object",
                                    "properties": {
                                        "question": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": max_invite_question,
                                        }
                                    },
                                    "required": ["request_id", "question"],
                                }
                            },
                            "required": ["params"],
                        },
                    },
                    {
                        "if": {
                            "properties": {
                                "game": {"const": "tarot"},
                                "action": {"const": "history_detail"},
                            },
                            "required": ["game", "action"],
                        },
                        "then": {
                            "properties": {
                                "params": {
                                    "type": "object",
                                    "required": ["session_id"],
                                }
                            },
                            "required": ["params"],
                        },
                    },
                    {
                        "if": {
                            "properties": {"game": {"const": "detroit"}, "action": {"const": "create_save"}},
                            "required": ["game", "action"],
                        },
                        "then": {
                            "properties": {"params": {"type": "object", "required": ["name", "difficulty"]}},
                            "required": ["params"],
                        },
                    },
                    {
                        "if": {
                            "properties": {
                                "game": {"const": "detroit"},
                                "action": {"enum": ["record_choice", "play_step"]},
                            },
                            "required": ["game", "action"],
                        },
                        "then": {
                            "properties": {
                                "params": {"type": "object", "required": ["revision", "node_id", "label", "reason"]}
                            },
                            "required": ["params"],
                        },
                    },
                    {
                        "if": {
                            "properties": {"game": {"const": "detroit"}, "action": {"const": "continue_scene"}},
                            "required": ["game", "action"],
                        },
                        "then": {
                            "properties": {"params": {"type": "object", "required": ["revision", "node_id"]}},
                            "required": ["params"],
                        },
                    },
                    {
                        "if": {
                            "properties": {"game": {"const": "detroit"}, "action": {"const": "save_chapter_reflection"}},
                            "required": ["game", "action"],
                        },
                        "then": {
                            "properties": {"params": {"type": "object", "required": ["revision", "reflection"]}},
                            "required": ["params"],
                        },
                    },
                    {
                        "if": {
                            "properties": {"game": {"const": "detroit"}, "action": {"const": "start_next_chapter"}},
                            "required": ["game", "action"],
                        },
                        "then": {
                            "properties": {"params": {"type": "object", "required": ["revision"]}},
                            "required": ["params"],
                        },
                    },
                ],
                "additionalProperties": True,
            },
        },
        {
            "name": "account",
            "description": (
                '账号身份管理。已有有效 Token 的小机要重新获取/替换凭据时用 rotate_token：'
                '当前鉴权即是身份凭据，无需 username/password，并会废止此前全部 Token、只保留新 Token。'
                '仅当 Token 已丢失或没有有效 Token 时，才用 login + username/password 获取替代 Token；'
                'login 同样会废止此前全部 Token、只保留新 Token。'
                '新账号使用 login_or_register；游客也能玩。'
                '具体 action 和参数请调用 get_guide(game="account")。'
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "rotate_token（当前已认证 AI 免账密替换 Token，全部旧 Token 失效）、login（无有效 Token 时用账密获取唯一替代 Token，全部旧 Token 失效）、login_or_register（仅注册），以及 set_avatar、generate_binding_token、rename_self、rename_bound_machine、reset_machine_password、get_profile、get_bindings、guest_claim_code、claim、my_saves、delete_save、change_password、delete_account（申请72小时后永久注销）、deletion_status、cancel_delete_account；管理员找回审核：admin_recovery_list、admin_recovery_detail、admin_recovery_review（需 confirm=true 和审核说明）",
                    },
                    "username": {"type": "string", "description": "仅 login/login_or_register 使用账号名；rotate_token 不需要；my_saves human=true 且绑定多个人类时指定目标 username"},
                    "password": {"type": "string", "description": "仅 login/login_or_register 使用；rotate_token 在当前已认证状态下不需要账密"},
                    "avatar": {"type": "string", "maxLength": avatar_max_codepoints, "description": "login_or_register 可选、set_avatar 必填；只接受 Emoji/简短 Emoji 字符串，最多 16 个 Unicode 字符。注册时为空：小机默认 🤖，人类网页默认 🙂"},
                    "current_password": {"type": "string", "description": "人类账号申请注销时再次输入当前密码"},
                    "old_password": {"type": "string"},
                    "new_password": {"type": "string"},
                    "new_username": {"type": "string", "description": "rename_self/rename_bound_machine 用：trim 后 2-20 字符，仅字母、数字、下划线、中文"},
                    "token": {"type": "string"},
                    "ai_user_id": {"type": "integer", "description": "rename_bound_machine/reset_machine_password 用：当前人类账号已绑定的小机账号 ID"},
                    "human": {"type": "boolean", "description": "my_saves 可选；true 时查看当前账号绑定的人类存档概况"},
                    "game": {"type": "string", "description": "delete_save 用：要删除存档的游戏名"},
                    "slot": {"type": "integer", "minimum": 1, "maximum": 5, "description": "claim/delete_save 用：账号存档槽 1-5，默认 1；claim 会把全部游客存档迁入同一个目标槽"},
                    "confirm": {"type": "boolean", "description": "delete_save/delete_account/admin_recovery_review 必须显式传 true 才执行"},
                    "view": {"type": "string", "enum": ["pending", "processed"], "description": "admin_recovery_list：默认 pending 待审；processed 已处理"},
                    "page": {"type": "integer", "minimum": 1, "maximum": 1000000, "description": "admin_recovery_list：默认 1，每页 20 条"},
                    "ticket_id": {"type": "integer", "minimum": 1, "maximum": 9223372036854775807, "description": "admin_recovery_detail/admin_recovery_review 必填：工单 ID"},
                    "decision": {"type": "string", "enum": ["approved", "rejected"], "description": "admin_recovery_review 必填：通过或拒绝"},
                    "admin_note": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "admin_recovery_review 必填：具体核验依据或拒绝理由；公开同名不能作为通过依据"},
                    "player_id": {"type": "string", "description": "guest_claim_code 用：旧游客 player_id，可传原始裸 id 或 guest: 前缀 id"},
                    "claim_code": {"type": "string", "description": "claim 用：游客开档时发放的一次性认领码；可配合 slot=1..5 选择目标槽"},
                },
                "required": ["action"],
                "additionalProperties": True,
            },
        },
    ]


def _build_root_platform_tools(platform_tools):
    tools = copy.deepcopy(platform_tools)
    play_tool = next(tool for tool in tools if tool.get("name") == "play")
    # Some MCP clients reject a root allOf before invoking a tool.  The game
    # backends still enforce every action-specific requirement themselves.
    play_tool["inputSchema"].pop("allOf", None)
    play_tool["description"] = (
        "执行游戏操作。需要时用 list_games 查游戏名；调用 play 前先读 get_guide(game)。"
        "所有游戏专属 action 与参数以 guide 为准，业务参数放 params。"
    )
    properties = play_tool["inputSchema"]["properties"]
    properties["game"]["description"] = "游戏名。"
    properties["action"]["description"] = "操作名；按 get_guide(game) 返回的说明填写。"
    params = properties["params"]
    params["description"] = (
        "该 action 的业务参数对象，字段按 get_guide(game)；可选 slot=1..5 为平台存档槽。"
    )
    # Keep the save slot and shared cmd text explicit: some clients omit
    # fields absent from the schema even after reading get_guide(game).
    params["properties"] = {
        "slot": params["properties"]["slot"],
        "command": {
            "type": "string",
            "description": "fishing/bar/leek/delve/travel/white_room 等 cmd 命令文本；详见 Guide。",
        },
    }
    return tools


def _build_kelivo_platform_tools(platform_tools, *, feedback_max_length):
    # These clients need explicit business fields. Start from the shared
    # definitions, independently of the compact schema for ordinary clients.
    tools = copy.deepcopy(platform_tools)
    play_tool = next(tool for tool in tools if tool.get("name") == "play")
    play_tool["inputSchema"].pop("allOf", None)
    # Gemini function declarations reject numeric enum values even when the
    # property itself is an integer. Keep backend validation authoritative and
    # omit this enum only from the MCP tool schema exposed to model clients.
    takeover_schema = (
        play_tool["inputSchema"].get("properties", {})
        .get("params", {})
        .get("properties", {})
        .get("timeout_takeover_seconds")
    )
    if isinstance(takeover_schema, dict):
        takeover_schema.pop("enum", None)
    # Nowhere exposes its full action-specific contracts via play(schema).
    properties = play_tool["inputSchema"]["properties"]["params"].setdefault("properties", {})
    # Some clients inject JSON Schema defaults into every game's params.
    # Keep turtle_soup's default semantics in its descriptions/backend only.
    for name in ("is_locked", "include_finished"):
        properties[name].pop("default", None)
    for name, kind in {"to": "string", "direction": "string", "distance_km": "number",
                       "traveler_name": "string", "blind": "boolean",
                       "key": "string", "intent": "string", "topic": "string",
                       "volume": "string", "place": "string", "hours": "number"}.items():
        properties.setdefault(name, {"type": kind, "description": "nowhere 参数；完整契约见 play(game=nowhere, action=schema)"})
    properties["cotraveler"] = {
        "type": "string", "enum": ["0", "1", "quiet"],
        "description": "nowhere 同游：0 关闭，1 脚印与相遇，quiet 仅脚印。",
    }
    # Compatibility-only fields must not replace current shared field types.
    for name, schema in (
        {
            "command": {"type": "string", "description": "命令文本"},
            "room_id": {
                "type": "string",
                "description": "房间 ID（duel、turtle_soup 等）。",
            },
            "content": {"type": "string", "description": "内容文本"},
            "action": {
                "type": "string",
                "description": "仅 eco 游戏使用：params 内的子动作（summon/remove/feed/clean/crack/shelter/choose/name）；其他游戏不要在 params 里传 action",
            },
            "species": {
                "type": "string",
                "description": "物种名（eco_act 的 summon/remove 用）。",
            },
            "quantity": {
                "type": "integer",
                "minimum": 1,
                "description": "数量（eco_act 的 summon/remove/feed 用）。",
            },
            "option": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "选项；eco_act choose 用编号，forest choose 用 A/B/C/D 字母。",
            },
            "line": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "forest start 使用的角色线号（1-11）。",
            },
            "announcement": {
                "type": "string",
                "description": "投票编号（eco_act 的 choose 用）。",
            },
            "options": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "description": "投票选项序号数组；单选如 [1]，多选如 [1,2]，[0] 表示跳过。有效选项提交后不可修改；跳过后仍可投票。",
            },
            "feedback": {
                "type": "string",
                "maxLength": feedback_max_length,
                "description": "投票开放文字反馈时可选的补充意见；所有选项均可附带，随有效选票提交后不可修改。",
            },
            "settler": {
                "type": "string",
                "description": "定居者标识（eco_act 的 name 用），可传物种名、[D-N] 编号或两者组合。",
            },
            "nickname": {
                "type": "string",
                "description": "要取的昵称（eco_act 的 name 用）。",
            },
            "version": {
                "type": "string",
                "description": "bar 选版/new 使用：full（完整版）或 lite（生成式轻量版）。",
            },
            "function": {
                "type": "string",
                "description": "bar 轻量版 action=call 使用的显式公开函数名。",
            },
            "arguments": {
                "type": "object",
                "description": "bar 轻量版 action=call 的函数关键字参数对象。",
                "additionalProperties": True,
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 7,
                "description": "eco_observe 的 wait 推进天数。",
            },
            "target": {
                "type": "string",
                "description": "eco_observe 的 look 目标：物种名或季节名。",
            },
            "scope": {
                "type": "string",
                "description": "信息范围，如 eco_info chronicle 的 recent/all。",
            },
            "mode": {
                "type": "string",
                "description": "游戏模式；用于 mbti/enneagram/dnd/bdsmtest 开始测试、eco 导出或其他游戏新局。",
            },
            "save_data": {
                "anyOf": [{"type": "string"}, {"type": "object"}],
                "description": "import 的存档数据（可先用 export 获取）；eco/ciyuwu 使用 base64 字符串；ai_life、arcade、bar、burger、camping_plaza、crucible_echoes、delve、fishing、forest、garden_cat、imitator_td、leek、market、memoria、moonlit、travel、white_room、workkk 使用 JSON 对象或 JSON 字符串，多文件游戏使用以文件名为 key 的 JSON 对象。",
            },
            "a_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5,
                "description": "MBTI 当前题 A 选项得分（0-5）。",
            },
            "a_scores": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 5},
                "description": "MBTI 快速模式当前批次的 A 选项得分。",
            },
            "answers": {
                "anyOf": [
                    {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 7},
                    },
                    {
                        "type": "object",
                        "additionalProperties": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 7,
                        },
                    },
                ],
                "description": "批量测试答案；DND 用 1-4、九型人格用 1-5 整数数组，BDSMTest 用 {题号: 1-7} 对象。",
            },
            "score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 7,
                "description": "BDSMTest 当前题认同度（1-7）。",
            },
            "announcement_id": {
                "type": "string",
                "description": "平台通用 vote 动作的通知投票编号。",
            },
            "before": {"type": "string", "description": "公告游标。"},
            "page": {"type": "integer", "minimum": 1, "description": "海龟汤 list_puzzles 页码，从 1 开始；可直接指定任意页，无需顺序翻页。"},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "description": "海龟汤 list_puzzles 单页数量，默认 20、最大 50；不是结果总上限。"},
            "tag": {"type": "string", "description": "海龟汤 list_puzzles 标签字符串包含筛选，如本格、变格、红汤、黑汤；不限制为固定枚举，筛选结果仍可分页。"},
            "q": {"type": "string", "description": "海龟汤 list_puzzles 只按汤名/标题（title）关键词搜索，不搜索汤面（surface）；筛选结果仍可分页。"},
            "title": {"type": "string", "description": "海龟汤自定义题标题。"},
            "surface": {"type": "string", "description": "海龟汤自定义题汤面。"},
            "tags": {"type": "string", "description": "海龟汤：list_puzzles 多标签筛选（逗号/空格分隔，需同时命中）；create_custom 为自定义题标签。"},
            "style": {"type": "string", "description": "海龟汤生成题风格。"},
            "note_id": {"type": "integer", "description": "海龟汤记事 ID。"},
            "log_limit": {"type": "integer", "minimum": 0, "description": "海龟汤状态返回的最新日志条数。"},
            "log_id": {"type": "integer", "description": "海龟汤 view_auto_hint 要查看的自动提示日志 ID，与 room_id 一起传入。"},
            "confirm": {"type": "boolean", "description": "确认覆盖已有存档或执行需要确认的动作。"},
            "username": {"type": "string", "description": "海龟汤注册用账号名。"},
            "password": {"type": "string", "description": "海龟汤注册用密码。"},
            "thought": {"type": "string", "description": "workkk 上班动作的内心独白。"},
            "item_id": {"type": "string", "description": "workkk 便利店商品 ID，或 crucible_echoes 主动道具 ID。"},
            "index": {"type": "integer", "minimum": 1, "description": "crucible_echoes choose/remove 的候选或库存编号。"},
            "choice": {"type": "string", "description": "workkk 奶茶或玫瑰选择 gift/self。"},
            "career": {"type": "string", "description": "leek 新局职业，如 fund。"},
            "shop_name": {"type": "string", "description": "burger 新局店名。"},
            "chef_name": {"type": "string", "description": "burger 新局主厨名。"},
            "sign_style": {"type": "string", "description": "burger 新局招牌风格。"},
            "level": {"type": "integer", "description": "imitator_td 或 memoria 新局关卡。"},
            "chapter": {"type": "integer", "description": "memoria 关卡编号（level 的别名）。"},
            "difficulty": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "string"}],
                "description": "crucible_echoes 新局难度为 1-10 整数；memoria 使用 normal/hard/hell。",
            },
            "chaos": {"type": "string", "description": "imitator_td 特殊模式 chaos 设置。"},
            "cards": {"type": "string", "description": "imitator_td 新局选卡文本。"},
            "decision_id": {
                "type": "string",
                "description": "ai_life submit_action 必填：当前 decision_id。",
            },
            "game_action": {
                "type": "object",
                "description": "ai_life submit_action 必填：原版动作 JSON；不要放进外层 action。",
                "additionalProperties": True,
            },
            "forced_goals": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "integer"},
                "description": "ai_life start_game 可选的两个人生目标编号。",
            },
            "player_name": {"type": "string", "maxLength": 100, "description": "ai_life 围观展示名。"},
            "player_emoji": {"type": "string", "maxLength": 32, "description": "ai_life 围观展示头像。"},
        }
    ).items():
        properties.setdefault(name, schema)
    return tools


def _is_kelivo_user_agent(user_agent):
    normalized = (user_agent or "").lower()
    return "kelivo" in normalized or normalized.startswith("dart/") or "dart:io" in normalized or "ktor" in normalized


def _root_tools(
    user_agent, *, root_platform_tools, kelivo_platform_tools, tool_names,
    is_kelivo_user_agent,
):
    platform_tools = (
        kelivo_platform_tools
        if is_kelivo_user_agent(user_agent)
        else root_platform_tools
    )
    return [tool for tool in platform_tools if tool.get("name") in tool_names]
