"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {JSDOM} = require("jsdom");

const root = path.resolve(__dirname, "..");
const frontendRoot = path.join(root, "turtle-soup", "frontend");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");

const MAIN_PASSWORD_IDS = [
  "loginPass",
  "loginConfirmPass",
  "forgotNewPassword",
  "forgotConfirmPassword",
  "emailSecurityPassword",
  "machineTokenPass",
  "oldPassword",
  "newPassword",
  "confirmNewPassword",
  "deleteAccountPassword",
  "machineNewPassword",
  "machineConfirmPassword",
  "resetNewPassword",
  "resetConfirmPassword",
];

function response(body, ok = true) {
  return {ok, status: ok ? 200 : 404, json: async () => body};
}

async function checkMainPage() {
  const requests = [];
  const dom = new JSDOM(html, {
    url: "https://toy.cedarstar.org/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = async (url, options = {}) => {
        requests.push({url, options});
        if (url === "/api/rooms/count") return response({count: 0});
        if (url === "/api/platform-stats") return response({});
        if (url === "/api/games/stats") return response({});
        if (url === "/api/announcements") {
          return response({announcements: [], unread_count: 0, authenticated: false});
        }
        return response({}, false);
      };
    },
  });

  try {
    const {window} = dom;
    const document = window.document;
    const byId = (id) => document.getElementById(id);
    await new Promise((resolve) => window.setTimeout(resolve, 30));

    const passwordInputs = MAIN_PASSWORD_IDS.map((id) => byId(id));
    assert.ok(passwordInputs.every(Boolean), "all expected main-page password inputs exist");
    assert.equal(document.querySelectorAll('input[data-password-visibility-ready="true"]').length, 14);
    assert.equal(document.querySelectorAll(".password-visibility-toggle").length, 14);

    for (const [index, input] of passwordInputs.entries()) {
      const toggle = input.parentElement.querySelector(".password-visibility-toggle");
      const value = `keep-${index}`;
      const autocomplete = input.getAttribute("autocomplete");
      input.value = value;
      assert.equal(input.type, "password", `${input.id} starts masked`);
      assert.equal(toggle.getAttribute("aria-label"), "显示密码");
      assert.equal(toggle.getAttribute("aria-pressed"), "false");

      toggle.click();
      assert.equal(input.type, "text", `${input.id} can be revealed`);
      assert.equal(input.value, value, `${input.id} value survives reveal`);
      assert.equal(input.getAttribute("autocomplete"), autocomplete, `${input.id} autocomplete survives reveal`);
      assert.equal(toggle.getAttribute("aria-label"), "隐藏密码");
      assert.equal(toggle.getAttribute("aria-pressed"), "true");

      toggle.click();
      assert.equal(input.type, "password", `${input.id} can be masked again`);
      assert.equal(input.value, value, `${input.id} value survives remasking`);
    }

    byId("oldPassword").parentElement.querySelector(".password-visibility-toggle").click();
    assert.equal(byId("oldPassword").type, "text");
    window.closeModals();
    assert.equal(byId("oldPassword").type, "password", "closing modals restores masking outside the login dialog");

    assert.equal(byId("loginConfirmField").hidden, true, "confirmation is hidden in login mode");
    window.setLoginMode("register");
    assert.equal(byId("loginConfirmField").hidden, false, "confirmation appears in register mode");
    assert.equal(byId("loginPass").autocomplete, "new-password");

    byId("loginUser").value = "TestUser";
    byId("loginPass").value = "secret-one";
    byId("loginConfirmPass").value = "secret-two";
    const registerRequestsBefore = requests.filter(({url}) => url === "/api/auth/register").length;
    await window.login();
    const registerRequestsAfter = requests.filter(({url}) => url === "/api/auth/register").length;
    assert.equal(registerRequestsAfter, registerRequestsBefore, "mismatched passwords do not send a registration request");
    assert.equal(byId("loginMsg").textContent, "两次输入的密码不一致");

    byId("loginConfirmPass").parentElement.querySelector(".password-visibility-toggle").click();
    assert.equal(byId("loginConfirmPass").type, "text");
    window.setLoginMode("login");
    assert.equal(byId("loginConfirmField").hidden, true);
    assert.equal(byId("loginConfirmPass").value, "");
    assert.equal(byId("loginConfirmPass").type, "password");
    assert.equal(byId("loginPass").type, "password");
    assert.equal(byId("loginPass").autocomplete, "current-password");

    window.setLoginMode("register");
    byId("loginConfirmPass").value = "stale-confirmation";
    byId("loginConfirmPass").parentElement.querySelector(".password-visibility-toggle").click();
    window.closeModals();
    assert.equal(byId("loginConfirmField").hidden, true);
    assert.equal(byId("loginConfirmPass").value, "");
    assert.equal(byId("loginConfirmPass").type, "password");
    window.openModal("loginModal");
    assert.equal(byId("loginConfirmField").hidden, true, "confirmation stays reset when the modal reopens");
  } finally {
    dom.window.close();
  }
}

function setReactInput(window, input, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new window.Event("input", {bubbles: true}));
  input.dispatchEvent(new window.Event("change", {bubbles: true}));
}

