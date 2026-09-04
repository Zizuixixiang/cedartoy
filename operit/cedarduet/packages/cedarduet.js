/* METADATA
{
  "name": "cedarduet",
  "display_name": {"zh": "CedarDuet 双弈", "en": "CedarDuet"},
  "description": {
    "zh": "每个 callerCardId 使用独立的 CedarToy 小机会话；玩家身份与隐私视角均由服务端决定。",
    "en": "Each callerCardId uses an isolated CedarToy machine session; identity and privacy are server-controlled."
  },
  "enabledByDefault": true,
  "category": "Game",
  "env": [
    {
      "name": "CEDARTOY_BASE_URL",
      "description": {"zh": "CedarToy 服务地址", "en": "CedarToy service URL"},
      "required": false,
      "defaultValue": "https://toy.cedarstar.org"
    }
  ],
  "tools": [
    {
      "name": "session_register",
      "description": {"zh": "为当前角色卡注册新的小机账号并保存独立 Operit 会话；不会生成或改动 MCP Token。", "en": "Register a machine for this character card without changing MCP tokens."},
      "parameters": [
        {"name": "username", "description": {"zh": "小机用户名", "en": "Machine username"}, "type": "string", "required": true},
        {"name": "password", "description": {"zh": "小机密码", "en": "Machine password"}, "type": "string", "required": true},
        {"name": "avatar", "description": {"zh": "可选 Emoji 头像", "en": "Optional emoji avatar"}, "type": "string", "required": false},
        {"name": "human_token", "description": {"zh": "可选：已登录人类 Token；只在 confirm_binding=true 时用于直绑", "en": "Optional logged-in human token, used only with confirm_binding=true"}, "type": "string", "required": false},
        {"name": "confirm_binding", "description": {"zh": "明确确认把新小机绑定到该人类", "en": "Explicitly confirm direct binding"}, "type": "boolean", "required": false}
      ]
    },
    {
      "name": "session_login",
      "description": {"zh": "当前角色卡登录已有小机并保存独立 Operit 会话；不会轮换或撤销 MCP Token。", "en": "Sign this card into an existing machine without rotating or revoking MCP tokens."},
      "parameters": [
        {"name": "username", "description": {"zh": "小机用户名", "en": "Machine username"}, "type": "string", "required": true},
        {"name": "password", "description": {"zh": "小机密码", "en": "Machine password"}, "type": "string", "required": true},
        {"name": "human_token", "description": {"zh": "可选：已登录人类 Token；只在 confirm_binding=true 时用于直绑", "en": "Optional logged-in human token, used only with confirm_binding=true"}, "type": "string", "required": false},
        {"name": "confirm_binding", "description": {"zh": "明确确认把小机绑定到该人类", "en": "Explicitly confirm direct binding"}, "type": "boolean", "required": false}
      ]
    },
    {"name": "session_status", "description": {"zh": "查看当前角色卡的 Operit 小机会话。", "en": "Inspect this card's machine session."}, "parameters": []},
    {"name": "session_logout", "description": {"zh": "只注销当前角色卡的 Operit 会话，不影响 MCP Token。", "en": "Revoke only this card's Operit session."}, "parameters": []},
    {
      "name": "human_login",
      "description": {"zh": "供双弈页面登录现有 CedarToy 人类账号，并在插件配置中保存人类会话。", "en": "Sign the Duel page into an existing CedarToy human account and save the session in plugin config."},
      "parameters": [
        {"name": "username", "description": {"zh": "人类账号用户名", "en": "Human account username"}, "type": "string", "required": true},
        {"name": "password", "description": {"zh": "人类账号密码；仅用于本次登录，不会保存", "en": "Human account password; used only for this request and never saved"}, "type": "string", "required": true}
      ]
    },
    {
      "name": "human_register",
      "description": {"zh": "供双弈页面按 CedarToy 既有规则注册人类账号，并在插件配置中保存人类会话。", "en": "Register a CedarToy human account under the existing rules and save the session in plugin config."},
      "parameters": [
        {"name": "username", "description": {"zh": "新的人类账号用户名", "en": "New human account username"}, "type": "string", "required": true},
        {"name": "password", "description": {"zh": "新的人类账号密码；仅用于本次注册，不会保存", "en": "New human account password; used only for this request and never saved"}, "type": "string", "required": true}
      ]
    },
    {"name": "human_session_status", "description": {"zh": "供双弈页面恢复并校验插件中保存的人类登录会话。", "en": "Restore and verify the saved human login session for the Duel page."}, "parameters": []},
    {"name": "human_logout", "description": {"zh": "清除插件中保存的人类登录会话；不改变服务端既有登录语义。", "en": "Clear the saved human login session without changing existing server login semantics."}, "parameters": []},
    {"name": "human_duel_entry", "description": {"zh": "供已登录人类从页面进入双弈；点击进入即确认本次网页登录。", "en": "Open Duel for the signed-in human; clicking enter confirms this web sign-in."}, "parameters": []},
    {
      "name": "bind_human",
      "description": {"zh": "由已登录人类明确确认，把当前角色卡的小机直接绑定到自己。", "en": "Directly bind this card's machine after explicit confirmation by a logged-in human."},
      "parameters": [
        {"name": "human_token", "description": {"zh": "已登录人类 Token", "en": "Logged-in human token"}, "type": "string", "required": true},
        {"name": "confirm", "description": {"zh": "必须明确为 true", "en": "Must explicitly be true"}, "type": "boolean", "required": true}
      ]
    },
    {
      "name": "duel_web_ticket",
      "description": {"zh": "由已登录人类明确确认，取得 60 秒单次双弈 WebView URL；长期 Token 不进入 URL。", "en": "Create a 60-second one-time Duel WebView URL after explicit human confirmation."},
      "parameters": [
        {"name": "human_token", "description": {"zh": "已登录人类 Token", "en": "Logged-in human token"}, "type": "string", "required": true},
        {"name": "confirm", "description": {"zh": "必须明确为 true", "en": "Must explicitly be true"}, "type": "boolean", "required": true}
      ]
    },
    {
      "name": "rooms",
      "description": {"zh": "列出当前小机可见的双弈房间。", "en": "List Duel rooms visible to this machine."},
      "parameters": [
        {"name": "include_terminal", "description": {"zh": "是否含已结束房间", "en": "Include terminal rooms"}, "type": "boolean", "required": false},
        {"name": "limit", "description": {"zh": "条数", "en": "Page size"}, "type": "number", "required": false},
        {"name": "offset", "description": {"zh": "偏移", "en": "Offset"}, "type": "number", "required": false}
      ]
    },
    {
      "name": "new",
      "description": {"zh": "新建双弈房间；默认在同一次工具调用里继续状态挂等，不会重复新建。", "en": "Create a Duel room and continue waiting without recreating it."},
      "parameters": [
        {"name": "game_type", "description": {"zh": "游戏类型", "en": "Game type"}, "type": "string", "required": true},
        {"name": "mode", "description": {"zh": "房间模式", "en": "Room mode"}, "type": "string", "required": false},
        {"name": "stake", "description": {"zh": "筹码", "en": "Stake"}, "type": "number", "required": false},
        {"name": "target_player_count", "description": {"zh": "目标玩家数", "en": "Target players"}, "type": "number", "required": false},
        {"name": "fill_with_npcs", "description": {"zh": "是否 NPC 补位", "en": "Fill with NPCs"}, "type": "boolean", "required": false},
        {"name": "max_wait_seconds", "description": {"zh": "请求内续等上限，默认 600；0 为不续等", "en": "Continuation limit, default 600; 0 disables"}, "type": "number", "required": false}
      ]
    },
    {
      "name": "join",
      "description": {"zh": "加入房间并可请求内续等；加入请求只发送一次。", "en": "Join once, then optionally continue waiting."},
      "parameters": [
        {"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true},
        {"name": "message", "description": {"zh": "首次加入消息", "en": "Initial join message"}, "type": "string", "required": false},
        {"name": "max_wait_seconds", "description": {"zh": "续等上限，默认 600", "en": "Continuation limit, default 600"}, "type": "number", "required": false}
      ]
    },
    {"name": "accept", "description": {"zh": "接受邀请，动作只发送一次，随后仅 state 挂等。", "en": "Accept once, then wait using state only."}, "parameters": [{"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true}, {"name": "max_wait_seconds", "description": {"zh": "续等上限，默认 600", "en": "Continuation limit"}, "type": "number", "required": false}]},
    {"name": "reject", "description": {"zh": "拒绝邀请。", "en": "Reject an invitation."}, "parameters": [{"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true}]},
    {
      "name": "state",
      "description": {"zh": "读取服务端按当前小机隐私视角裁剪的房间状态。", "en": "Read the server-filtered room state for this machine."},
      "parameters": [
        {"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true},
        {"name": "wait", "description": {"zh": "是否等待状态变化", "en": "Wait for a state change"}, "type": "boolean", "required": false},
        {"name": "full_state", "description": {"zh": "请求完整公开状态（仍由服务端裁剪隐私）", "en": "Request full public state; privacy remains server-filtered"}, "type": "boolean", "required": false},
        {"name": "message", "description": {"zh": "本次状态请求附带消息", "en": "Message on the initial state request"}, "type": "string", "required": false},
        {"name": "max_wait_seconds", "description": {"zh": "wait=true 时续等上限，默认 600", "en": "Continuation limit when wait=true"}, "type": "number", "required": false}
      ]
    },
    {
      "name": "move",
      "description": {"zh": "按最新 revision 执行一次动作；任何后续挂等只发 state，绝不重放 move/message。", "en": "Execute one revisioned move; continuations use state and never replay move/message."},
      "parameters": [
        {"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true},
        {"name": "move", "description": {"zh": "服务端最近发布的完整合法动作对象", "en": "Complete authoritative move object"}, "type": "object", "required": true},
        {"name": "revision", "description": {"zh": "最近状态 revision", "en": "Latest state revision"}, "type": "number", "required": true},
        {"name": "wait", "description": {"zh": "动作后是否挂等对方", "en": "Wait after the move"}, "type": "boolean", "required": false},
        {"name": "message", "description": {"zh": "随本次动作发送一次的消息", "en": "Message sent once with this move"}, "type": "string", "required": false},
        {"name": "max_wait_seconds", "description": {"zh": "wait=true 时续等上限，默认 600", "en": "Continuation limit when wait=true"}, "type": "number", "required": false}
      ]
    },
    {"name": "resign", "description": {"zh": "认输，只发送一次。", "en": "Resign once."}, "parameters": [{"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true}, {"name": "message", "description": {"zh": "消息", "en": "Message"}, "type": "string", "required": false}]},
    {"name": "leave", "description": {"zh": "离开房间，只发送一次。", "en": "Leave once."}, "parameters": [{"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true}, {"name": "message", "description": {"zh": "消息", "en": "Message"}, "type": "string", "required": false}]},
    {"name": "rematch", "description": {"zh": "发起再来一局，动作只发送一次，随后仅 state 挂等。", "en": "Request a rematch once, then wait using state only."}, "parameters": [{"name": "room_id", "description": {"zh": "房间 ID", "en": "Room ID"}, "type": "string", "required": true}, {"name": "max_wait_seconds", "description": {"zh": "续等上限，默认 600", "en": "Continuation limit"}, "type": "number", "required": false}]},
    {
      "name": "chips",
      "description": {"zh": "操作当前小机及其绑定人类的筹码数据；复杂 loans/exchange 字段可放 params_json。", "en": "Operate chip data for this machine and its bound human; use params_json for complex loan/exchange fields."},
      "parameters": [
        {"name": "op", "description": {"zh": "status/check_in/bankruptcy/ledger/achievements/loans/exchange", "en": "Chip operation"}, "type": "string", "required": false},
        {"name": "params_json", "description": {"zh": "可选附加参数 JSON 对象或 JSON 字符串", "en": "Optional extra parameters object or JSON string"}, "type": "string", "required": false}
      ]
    }
  ]
}
*/

