"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const uiPath = path.resolve(__dirname, "../ui/duel/index.ui.js");
const uiSource = fs.readFileSync(uiPath, "utf8");
const Screen = require(uiPath).default;

function createHarness(handler) {
  const state = new Map();
  const refs = new Map();
  const calls = [];
  const errors = [];
  const UI = {};
  [
    "LazyColumn", "Column", "Row", "Text", "TextField", "Button", "WebView",
  ].forEach(function (type) {
    UI[type] = function (props, children) {
      return {
        type: type,
        props: props || {},
        children: (Array.isArray(children) ? children : children ? [children] : [])
          .filter(Boolean),
      };
    };
  });
  const ctx = {
    UI: UI,
    useState: function (key, initialValue) {
      if (!state.has(key)) state.set(key, initialValue);
      return [state.get(key), function (value) { state.set(key, value); }];
    },
    useRef: function (key, initialValue) {
      if (!refs.has(key)) refs.set(key, { current: initialValue });
      return refs.get(key);
    },
    callTool: async function (toolName, params) {
      calls.push({ toolName: toolName, params: params });
      return handler(toolName, params, calls);
    },
    reportError: function (error) { errors.push(error); },
  };
  return {
    calls: calls,
    errors: errors,
    state: state,
    render: function () { return Screen(ctx); },
  };
}

function flatten(node) {
  if (!node) return [];
  if (Array.isArray(node)) {
    return node.reduce(function (all, item) { return all.concat(flatten(item)); }, []);
  }
  return [node].concat(flatten(node.children || []));
}

function nodeBy(tree, type, predicate) {
  return flatten(tree).find(function (node) {
    return node.type === type && (!predicate || predicate(node.props));
  });
}

function button(tree, text) {
  const found = nodeBy(tree, "Button", function (props) { return props.text === text; });
  assert.ok(found, "missing button: " + text);
  return found;
}

async function loadLoggedOut(harness) {
  let tree = harness.render();
  assert.strictEqual(tree.type, "LazyColumn");
  assert.strictEqual(typeof tree.props.onLoad, "function");
  await tree.props.onLoad();
  tree = harness.render();
  return tree;
}

function fillCredentials(harness, tree, username, password) {
  const usernameField = nodeBy(tree, "TextField", function (props) {
    return props.label === "用户名";
  });
  const passwordField = nodeBy(tree, "TextField", function (props) {
    return props.label === "密码";
  });
  assert.ok(usernameField);
  assert.ok(passwordField);
  assert.strictEqual(usernameField.props.fillMaxWidth, true);
  assert.strictEqual(passwordField.props.fillMaxWidth, true);
  assert.strictEqual(passwordField.props.isPassword, true);
  usernameField.props.onValueChange(username);
  passwordField.props.onValueChange(password);
  return harness.render();
}

async function testLoginUi() {
  const harness = createHarness(async function (toolName, params) {
    if (toolName === "cedarduet:human_session_status") {
      return { success: true, data: { logged_in: false } };
    }
    if (toolName === "cedarduet:human_login") {
      assert.deepStrictEqual(params, { username: "HumanAlice", password: "human-pass" });
      return {
        success: true,
        message: "登录成功",
        data: { logged_in: true, user: { id: 101, username: "HumanAlice", is_ai: false } },
      };
    }
    throw new Error("unexpected tool: " + toolName);
  });
  let tree = await loadLoggedOut(harness);
  tree = fillCredentials(harness, tree, "HumanAlice", "human-pass");
  await button(tree, "登录").props.onClick();
  assert.strictEqual(harness.state.get("password"), "", "password must be cleared after login");
  tree = harness.render();
  assert.ok(nodeBy(tree, "Text", function (props) {
    return props.text === "当前账号：HumanAlice";
  }));
  assert.ok(button(tree, "进入双弈"));
  assert.ok(button(tree, "退出登录"));
}