async function checkTurtleSoup() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: "https://toy.cedarstar.org/soup/",
    pretendToBeVisual: true,
  });
  const previousGlobals = {};
  const globals = ["window", "document", "navigator", "localStorage", "HTMLElement", "Event", "MouseEvent"];
  for (const name of globals) {
    previousGlobals[name] = global[name];
    global[name] = dom.window[name];
  }
  previousGlobals.IS_REACT_ACT_ENVIRONMENT = global.IS_REACT_ACT_ENVIRONMENT;
  previousGlobals.fetch = global.fetch;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const requests = [];
  global.fetch = async (url, options = {}) => {
    requests.push({url, options});
    return response({}, false);
  };

  let vite;
  let reactRoot;
  try {
    const viteUrl = pathToFileURL(path.join(frontendRoot, "node_modules", "vite", "dist", "node", "index.js")).href;
    const {createServer} = await import(viteUrl);
    vite = await createServer({
      root: frontendRoot,
      configFile: path.join(frontendRoot, "vite.config.js"),
      server: {middlewareMode: true},
      appType: "custom",
      logLevel: "error",
      optimizeDeps: {noDiscovery: true, include: []},
    });

    const React = require(path.join(frontendRoot, "node_modules", "react"));
    const {createRoot} = require(path.join(frontendRoot, "node_modules", "react-dom", "client"));
    const {default: LoginModal} = await vite.ssrLoadModule("/src/components/LoginModal.jsx");
    const {default: AccountActionModal} = await vite.ssrLoadModule("/src/components/AccountActionModal.jsx");
    const container = dom.window.document.getElementById("root");
    reactRoot = createRoot(container);
    const render = async (element) => {
      await React.act(async () => {
        reactRoot.render(element);
      });
    };
    const click = async (element) => {
      await React.act(async () => {
        element.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true}));
      });
    };
    const type = async (input, value) => {
      await React.act(async () => setReactInput(dom.window, input, value));
    };

    const loginProps = {open: true, onClose() {}, onSuccess() {}};
    await render(React.createElement(LoginModal, loginProps));
    assert.equal(container.querySelector("#soupLoginConfirmField"), null, "soup confirmation is absent in login mode");
    const authTab = (label) => [...container.querySelectorAll(".auth-mode-tabs button")]
      .find((button) => button.textContent === label);
    const registerTab = authTab("注册");
    await click(registerTab);
    assert.ok(container.querySelector("#soupLoginConfirmField"), "soup confirmation appears in register mode");
    assert.equal(container.querySelectorAll(".password-input-wrap").length, 2);
    assert.equal(container.querySelectorAll(".password-visibility-toggle").length, 2);

    await click(container.querySelector("#soupLoginPassword").parentElement.querySelector(".password-visibility-toggle"));
    assert.equal(container.querySelector("#soupLoginPassword").type, "text");
    await click(authTab("登录"));
    assert.equal(container.querySelector("#soupLoginPassword").type, "password", "soup mode switch restores masking");
    assert.equal(container.querySelector("#soupLoginConfirmField"), null);
    await click(authTab("注册"));

    await type(container.querySelector('input[autocomplete="username"]'), "SoupUser");
    await type(container.querySelector("#soupLoginPassword"), "secret-one");
    await type(container.querySelector("#soupLoginConfirmPassword"), "secret-two");
    await click(container.querySelector("#loginSubmit"));
    assert.equal(requests.length, 0, "soup mismatched registration passwords do not call fetch");
    assert.equal(container.querySelector(".modal-msg").textContent, "两次输入的密码不一致");

    for (const input of container.querySelectorAll(".password-input-wrap input")) {
      const toggle = input.parentElement.querySelector(".password-visibility-toggle");
      const value = input.value;
      assert.equal(input.type, "password");
      await click(toggle);
      assert.equal(input.type, "text");
      assert.equal(input.value, value);
      await click(toggle);
      assert.equal(input.type, "password");
    }

    await render(React.createElement(AccountActionModal, {
      action: {kind: "password", target: "machine", id: 7, currentName: "BoundBot"},
      onClose() {},
      onSubmit: async () => {},
    }));
    assert.equal(container.querySelectorAll(".password-input-wrap").length, 2, "account action uses PasswordInput for both password fields");
    assert.equal(container.querySelectorAll(".password-visibility-toggle").length, 2);
    for (const input of container.querySelectorAll(".password-input-wrap input")) {
      const toggle = input.parentElement.querySelector(".password-visibility-toggle");
      await click(toggle);
      assert.equal(input.type, "text");
      await click(toggle);
      assert.equal(input.type, "password");
    }

    await render(React.createElement(LoginModal, {...loginProps, open: false}));
    await render(React.createElement(LoginModal, loginProps));
    assert.equal(container.querySelector("#soupLoginConfirmField"), null, "soup confirmation is reset after close/reopen");
  } finally {
    if (reactRoot) {
      const React = require(path.join(frontendRoot, "node_modules", "react"));
      await React.act(async () => reactRoot.unmount());
    }
    if (vite) await vite.close();
    dom.window.close();
    for (const name of globals) {
      if (previousGlobals[name] === undefined) delete global[name];
      else global[name] = previousGlobals[name];
    }
    if (previousGlobals.IS_REACT_ACT_ENVIRONMENT === undefined) delete global.IS_REACT_ACT_ENVIRONMENT;
    else global.IS_REACT_ACT_ENVIRONMENT = previousGlobals.IS_REACT_ACT_ENVIRONMENT;
    global.fetch = previousGlobals.fetch;
  }
}

async function main() {
  await checkMainPage();
  await checkTurtleSoup();
  console.log("password UI checks passed (main: 14 fields, turtle-soup: 4 logical fields)");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