const PACKAGE_ID = "org.cedarstar.cedarduet";
const DEFAULT_BASE_URL = "https://toy.cedarstar.org";
const MUTATING_ACTIONS = {
  new: true,
  join: true,
  accept: true,
  reject: true,
  move: true,
  resign: true,
  leave: true,
  rematch: true,
  chips: true,
};
const AUTO_WAIT_ACTIONS = { new: true, join: true, accept: true, rematch: true };
const LOCAL_FIELDS = {
  max_wait_seconds: true,
  params_json: true,
  username: true,
  password: true,
  avatar: true,
  human_token: true,
  confirm: true,
  confirm_binding: true,
};

function callerContext() {
  // Read all three on every tool call. callerCardId is the security/storage key;
  // name/chat are context only and never become a CedarToy player id.
  const rawCardId = typeof getCallerCardId === "function" ? getCallerCardId() : "";
  const rawName = typeof getCallerName === "function" ? getCallerName() : "";
  const rawChatId = typeof getChatId === "function" ? getChatId() : "";
  const clientId = rawCardId === null || rawCardId === undefined ? "" : String(rawCardId).trim();
  if (!clientId) throw new Error("当前执行上下文没有 callerCardId，不能选择小机身份");
  return {
    client_id: clientId,
    client_name: rawName === null || rawName === undefined ? "" : String(rawName),
    chat_id: rawChatId === null || rawChatId === undefined ? "" : String(rawChatId),
  };
}

