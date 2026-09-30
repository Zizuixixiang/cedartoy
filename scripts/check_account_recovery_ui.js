"use strict";
// DOM interaction checks with mocked APIs: no server, mail or real account access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "..");
const reply = (data, ok = true) => ({ok, status: ok ? 200 : 400, json: async () => data});
const tick = () => new Promise(resolve => setTimeout(resolve, 10));

async function checkForgotPassword(emailEnabled) {
  const calls = [];
  const clipboard = [];
  let status = "pending";
  const secret = "Ab3X9k2Q";
  const resetUrl = "https://toy.cedarstar.org/?reset_token=fixture";
  const html = fs.readFileSync(path.join(root, "index.html"), "utf8").replace("const EMAIL_SECURITY_UI_ENABLED = false;", `const EMAIL_SECURITY_UI_ENABLED = ${emailEnabled};`);
  const dom = new JSDOM(html, {
    url: "https://toy.cedarstar.org/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(window) {
      window.navigator.clipboard = {writeText: async text => clipboard.push(text)};
      window.fetch = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : {};
        calls.push({url, body});
        if (url === "/api/auth/recovery/submit") return reply({ticket_id: 17, query_code: secret, message: "请保存查询码，用于查看审核结果；一般会在24小时内完成审核"});
        if (url === "/api/auth/recovery/query") {
          assert.equal(body.query_code, secret);
          return reply({status, admin_note: status === "rejected" ? "请补充信息 <script>" : "",
            ...(status === "approved" ? {reset_url: resetUrl, expires_at_epoch: 2000000000} : {})});
        }
        if (url === "/api/auth/forgot-password") return reply({state: "code_sent", masked_email: "h***@example.com"});
        if (url === "/api/auth/forgot-password/reset") return reply({message: "密码已重置"});
        if (url === "/api/announcements") return reply({announcements: [], unread_count: 0, authenticated: false});
        return reply({});
      };
    },
  });
  try {
    const w = dom.window, d = w.document, $ = id => d.getElementById(id);
    await tick();
    $("loginUser").value = "Human";
    w.openForgotPasswordModal();
    assert.equal(d.querySelectorAll("[data-recovery-mode]").length, 3);
    w.setRecoveryMode("apply");
    assert.equal($("forgotUsernameField").hidden, true);
    assert.equal($("recoveryAccount").value, "Human");
    $("recoveryMachine").value = "Machine / 3";
    $("recoveryRegistered").value = "约 2026 年 8 月";
    $("recoveryGames").value = "海龟汤";
    await w.submitForgotPassword();
    assert.match($("recoveryResult").textContent, /一般会在24小时内完成审核/);
    assert.equal(w.localStorage.getItem("cedartoy_recovery_query:username:Human"), secret);
    assert.match($("recoveryResult").textContent, /工单号：17/);
    assert.ok($("recoveryResult").textContent.includes("查询码：" + secret));
    assert.match($("recoveryResult").textContent, /请保存查询码，用于查看审核结果/);
    w.closeModals();
    w.openForgotPasswordModal();
    w.setRecoveryMode("query");
    assert.equal($("recoveryApplyFields").hidden, true);
    await w.submitForgotPassword();
    assert.match($("recoveryResult").textContent, /审批中/);
    assert.equal($("recoveryLinkBox").hidden, true);
    status = "approved";
    await w.submitForgotPassword();
    assert.equal($("recoveryLink").href, resetUrl);
    assert.equal($("recoveryLinkBox").hidden, false);
    $("recoveryCopy").click(); await tick();
    assert.equal(clipboard.at(-1), resetUrl);
    status = "rejected";
    await w.submitForgotPassword();
    assert.match($("recoveryResult").textContent, /请补充信息 <script>/);
    assert.equal($("recoveryResult").querySelector("script"), null);
    assert.equal($("recoveryLinkBox").hidden, true);
    assert.equal($("recoveryLink").hasAttribute("href"), false);
    status = "completed";
    await w.submitForgotPassword();
    assert.match($("recoveryResult").textContent, /已完成/);
    $("recoveryAccount").value = "OtherHuman";
    $("recoveryAccount").dispatchEvent(new w.Event("input"));
    const before = calls.filter(c => c.url === "/api/auth/recovery/query").length;
    await w.submitForgotPassword();
    assert.match($("forgotPasswordMsg").textContent, /请输入.*查询码/);
    assert.equal(calls.filter(c => c.url === "/api/auth/recovery/query").length, before);
    $("recoveryAccount").value = "Human";
    w.localStorage.clear();
    $("recoveryAccount").dispatchEvent(new w.Event("input"));
    await w.submitForgotPassword();
    assert.match($("forgotPasswordMsg").textContent, /请输入.*查询码/);
    // A fresh device can type the code without any saved browser credential.
    $("recoveryQueryCode").value = secret;
    status = "approved";
    await w.submitForgotPassword();
    assert.equal($("recoveryLink").href, resetUrl);
    assert.equal(w.localStorage.getItem("cedartoy_recovery_query:username:Human"), secret);
    // Both submission and manual query work with storage entirely blocked.
    w.setRecoveryMode("apply");
    const setItem = w.Storage.prototype.setItem, getItem = w.Storage.prototype.getItem;
    w.Storage.prototype.setItem = w.Storage.prototype.getItem = () => { throw new Error("storage blocked"); };
    const submits = calls.filter(c => c.url === "/api/auth/recovery/submit").length;
    await w.submitForgotPassword();
    assert.equal($("forgotPasswordMsg").textContent, "");
    assert.equal(calls.filter(c => c.url === "/api/auth/recovery/submit").length, submits + 1);
    assert.ok($("recoveryResult").textContent.includes("查询码：" + secret));
    w.setRecoveryMode("query");
    assert.equal($("recoveryQueryCode").value, "");
    $("recoveryQueryCode").value = secret;
    await w.submitForgotPassword();
    assert.equal($("recoveryLink").href, resetUrl);
    assert.equal($("forgotPasswordMsg").textContent, "");
    w.Storage.prototype.setItem = setItem;
    w.Storage.prototype.getItem = getItem;
    // Existing email flow remains reachable within the same modal.
    w.setRecoveryMode("self");
    $("forgotUsername").value = "Human";
    await w.submitForgotPassword();
    if (!emailEnabled) {
      assert.ok(!calls.some(c => c.url === "/api/auth/forgot-password"));
      console.log("Forgot-password DOM: ticket flow works with the existing email UI flag disabled");
      return;
    }
    assert.equal($("forgotCodeFields").hidden, false);
    $("forgotCode").value = "123456";
    $("forgotNewPassword").value = $("forgotConfirmPassword").value = "new-secret";
    await w.submitForgotPassword();
    assert.deepEqual(calls.find(c => c.url === "/api/auth/forgot-password/reset").body,
      {username: "Human", code: "123456", new_password: "new-secret"});
    console.log("Forgot-password DOM: explicit codes, cross-device query, copy, statuses, storage failure and email regression passed");
  } finally { dom.window.close(); }
}

