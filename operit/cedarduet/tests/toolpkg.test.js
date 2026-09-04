"use strict";

const assert = require("assert");
const path = require("path");

const memoryFiles = new Map();
const httpCalls = [];
const completed = [];
const callerCounts = { card: 0, name: 0, chat: 0 };
let currentCaller = { card: "card-a", name: "Machine A", chat: "chat-a" };
let httpHandler = null;

global.getCallerCardId = function () { callerCounts.card += 1; return currentCaller.card; };
global.getCallerName = function () { callerCounts.name += 1; return currentCaller.name; };
global.getChatId = function () { callerCounts.chat += 1; return currentCaller.chat; };
global.getEnv = function () { return "https://toy.example.test"; };
global.complete = function (value) { completed.push(value); };
global.ToolPkg = {
  getConfigDir: function () { return "/mock/cedarduet"; },
  registerUiRoute: function () {},
  registerNavigationEntry: function () {},
};
global.Tools = {
  Files: {
    mkdir: async function () { return { successful: true }; },
    exists: async function (filePath) { return { exists: memoryFiles.has(filePath) }; },
    read: async function (options) { return { content: memoryFiles.get(options.path) }; },
    write: async function (filePath, content) {
      memoryFiles.set(filePath, content);
      return { successful: true };
    },
    deleteFile: async function (filePath) {
      memoryFiles.delete(filePath);
      return { successful: true };
    },
  },
  Net: {
    http: async function (options) {
      httpCalls.push(options);
      if (!httpHandler) throw new Error("missing HTTP mock");
      const payload = await httpHandler(options);
      return {
        statusCode: payload.statusCode || 200,
        content: JSON.stringify(payload.body),
      };
    },
  },
};

const modulePath = path.resolve(__dirname, "../packages/cedarduet.js");
const duel = require(modulePath);
const REQUIRED_TOOLS = [
  "session_register", "session_login", "session_status", "session_logout",
  "bind_human", "duel_web_ticket", "rooms", "new", "join", "accept",
  "reject", "state", "move", "resign", "leave", "rematch", "chips",
];

async function invoke(exported, params) {
  completed.length = 0;
  await exported(params || {});
  assert.strictEqual(completed.length, 1);
  return completed[0];
}

async function login(card, userId, username, token) {
  currentCaller = { card: card, name: username, chat: "chat-" + card };
  httpHandler = async function (options) {
    const body = JSON.parse(options.body);
    assert.strictEqual(body.client_id, card);
    return {
      body: {
        session_token: token,
        expires_at_epoch: 2000000000,
        user: { id: userId, username: username, is_ai: true },
        bound: false,
      },
    };
  };
  const result = await invoke(duel.session_login, {
    username: username,
    password: "secret-pass",
  });
  assert.strictEqual(result.success, true);
  assert.strictEqual(JSON.stringify(result).includes(token), false);
}

async function testCardIsolation() {
  await login("card-a", 11, "MachineA", "ctop_v1_token-a");
  await login("card-b", 22, "MachineB", "ctop_v1_token-b");
  assert.strictEqual(memoryFiles.size, 2);

  const observed = [];
  httpHandler = async function (options) {
    const body = JSON.parse(options.body);
    observed.push({
      card: body.client_id,
      auth: options.headers.Authorization,
    });
    return { body: { room_id: "ROOM1234", status: "playing", your_turn: true } };
  };
  currentCaller = { card: "card-a", name: "MachineA", chat: "chat-a" };
  assert.strictEqual((await invoke(duel.state, { room_id: "ROOM1234" })).success, true);
  currentCaller = { card: "card-b", name: "MachineB", chat: "chat-b" };
  assert.strictEqual((await invoke(duel.state, { room_id: "ROOM1234" })).success, true);
  assert.deepStrictEqual(observed, [
    { card: "card-a", auth: "Bearer ctop_v1_token-a" },
    { card: "card-b", auth: "Bearer ctop_v1_token-b" },
  ]);
  assert.ok(callerCounts.card > 0 && callerCounts.name > 0 && callerCounts.chat > 0);
}

async function testMoveIsNeverReplayedByWaitLoop() {
  currentCaller = { card: "card-a", name: "MachineA", chat: "chat-a" };
  const requests = [];
  httpHandler = async function (options) {
    const body = JSON.parse(options.body);
    requests.push(body);
    if (requests.length === 1) {
      return {
        body: {
          room_id: "ROOM1234",
          status: "playing",
          your_turn: false,
          next_call: { action: "state" },
        },
      };
    }
    if (requests.length === 2) {
      return {
        body: {
          room_id: "ROOM1234",
          status: "still_waiting",
          next_call: { action: "state" },
        },
      };
    }
    return {
      body: {
        room_id: "ROOM1234",
        status: "playing",
        your_turn: true,
        private_view: { hand: ["server-filtered"] },
      },
    };
  };
  const result = await invoke(duel.move, {
    room_id: "ROOM1234",
    move: { action: "play", card: "A" },
    revision: 7,
    wait: true,
    message: "sent once",
    max_wait_seconds: 60,
  });

  assert.strictEqual(result.success, true);
  assert.strictEqual(result.data.wait_heartbeats, 2);
  assert.deepStrictEqual(requests.map(function (request) { return request.action; }), [
    "move", "state", "state",
  ]);
  assert.deepStrictEqual(requests[0].params.move, { action: "play", card: "A" });
  assert.strictEqual(requests[0].params.message, "sent once");
  requests.slice(1).forEach(function (request) {
    assert.deepStrictEqual(request.params, { room_id: "ROOM1234", wait: true });
  });
  assert.deepStrictEqual(
    result.data.result.private_view,
    { hand: ["server-filtered"] },
  );
}

async function testAmbiguousLogoutRetainsLocalSessionForRetry() {
  currentCaller = { card: "card-a", name: "MachineA", chat: "chat-a" };
  const before = memoryFiles.size;
  httpHandler = async function () {
    throw new Error("simulated transport failure");
  };

  const result = await invoke(duel.session_logout, {});

  assert.strictEqual(result.success, false);
  assert.strictEqual(memoryFiles.size, before);
}

function testSidebarRegistration() {
  const routes = [];
  const navigation = [];
  global.ToolPkg.registerUiRoute = function (spec) { routes.push(spec); };
  global.ToolPkg.registerNavigationEntry = function (spec) { navigation.push(spec); };
  global.Icons = { SportsEsports: "SportsEsports" };
  const mainPath = path.resolve(__dirname, "../main.js");
  delete require.cache[mainPath];
  const main = require(mainPath);
  assert.strictEqual(main.registerToolPkg(), true);
  assert.strictEqual(routes.length, 1);
  assert.strictEqual(navigation.length, 1);
  assert.strictEqual(routes[0].screen, "ui/duel/index.ui.js");
  assert.strictEqual(navigation[0].surface, "main_sidebar_plugins");
  assert.strictEqual(navigation[0].title.zh, "双弈");
  assert.strictEqual(navigation[0].icon, "SportsEsports");
}

async function main() {
  REQUIRED_TOOLS.forEach(function (name) {
    assert.strictEqual(typeof duel[name], "function", "missing tool export: " + name);
  });
  await testCardIsolation();
  await testMoveIsNeverReplayedByWaitLoop();
  await testAmbiguousLogoutRetainsLocalSessionForRetry();
  testSidebarRegistration();
  process.stdout.write("ToolPkg tests passed\n");
}

main().catch(function (error) {
  process.stderr.write((error && error.stack) || String(error));
  process.stderr.write("\n");
  process.exitCode = 1;
});