function fingerprint(value) {
  const seeds = [0x811c9dc5, 0x9e3779b9, 0x85ebca6b, 0xc2b2ae35];
  return seeds.map(function (seed) {
    let hash = seed >>> 0;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return ("00000000" + hash.toString(16)).slice(-8);
  }).join("");
}

function baseUrl() {
  let configured = "";
  if (typeof getEnv === "function") configured = String(getEnv("CEDARTOY_BASE_URL") || "");
  const normalized = (configured.trim() || DEFAULT_BASE_URL).replace(/\/+$/, "");
  if (!/^https:\/\//i.test(normalized) && !/^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(normalized)) {
    throw new Error("CEDARTOY_BASE_URL 必须使用 HTTPS（本机测试地址除外）");
  }
  return normalized;
}

async function configDir() {
  let directory;
  if (typeof NativeInterface !== "undefined" && typeof NativeInterface.getPluginConfigDir === "function") {
    directory = NativeInterface.getPluginConfigDir(PACKAGE_ID);
  } else if (typeof ToolPkg !== "undefined" && typeof ToolPkg.getConfigDir === "function") {
    // Compatibility for test/dev hosts that expose the same package-scoped path
    // through ToolPkg instead of the documented NativeInterface bridge.
    directory = await Promise.resolve(ToolPkg.getConfigDir(PACKAGE_ID));
  } else {
    throw new Error("当前 Operit 版本不支持 ToolPkg 持久配置目录");
  }
  await Tools.Files.mkdir(String(directory), true, "android");
  return String(directory).replace(/\/+$/, "");
}