async function checkAdmin() {
  const calls = [], clipboard = [];
  let tickets = [{id: 7, account_kind: "username", account: "Human", machine: "<img src=x onerror=alert(1)>",
    registered_about: "八月", games: "海龟汤", explanation: "补充信息", created_at_epoch: 1800000000, status: "pending", admin_note: ""}];
  const dom = new JSDOM(fs.readFileSync(path.join(root, "admin.html"), "utf8"), {
    url: "https://toy.cedarstar.org/admin", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(window) {
      window.localStorage.setItem("cedartoy_token", "admin-fixture");
      window.navigator.clipboard = {writeText: async text => clipboard.push(text)};
      window.fetch = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : {};
        calls.push({url, body, options});
        if (url.startsWith("/api/admin/recovery?")) return reply({tickets, total: tickets.length, pending_count: tickets.filter(t => t.status === "pending").length});
        if (url === "/api/admin/recovery/review") { tickets = []; return reply({ok: true}); }
        if (url.startsWith("/api/admin/users")) return reply({users: [], total: 0, page: 1, page_size: 50});
        return reply({});
      };
    },
  });
  try {
    const w = dom.window, d = w.document, $ = id => d.getElementById(id);
    await tick();
    assert.equal(d.querySelectorAll(".admin-tabs button").length, 2);
    const panel = $("recoveryHeading").closest("section");
    assert.ok(panel.compareDocumentPosition(d.querySelector(".admin-tabs")) & w.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.equal($("recoveryView").value, "pending");
    assert.match($("recoveryCount").textContent, /待审批 1/);
    assert.equal($("recoveryTickets").querySelector("img"), null, "submitted text must be escaped");
    assert.match($("recoveryTickets").textContent, /绑定小机/);
    d.querySelector('[data-recovery-action="copy"]').click(); await tick();
    for (const label of ["工单 ID：7", "账号名：Human", "绑定小机：", "约注册时间：", "玩过游戏：", "补充说明：", "提交时间："]) assert.ok(clipboard[0].includes(label));
    d.querySelector('[data-recovery-action="rejected"]').click(); await tick();
    assert.match($("recoveryStatus").textContent, /不通过的原因/);
    assert.equal(calls.filter(c => c.url === "/api/admin/recovery/review").length, 0);
    $("recoveryTickets").querySelector("textarea").value = "已核验";
    d.querySelector('[data-recovery-action="approved"]').click(); await tick();
    const review = calls.find(c => c.url === "/api/admin/recovery/review");
    assert.deepEqual(review.body, {ticket_id: 7, decision: "approved", admin_note: "已核验"});
    assert.equal(review.options.headers.Authorization, "Bearer admin-fixture");
    assert.ok(!calls.some(c => c.url.includes("generate-reset-link")), "approval never requests a token");
    assert.match($("recoveryCount").textContent, /待审批 0/);
    tickets = ["rejected", "approved", "completed"].map((status, i) => ({id: 8 + i, account_kind: "id", account: "42", machine: "小机", registered_about: "夏天", games: "花园", explanation: "", created_at_epoch: 1800000000, status, admin_note: "补充资料", reviewed_at_epoch: 1800000010}));
    $("recoveryView").value = "processed";
    $("recoveryView").dispatchEvent(new w.Event("change")); await tick();
    assert.match($("recoveryTickets").textContent, /补充资料/);
    for (const status of ["rejected", "approved", "completed"]) assert.ok($("recoveryTickets").textContent.includes(status));
    assert.equal($("recoveryTickets").querySelectorAll("textarea[readonly]").length, 3);
    assert.match($("recoveryTickets").textContent, /审核时间/);
    assert.ok(!$("recoveryTickets").textContent.includes("查看详情"));
    assert.equal(d.querySelector('[data-recovery-action="approved"]'), null);
    console.log("Admin DOM: independent module, two tabs, inline escaped evidence, copy, review and processed records passed");
  } finally { dom.window.close(); }
}
(async () => { await checkForgotPassword(false); await checkForgotPassword(true); await checkAdmin(); })().catch(error => { console.error(error); process.exitCode = 1; });
