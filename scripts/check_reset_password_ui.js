"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const reply = (body, ok = true) => ({ok, json: async () => body});

(async () => {
  const requests = [], pending = [];
  let holdPost = false, finishPost;
  const dom = new JSDOM(html, {
    url: "https://toy.cedarstar.org/?reset_token=first&username=ForgedName",
    runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = async (url, options = {}) => {
        requests.push({url, options});
        if (url.startsWith("/api/auth/reset-password?")) {
          return new Promise((resolve, reject) => pending.push({resolve, reject}));
        }
        if (url === "/api/auth/reset-password") {
          if (holdPost) return new Promise(resolve => { finishPost = resolve; });
          return reply({ok: true});
        }
        if (url === "/api/announcements") return reply({announcements: [], unread_count: 0});
        return reply({});
      };
    },
  });
  try {
    const w = dom.window, $ = id => w.document.getElementById(id);
    const hint = $("resetPasswordHint"), submit = $("resetPasswordSubmit");
    const fields = [$("resetNewPassword"), $("resetConfirmPassword")];
    const visible = () => $("resetPasswordModal").classList.contains("show");
    const blocked = () => {
      assert.equal(submit.disabled, true);
      assert.equal(hint.textContent, "");
      for (const input of fields) {
        assert.equal(input.disabled, true);
        assert.equal(input.closest(".field").hidden, true);
        assert.equal(input.value, "");
      }
    };
    const open = token => {
      w.history.replaceState(null, "", `/?reset_token=${encodeURIComponent(token)}&username=ForgedName`);
      return w.openResetPasswordFromUrl();
    };
    await tick();
    assert.equal(hint.className, "modal-hint");
    assert.equal(hint.previousElementSibling.id, "resetPasswordTitle");
    assert.equal(w.getComputedStyle(hint).fontSize, w.getComputedStyle($("machinePasswordHint")).fontSize);
    assert.equal(visible(), false, "wait for token info before opening the modal");
    blocked();
    const info = requests.find(r => r.url.startsWith("/api/auth/reset-password?"));
    assert.equal(info.url, "/api/auth/reset-password?reset_token=first");
    assert.equal(info.options.cache, "no-store");
    pending.shift().resolve(reply({username: "真正账号<img src=x>"}));
    await tick();
    assert.equal(visible(), true);
    assert.equal(hint.textContent, "正在为账号「真正账号<img src=x>」重置密码");
    assert.equal(hint.children.length, 0, "username is rendered as text, never HTML");
    assert.equal(submit.disabled, false);
    assert.equal(w.document.activeElement, fields[0]);
    for (const input of fields) {
      assert.equal(input.disabled, false);
      assert.equal(input.closest(".field").hidden, false);
      input.value = "new-password";
    }
    submit.click(); await tick();
    const post = requests.find(r => r.options.method === "POST" && r.url === "/api/auth/reset-password");
    assert.deepEqual(JSON.parse(post.options.body), {reset_token: "first", new_password: "new-password"});
    assert.equal(visible(), false);
    assert.equal(new URL(w.location.href).searchParams.has("reset_token"), false);
    blocked();

    for (const error of ["无效的重置链接", "链接已过期", "该链接已使用", "账号处于待注销状态"]) {
      const opening = open(error);
      blocked(); assert.equal(visible(), false);
      pending.shift().resolve(reply({error}, false));
      await opening;
      assert.equal(visible(), true);
      assert.equal($("resetPasswordMsg").textContent, error);
      blocked();
      const count = requests.length;
      w.document.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
      await tick();
      assert.equal(requests.length, count, "Enter cannot submit an invalid token");
    }
    for (const fail of [p => p.reject(new Error("offline")), p => p.resolve(reply({})),
      p => p.resolve({ok: true, json: async () => { throw new Error("invalid JSON"); }})]) {
      const opening = open("failure"); fail(pending.shift()); await opening;
      blocked(); assert.equal(visible(), true); assert.ok($("resetPasswordMsg").textContent);
    }

    const older = open("older"), oldReply = pending.shift();
    const newer = open("newer+&"), newReply = pending.shift();
    assert.ok(requests.some(r => r.url.endsWith("reset_token=newer%2B%26")));
    newReply.resolve(reply({username: "新账号"})); await newer;
    oldReply.resolve(reply({username: "旧账号"})); await older;
    assert.equal(hint.textContent, "正在为账号「新账号」重置密码");
    w.clearResetPasswordModal(); blocked();

    const closing = open("closed"), lateReply = pending.shift();
    w.closeModals(); lateReply.resolve(reply({username: "不应残留"})); await closing;
    blocked(); assert.equal(visible(), false);

    const changed = open("verified"); pending.shift().resolve(reply({username: "已核验账号"})); await changed;
    w.history.replaceState(null, "", "/?reset_token=different");
    fields.forEach(input => { input.value = "new-password"; });
    const count = requests.length;
    await w.resetPasswordByToken();
    assert.equal(requests.length, count, "a changed URL cannot submit under the old account hint");
    blocked();
    w.history.replaceState(null, "", "/"); await w.openResetPasswordFromUrl();
    blocked(); assert.equal(visible(), false);

    holdPost = true;
    for (const ok of [true, false]) {
      const opening = open("slow-post"); pending.shift().resolve(reply({username: "待提交账号"})); await opening;
      fields.forEach(input => { input.value = "new-password"; });
      const posting = w.resetPasswordByToken();
      const replacement = open("replacement"); pending.shift().resolve(reply({error: "链接已过期"}, false)); await replacement;
      finishPost(reply(ok ? {ok: true} : {error: "旧请求错误"}, ok)); await posting;
      blocked(); assert.equal(visible(), true);
      assert.equal($("resetPasswordMsg").textContent, "链接已过期");
      assert.equal(new URL(w.location.href).searchParams.get("reset_token"), "replacement");
    }
    console.log("Reset UI passed: lookup before display, trusted username/text escaping, POST success, invalid/expired/used/deleting, network/JSON failure, Enter guard, URL encoding, stale responses and state cleanup.");
  } finally { dom.window.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