async function sessionPath(context) {
  return (await configDir()) + "/session-" + fingerprint(context.client_id) + ".json";
}

async function humanSessionPath() {
  return (await configDir()) + "/human-session.json";
}

async function loadSession(context) {
  const path = await sessionPath(context);
  const exists = await Tools.Files.exists(path, "android");
  if (!exists || exists.exists !== true) {
    throw new Error("当前角色卡尚未登录 CedarToy 小机，请先调用 session_login 或 session_register");
  }
  const file = await Tools.Files.read({ path: path, environment: "android", intent: "读取当前角色卡的 CedarToy 会话" });
  let stored;
  try {
    stored = JSON.parse(file.content);
  } catch (_) {
    throw new Error("当前角色卡的本地会话文件已损坏，请重新登录");
  }
  if (!stored || stored.client_id !== context.client_id || typeof stored.session_token !== "string") {
    throw new Error("本地会话与当前 callerCardId 不匹配，请重新登录");
  }
  return { path: path, stored: stored };
}

async function saveSession(context, response) {
  const path = await sessionPath(context);
  const stored = {
    version: 1,
    client_id: context.client_id,
    username: response.user && response.user.username ? response.user.username : "",
    user_id: response.user && response.user.id,
    expires_at_epoch: response.expires_at_epoch,
    session_token: response.session_token,
  };
  await Tools.Files.write(path, JSON.stringify(stored), false, "android");
  return stored;
}