async function testRegisterUi() {
  const harness = createHarness(async function (toolName, params) {
    if (toolName === "cedarduet:human_session_status") {
      return { success: true, data: { logged_in: false } };
    }
    if (toolName === "cedarduet:human_register") {
      assert.deepStrictEqual(params, { username: "NewHuman", password: "new-human-pass" });
      return {
        success: true,
        message: "注册并登录成功",
        data: { logged_in: true, user: { id: 202, username: "NewHuman", is_ai: false } },
      };
    }
    throw new Error("unexpected tool: " + toolName);
  });
  let tree = await loadLoggedOut(harness);
  tree = fillCredentials(harness, tree, "NewHuman", "new-human-pass");
  await button(tree, "注册").props.onClick();
  assert.strictEqual(harness.state.get("password"), "", "password must be cleared after register");
  tree = harness.render();
  assert.ok(nodeBy(tree, "Text", function (props) {
    return props.text === "当前账号：NewHuman";
  }));
}

async function testRestoredSessionAndLogoutUi() {
  const harness = createHarness(async function (toolName, params) {
    assert.deepStrictEqual(params, {});
    if (toolName === "cedarduet:human_session_status") {
      return {
        success: true,
        data: { logged_in: true, user: { id: 303, username: "SavedHuman", is_ai: false } },
      };
    }
    if (toolName === "cedarduet:human_logout") {
      return { success: true, message: "已退出登录", data: { logged_in: false } };
    }
    throw new Error("unexpected tool: " + toolName);
  });
  let tree = harness.render();
  await tree.props.onLoad();
  tree = harness.render();
  assert.ok(nodeBy(tree, "Text", function (props) {
    return props.text === "当前账号：SavedHuman";
  }));
  await button(tree, "退出登录").props.onClick();
  tree = harness.render();
  assert.ok(nodeBy(tree, "TextField", function (props) { return props.label === "用户名"; }));
  assert.deepStrictEqual(harness.calls.map(function (call) { return call.toolName; }), [
    "cedarduet:human_session_status",
    "cedarduet:human_logout",
  ]);
}

async function testEnterDuelUi() {
  const longLivedSecret = "this-must-never-enter-the-webview-url";
  const harness = createHarness(async function (toolName, params) {
    assert.deepStrictEqual(params, {});
    if (toolName === "cedarduet:human_session_status") {
      return {
        success: true,
        data: { logged_in: true, user: { id: 404, username: "PlayerFour", is_ai: false } },
      };
    }
    if (toolName === "cedarduet:human_duel_entry") {
      return {
        success: true,
        data: { web_url: "https://toy.example.test/duel/?web_ticket=short-one-use" },
      };
    }
    throw new Error("unexpected tool: " + toolName);
  });
  let tree = harness.render();
  await tree.props.onLoad();
  tree = harness.render();
  await button(tree, "进入双弈").props.onClick();
  tree = harness.render();
  assert.strictEqual(tree.type, "WebView");
  assert.strictEqual(
    tree.props.url,
    "https://toy.example.test/duel/?web_ticket=short-one-use",
  );
  assert.strictEqual(tree.props.url.includes(longLivedSecret), false);
  assert.deepStrictEqual(harness.calls[harness.calls.length - 1], {
    toolName: "cedarduet:human_duel_entry",
    params: {},
  });
}

async function testInternalTermsAreHiddenFromErrors() {
  const harness = createHarness(async function (toolName) {
    if (toolName === "cedarduet:human_session_status") {
      return { success: true, data: { logged_in: false } };
    }
    return { success: false, message: "human_token / ticket / confirm invalid" };
  });
  let tree = await loadLoggedOut(harness);
  tree = fillCredentials(harness, tree, "HumanAlice", "human-pass");
  await button(tree, "登录").props.onClick();
  tree = harness.render();
  const visibleText = flatten(tree)
    .filter(function (node) { return node.type === "Text"; })
    .map(function (node) { return String(node.props.text || ""); })
    .join(" ");
  assert.strictEqual(/token|ticket|confirm/i.test(visibleText), false);
  assert.match(visibleText, /登录失败/);
}

async function main() {
  assert.strictEqual(typeof Screen, "function");
  assert.strictEqual(uiSource.includes("人类 Token"), false);
  assert.strictEqual(/ctx\.UI\.Checkbox/.test(uiSource), false);
  assert.strictEqual(/label:\s*["'].*Token/i.test(uiSource), false);
  await testLoginUi();
  await testRegisterUi();
  await testRestoredSessionAndLogoutUi();
  await testEnterDuelUi();
  await testInternalTermsAreHiddenFromErrors();
  process.stdout.write("Compose UI tests passed\n");
}

main().catch(function (error) {
  process.stderr.write((error && error.stack) || String(error));
  process.stderr.write("\n");
  process.exitCode = 1;
});
