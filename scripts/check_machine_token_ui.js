"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const requests = [];
const issuedToken = "ctai_v1_abc_DEF-123456789";

function response(body, ok = true) {
  return {ok, json: async () => body};
}

function cssRule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = html.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `missing CSS rule: ${selector}`);
  return match[1];
}

async function main() {
  const dom = new JSDOM(html, {
    url: "https://toy.cedarstar.org/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      window.localStorage.setItem("cedartoy_token", "human-token");
      window.fetch = async (url, options = {}) => {
        requests.push({url, options});
        if (url === "/api/auth/me") {
          return response({
            user: {id: 1, username: "Human", is_ai: false, avatar: {type: "emoji", value: "🙂", is_default: true}},
            bindings: [{id: 7, username: "BoundBot", is_ai: true, avatar: {type: "emoji", value: "🤖", is_default: true}}],
          });
        }
        if (url === "/api/auth/avatar") {
          if (options.method === "POST") {
            return response({
              ok: true,
              user: {id: 1, username: "Human", is_ai: false, avatar: {type: "emoji", value: "🐼", is_default: false}},
            });
          }
          return response({supported: true, type: "emoji"});
        }
        if (url === "/api/auth/machine-token") {
          return response({token: issuedToken});
        }
        if (url === "/api/announcements") {
          return response({announcements: [], unread_count: 0, authenticated: true});
        }
        return response({}, false);
      };
    },
  });

  try {
    const {window} = dom;
    const byId = (id) => window.document.getElementById(id);
    await new Promise((resolve) => window.setTimeout(resolve, 30));

    assert.match(html, /id="mineMachineToken"/);
    assert.equal(byId("loginPass").closest("label").nextElementSibling.id, "loginConfirmField");
    assert.equal(byId("loginConfirmField").nextElementSibling.id, "loginAvatarField");
    assert.equal(byId("loginAvatarField").nextElementSibling.id, "forgotPasswordOpen");
    assert.equal(byId("forgotPasswordOpen").className, "forgot-password-link");
    assert.deepEqual(
      [...byId("loginModal").querySelectorAll(".modal-actions button")].map((button) =>
        button.id || button.textContent.trim()
      ),
      ["取消", "loginSubmit"]
    );
    window.setLoginMode("register");
    assert.equal(byId("forgotPasswordOpen").hidden, true);
    assert.equal(byId("loginAvatarField").hidden, false);
    window.setLoginMode("login");
    assert.equal(byId("forgotPasswordOpen").hidden, false);
    assert.equal(byId("loginAvatarField").hidden, true);

    window.renderMine();
    assert.equal(byId("mineAvatarOpen").textContent, "设置 / 修改 Emoji 头像");
    assert.equal(window.document.querySelector(".mine-profile-avatar").textContent, "🙂");
    byId("mineAvatarOpen").click();
    assert.equal(byId("avatarModal").classList.contains("show"), true);
    assert.equal(byId("avatarInput").value, "🙂");
    byId("avatarInput").value = "🐼";
    await window.saveAvatar();
    const avatarRequest = requests.filter(({url, options}) =>
      url === "/api/auth/avatar" && options.method === "POST"
    ).at(-1);
    assert.deepEqual(JSON.parse(avatarRequest.options.body), {avatar: "🐼"});
    assert.equal(avatarRequest.options.headers.Authorization, "Bearer human-token");
    assert.equal(window.document.querySelector(".mine-profile-avatar").textContent, "🐼");

    window.openMachineTokenModal();
    assert.equal(byId("machineTokenModeGet"), null);
    assert.equal(byId("machineTokenModeRotate"), null);
    assert.equal(
      byId("machineTokenHint").textContent,
      "获取新 Token 后，该小机此前所有旧 Token 都会失效，请使用新 Token 重新设置 MCP 地址。"
    );
    assert.equal(byId("machineTokenBoundField").hidden, false);
    assert.equal(byId("machineTokenBound").value, "7");
    assert.equal(byId("machineTokenUserField").hidden, true);
    assert.equal(byId("machineTokenPassField").hidden, false);
    assert.equal(byId("machineTokenBindField").hidden, true);
    assert.equal(window.document.activeElement.id, "machineTokenPass");

    byId("machineTokenPass").value = "machine-pass";
    await window.getMachineToken();
    const getRequest = requests.filter(({url}) => url === "/api/auth/machine-token").at(-1);
    assert.deepEqual(JSON.parse(getRequest.options.body), {
      username: "BoundBot",
      password: "machine-pass",
      bind: false,
      ai_user_id: 7,
    });
    assert.equal(getRequest.options.headers.Authorization, "Bearer human-token");
    assert.equal(byId("machineTokenForm").hidden, true);
    assert.equal(byId("machineTokenResult").hidden, false);
    assert.equal(byId("machineTokenSubmit").hidden, true);
    assert.equal(byId("machineTokenBoundField").closest("#machineTokenForm").hidden, true);
    assert.equal(byId("machineTokenUserField").closest("#machineTokenForm").hidden, true);
    assert.equal(byId("machineTokenPassField").closest("#machineTokenForm").hidden, true);
    assert.equal(byId("machineTokenValue").value, issuedToken);
    assert.equal(byId("machineTokenUrlValue").value, `https://toy.cedarstar.org/${issuedToken}`);
    assert.equal(byId("machineTokenUrlCopy").className, byId("machineTokenCopy").className);
    assert.equal(byId("machineTokenUrlCopy").textContent, "复制");
    assert.equal(byId("machineTokenCopy").textContent, "复制");

    let clipboardValue = null;
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: {writeText: async (value) => { clipboardValue = value; }},
    });
    await window.copyMachineTokenUrl();
    assert.equal(clipboardValue, `https://toy.cedarstar.org/${issuedToken}`);
    assert.equal(byId("machineTokenUrlCopy").textContent, "✓ 已复制");

    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: {writeText: async () => { throw new Error("denied"); }},
    });
    window.document.execCommand = (command) => {
      assert.equal(command, "copy");
      clipboardValue = window.document.activeElement.value;
      return true;
    };
    await window.copyMachineToken();
    assert.equal(clipboardValue, issuedToken);
    assert.equal(byId("machineTokenCopy").textContent, "✓ 已复制");

    byId("machineTokenBack").click();
    assert.equal(byId("machineTokenForm").hidden, false);
    assert.equal(byId("machineTokenResult").hidden, true);
    assert.equal(byId("machineTokenSubmit").hidden, false);
    assert.equal(byId("machineTokenSubmit").textContent, "获取 Token");
    assert.equal(byId("machineTokenValue").value, "");
    assert.equal(byId("machineTokenUrlValue").value, "");
    assert.equal(byId("machineTokenPass").value, "");

    byId("machineTokenBound").value = "";
    byId("machineTokenBound").dispatchEvent(new window.Event("change"));
    assert.equal(byId("machineTokenUserField").hidden, false);
    assert.equal(byId("machineTokenPassField").hidden, false);
    assert.equal(byId("machineTokenBindField").hidden, false);
    assert.equal(byId("machineTokenResult").hidden, true);

    byId("machineTokenUser").value = "OtherBot";
    byId("machineTokenPass").value = "manual-pass";
    await window.getMachineToken();
    const manualRequest = requests.filter(({url}) => url === "/api/auth/machine-token").at(-1);
    assert.deepEqual(JSON.parse(manualRequest.options.body), {
      username: "OtherBot",
      password: "manual-pass",
      bind: false,
    });
    assert.equal(manualRequest.options.headers.Authorization, undefined);
    assert.equal(byId("machineTokenForm").hidden, true);
    assert.equal(byId("machineTokenResult").hidden, false);
    assert.equal(byId("machineTokenSubmit").hidden, true);
    assert.equal(byId("machineTokenSuccessTitle").textContent, "Token 已获取");

    window.closeModals();
    assert.equal(byId("machineTokenForm").hidden, false);
    assert.equal(byId("machineTokenResult").hidden, true);
    assert.equal(byId("machineTokenSubmit").hidden, false);
    assert.equal(byId("machineTokenValue").value, "");
    window.openMachineTokenModal();
    assert.equal(byId("machineTokenTitle").textContent, "获取小机 Token");
    assert.equal(byId("machineTokenForm").hidden, false);
    assert.equal(byId("machineTokenResult").hidden, true);

    const modalBoxRule = cssRule("#machineTokenModal .modal-box");
    assert.match(modalBoxRule, /max-height:\s*calc\(var\(--visual-viewport-height\)/);
    assert.match(modalBoxRule, /overflow-y:\s*auto/);
    assert.match(html, /#machineTokenModal\s*\{[^}]*overflow-y:\s*auto/s);
    assert.match(html, /#machineTokenModal \.modal-box\s*\{[^}]*max-height:\s*calc\(var\(--visual-viewport-height\) - 96px/s);
    assert.match(html, /setProperty\("--visual-viewport-height", `\$\{Math\.max\(1, Math\.round\(vv\.height\)\)\}px`\)/);
    const tokenTextareaRule = cssRule("#machineTokenModal .machine-token-result textarea");
    assert.match(tokenTextareaRule, /min-height:\s*0/);
    assert.match(tokenTextareaRule, /height:\s*52px/);
    assert.match(tokenTextareaRule, /resize:\s*none/);
    assert.match(html, /#machineTokenModal \[hidden\]\s*\{[^}]*display:\s*none !important/s);

    console.log("machine token UI checks passed");
  } finally {
    dom.window.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