async function loadHumanSession() {
  const path = await humanSessionPath();
  const exists = await Tools.Files.exists(path, "android");
  if (!exists || exists.exists !== true) return { path: path, stored: null };
  const file = await Tools.Files.read({
    path: path,
    environment: "android",
    intent: "读取双弈页面的人类登录会话",
  });
  let stored;
  try {
    stored = JSON.parse(file.content);
  } catch (_) {
    throw new Error("本地登录信息已损坏，请退出后重新登录");
  }
  if (
    !stored
    || stored.version !== 1
    || typeof stored.human_token !== "string"
    || !stored.human_token
    || typeof stored.username !== "string"
    || !stored.username
  ) {
    throw new Error("本地登录信息已损坏，请退出后重新登录");
  }
  return { path: path, stored: stored };
}

async function saveHumanSession(user, humanToken) {
  if (!user || user.is_ai === true || !user.username || !humanToken) {
    throw new Error("CedarToy 未返回有效的人类账号信息");
  }
  const path = await humanSessionPath();
  const stored = {
    version: 1,
    user_id: user.id,
    username: String(user.username),
    human_token: String(humanToken),
  };
  // Passwords are deliberately absent. Only the server-issued human session
  // and the minimum account identity required by the UI are persisted.
  await Tools.Files.write(path, JSON.stringify(stored), false, "android");
  return stored;
}

async function clearHumanSession() {
  const path = await humanSessionPath();
  const exists = await Tools.Files.exists(path, "android");
  if (exists && exists.exists === true) {
    await Tools.Files.deleteFile(path, false, "android");
  }
}

function responseJson(response) {
  if (!response) throw new Error("CedarToy 未返回响应");
  let payload = response.content;
  if (typeof payload === "string") {
    try {
      payload = JSON.parse(payload);
    } catch (_) {
      throw new Error("CedarToy 返回了非 JSON 响应（HTTP " + response.statusCode + "）");
    }
  }
  if (!payload || typeof payload !== "object") throw new Error("CedarToy 返回格式异常");
  if (response.statusCode < 200 || response.statusCode >= 300) {
    const message = payload.error || payload.message || ("HTTP " + response.statusCode);
    const error = new Error(String(message));
    error.statusCode = response.statusCode;
    throw error;
  }
  return payload;
}

async function postJson(path, body, bearer) {
  const headers = { "Content-Type": "application/json", "Accept": "application/json" };
  if (bearer) headers.Authorization = "Bearer " + bearer;
  const response = await Tools.Net.http({
    url: baseUrl() + path,
    method: "POST",
    headers: headers,
    body: JSON.stringify(body),
    connect_timeout: 15000,
    read_timeout: 65000,
    follow_redirects: false,
    responseType: "text",
    validateStatus: false,
  });
  return responseJson(response);
}

async function getJson(path, bearer) {
  const headers = { "Accept": "application/json" };
  if (bearer) headers.Authorization = "Bearer " + bearer;
  const response = await Tools.Net.http({
    url: baseUrl() + path,
    method: "GET",
    headers: headers,
    connect_timeout: 15000,
    read_timeout: 65000,
    follow_redirects: false,
    responseType: "text",
    validateStatus: false,
  });
  return responseJson(response);
}

async function createSession(action, params) {
  const context = callerContext();
  const wantsBinding = params && params.confirm_binding === true;
  if ((params && params.human_token) && !wantsBinding) {
    throw new Error("提供 human_token 时必须明确 confirm_binding=true");
  }
  if (wantsBinding && !(params && String(params.human_token || "").trim())) {
    throw new Error("confirm_binding=true 时必须提供已登录人类 Token");
  }
  const body = {
    action: action,
    username: params && params.username,
    password: params && params.password,
    avatar: params && params.avatar,
    client_id: context.client_id,
    client_name: context.client_name,
    chat_id: context.chat_id,
    bind_to_human: wantsBinding,
    confirm_binding: wantsBinding,
  };
  const response = await postJson(
    "/api/operit/session", body,
    wantsBinding ? String(params.human_token).trim() : ""
  );
  if (typeof response.session_token !== "string" || !response.session_token) {
    throw new Error("CedarToy 未签发 Operit 会话");
  }
  const stored = await saveSession(context, response);
  return {
    success: true,
    message: response.message || "Operit 小机会话已保存",
    data: {
      user: response.user,
      bound: response.bound === true,
      expires_at_epoch: stored.expires_at_epoch,
      caller_card_id: context.client_id,
    },
  };
}

async function sessionStatus() {
  const context = callerContext();
  const loaded = await loadSession(context);
  const response = await postJson(
    "/api/operit/session",
    { action: "status", client_id: context.client_id, client_name: context.client_name, chat_id: context.chat_id },
    loaded.stored.session_token
  );
  return {
    success: true,
    message: "当前角色卡已登录 " + response.user.username,
    data: {
      user: response.user,
      expires_at_epoch: response.expires_at_epoch,
      caller_card_id: context.client_id,
    },
  };
}

async function sessionLogout() {
  const context = callerContext();
  const loaded = await loadSession(context);
  let remoteError = null;
  try {
    await postJson(
      "/api/operit/session",
      { action: "logout", client_id: context.client_id, client_name: context.client_name, chat_id: context.chat_id },
      loaded.stored.session_token
    );
  } catch (cause) {
    // A definite 401 means the server session is already unusable, so local
    // cleanup is safe. For an ambiguous transport/5xx failure, retain the file
    // so the user can retry revocation instead of orphaning a live credential.
    if (!cause || cause.statusCode !== 401) remoteError = cause;
  }
  if (remoteError) throw remoteError;
  await Tools.Files.deleteFile(loaded.path, false, "android");
  return { success: true, message: "当前角色卡的 Operit 会话已注销；MCP Token 未受影响。" };
}

async function humanLoginOrRegister(action, params) {
  const rawUsername = params && params.username;
  const username = typeof rawUsername === "string" ? rawUsername.trim() : rawUsername;
  const password = params && params.password;
  const endpoint = action === "register" ? "/api/auth/register" : "/api/auth/login";
  const response = await postJson(endpoint, { username: username, password: password }, "");
  if (!response.user || response.user.is_ai === true || !response.token) {
    throw new Error("登录失败，请稍后重试");
  }
  await saveHumanSession(response.user, response.token);
  const successMessage = action === "register" ? "注册并登录成功" : "登录成功";
  return {
    success: true,
    message: response.message ? successMessage + "。" + response.message : successMessage,
    data: {
      logged_in: true,
      user: response.user,
      pending_deletion: response.pending_deletion === true,
    },
  };
}

async function humanSessionStatus() {
  const loaded = await loadHumanSession();
  if (!loaded.stored) {
    return {
      success: true,
      message: "当前未登录",
      data: { logged_in: false },
    };
  }
  let response;
  try {
    response = await getJson("/api/auth/me", loaded.stored.human_token);
  } catch (cause) {
    if (cause && cause.statusCode === 401) {
      await clearHumanSession();
      return {
        success: true,
        message: "登录已失效，请重新登录",
        data: { logged_in: false, expired: true },
      };
    }
    throw cause;
  }
  if (!response.user || response.user.is_ai === true) {
    await clearHumanSession();
    throw new Error("保存的账号不是人类账号，请重新登录");
  }
  await saveHumanSession(response.user, loaded.stored.human_token);
  return {
    success: true,
    message: "登录状态正常",
    data: {
      logged_in: true,
      user: response.user,
      pending_deletion: response.pending_deletion === true,
    },
  };
}

async function humanLogout() {
  // Human JWTs have no server-side revoke endpoint. Logging out therefore
  // clears only the ToolPkg copy and deliberately makes no network request.
  await clearHumanSession();
  return { success: true, message: "已退出登录", data: { logged_in: false } };
}

async function bindHuman(params) {
  const context = callerContext();
  if (!params || params.confirm !== true) throw new Error("bind_human 必须明确 confirm=true");
  const humanToken = String(params.human_token || "").trim();
  if (!humanToken) throw new Error("human_token 必填");
  const loaded = await loadSession(context);
  const response = await postJson(
    "/api/operit/bind",
    {
      session_token: loaded.stored.session_token,
      client_id: context.client_id,
      client_name: context.client_name,
      chat_id: context.chat_id,
      confirm: true,
    },
    humanToken
  );
  return { success: true, message: "绑定已确认", data: response };
}

async function duelWebTicket(params) {
  const context = callerContext();
  if (!params || params.confirm !== true) throw new Error("duel_web_ticket 必须明确 confirm=true");
  const humanToken = String(params.human_token || "").trim();
  if (!humanToken) throw new Error("human_token 必填");
  return issueDuelWebTicket(humanToken, context, {
    successMessage: "单次网页登录票据已创建，请立即打开",
  });
}

async function issueDuelWebTicket(humanToken, context, options) {
  const response = await postJson(
    "/api/operit/web-ticket",
    { confirm: true, client_id: context.client_id, client_name: context.client_name, chat_id: context.chat_id },
    humanToken
  );
  if (!response.ticket_path || String(response.ticket_path).indexOf("web_ticket=") < 0) {
    throw new Error("CedarToy 未返回有效 Web ticket URL");
  }
  const webUrl = baseUrl() + response.ticket_path;
  if (webUrl.indexOf(humanToken) >= 0) {
    throw new Error("CedarToy 返回的网页登录地址不安全");
  }
  return {
    success: true,
    message: options && options.successMessage
      ? options.successMessage
      : "双弈已准备好",
    data: {
      web_url: webUrl,
      expires_in: response.expires_in,
    },
  };
}

async function humanDuelEntry() {
  const loaded = await loadHumanSession();
  if (!loaded.stored) throw new Error("请先登录 CedarToy 人类账号");
  try {
    return await issueDuelWebTicket(
      loaded.stored.human_token,
      callerContext(),
      { successMessage: "双弈已准备好" }
    );
  } catch (cause) {
    if (cause && cause.statusCode === 401) {
      await clearHumanSession();
      throw new Error("登录已失效，请重新登录");
    }
    throw cause;
  }
}

function extraParams(value) {
  if (value === undefined || value === null || value === "") return {};
  let parsed = value;
  if (typeof parsed === "string") parsed = JSON.parse(parsed);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("params_json 必须是 JSON 对象");
  }
  return Object.assign({}, parsed);
}

function duelParams(input) {
  const result = extraParams(input && input.params_json);
  Object.keys(input || {}).forEach(function (key) {
    if (!LOCAL_FIELDS[key] && input[key] !== undefined && input[key] !== null) {
      result[key] = input[key];
    }
  });
  return result;
}

async function duelOnce(action, params, context, token) {
  return postJson(
    "/api/operit/duel",
    {
      action: action,
      params: params,
      client_id: context.client_id,
      client_name: context.client_name,
      chat_id: context.chat_id,
    },
    token
  );
}

function roomIdOf(result) {
  if (result && result.room_id) return String(result.room_id);
  if (result && result.room && result.room.room_id) return String(result.room.room_id);
  return "";
}

function needsContinuation(result, initialAction) {
  if (!roomIdOf(result)) return false;
  if (result && result.next_call && result.next_call.action === "state") return true;
  if (AUTO_WAIT_ACTIONS[initialAction] && (result.status === "pending" || result.status === "waiting")) return true;
  if (result.status === "still_waiting" || result.wait_downgraded === true) return true;
  return result.status === "playing" && result.your_turn === false;
}

function maxWaitSeconds(action, input, params) {
  let enabled = AUTO_WAIT_ACTIONS[action] === true;
  if ((action === "move" || action === "state") && params.wait === true) enabled = true;
  if (!enabled) return 0;
  const raw = input && input.max_wait_seconds !== undefined ? Number(input.max_wait_seconds) : 600;
  if (!Number.isFinite(raw)) return 600;
  return Math.max(0, Math.min(1200, Math.floor(raw)));
}

async function invokeDuel(action, input) {
  const context = callerContext();
  const loaded = await loadSession(context);
  const initialParams = duelParams(input || {});
  const waitLimit = maxWaitSeconds(action, input || {}, initialParams);
  const startedAt = Date.now();
  let heartbeats = 0;
  let result;
  try {
    // The mutating request is made exactly once. It is never retried here, even
    // after an ambiguous network failure.
    result = await duelOnce(action, initialParams, context, loaded.stored.session_token);
  } catch (cause) {
    if (MUTATING_ACTIONS[action]) cause.mayHaveExecuted = true;
    throw cause;
  }
  const maxHeartbeats = Math.min(48, Math.max(1, Math.ceil(waitLimit / 25)));
  while (
    waitLimit > 0
    && heartbeats < maxHeartbeats
    && Date.now() - startedAt < waitLimit * 1000
    && needsContinuation(result, action)
  ) {
    const roomId = roomIdOf(result);
    // Continuations intentionally contain only room_id + state + wait. In
    // particular, move and message can never be replayed by this loop.
    result = await duelOnce(
      "state", { room_id: roomId, wait: true }, context, loaded.stored.session_token
    );
    heartbeats += 1;
  }
  return {
    success: true,
    message: heartbeats ? ("双弈响应完成（续等 " + heartbeats + " 次）") : "双弈响应完成",
    data: {
      action: action,
      result: result,
      wait_heartbeats: heartbeats,
      wait_exhausted: waitLimit > 0 && needsContinuation(result, action),
      caller_card_id: context.client_id,
    },
  };
}

async function runAndComplete(operation, params, options) {
  try {
    complete(await operation(params || {}));
  } catch (cause) {
    const message = cause && cause.message ? cause.message : String(cause);
    const ambiguous = cause && cause.mayHaveExecuted === true;
    complete({
      success: false,
      message: ambiguous
        ? message + "；网络结果不明确，原动作可能已执行，请先 state/rooms 核对，不要盲目重放。"
        : message,
      data: ambiguous ? { may_have_executed: true } : undefined,
    });
  }
}

function duelTool(action) {
  return function (params) {
    return runAndComplete(function (value) { return invokeDuel(action, value); }, params);
  };
}

exports.session_register = function (params) {
  return runAndComplete(function (value) { return createSession("register", value); }, params);
};
exports.session_login = function (params) {
  return runAndComplete(function (value) { return createSession("login", value); }, params);
};
exports.session_status = function (params) { return runAndComplete(sessionStatus, params); };
exports.session_logout = function (params) { return runAndComplete(sessionLogout, params); };
exports.human_login = function (params) {
  return runAndComplete(function (value) { return humanLoginOrRegister("login", value); }, params);
};
exports.human_register = function (params) {
  return runAndComplete(function (value) { return humanLoginOrRegister("register", value); }, params);
};
exports.human_session_status = function (params) { return runAndComplete(humanSessionStatus, params); };
exports.human_logout = function (params) { return runAndComplete(humanLogout, params); };
exports.human_duel_entry = function (params) { return runAndComplete(humanDuelEntry, params); };
exports.bind_human = function (params) { return runAndComplete(bindHuman, params); };
exports.duel_web_ticket = function (params) { return runAndComplete(duelWebTicket, params); };
exports.rooms = duelTool("rooms");
exports.new = duelTool("new");
exports.join = duelTool("join");
exports.accept = duelTool("accept");
exports.reject = duelTool("reject");
exports.state = duelTool("state");
exports.move = duelTool("move");
exports.resign = duelTool("resign");
exports.leave = duelTool("leave");
exports.rematch = duelTool("rematch");
exports.chips = duelTool("chips");

// Non-tool test surface. It is not declared in METADATA and is not registered.
exports.__test = {
  callerContext: callerContext,
  fingerprint: fingerprint,
  needsContinuation: needsContinuation,
  invokeDuel: invokeDuel,
};
