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
  const calls = [], clipboard = [];
  let status = "pending", queryError = false;
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
          if (queryError) return reply({error: "账号或查询码错误"}, false);
          assert.equal(body.query_code, secret);
          return reply({status, admin_note: status === "rejected" ? "请补充信息 <script>" : "",
            ...(status === "approved" ? {reset_url: resetUrl, expires_at_epoch: 2000000000} : {})});
        }
        if (url === "/api/announcements") return reply({announcements: [], unread_count: 0, authenticated: false});
        return reply({});
      };
    },
  });
  try {
    const w = dom.window, d = w.document, $ = id => d.getElementById(id);
    const tabs = [...d.querySelectorAll("[data-recovery-mode]")];
    const countCalls = url => calls.filter(c => c.url === url).length;
    function assertMode(mode) {
      for (const tab of tabs) assert.equal(tab.getAttribute("aria-pressed"), String(tab.dataset.recoveryMode === mode));
      assert.equal($("recoveryForm").hidden, false);
      assert.equal($("recoverySuccess").hidden, true);
      assert.equal($("recoveryApplyFields").hidden, mode !== "apply");
      assert.equal($("recoveryQueryField").hidden, mode !== "query");
      assert.equal($("forgotPasswordSubmit").textContent, mode === "apply" ? "提交申请" : "查询进度");
    }
    function assertBlank() {
      for (const id of ["recoveryAccountKind", "recoveryAccount", "recoveryQueryCode"]) assert.equal($(id).value, "", id);
      assert.equal($("recoveryResultPanel").hidden, true);
    }
    function enterAccount(kind = "username", account = "Human") {
      $("recoveryAccountKind").value = kind;
      $("recoveryAccountKind").dispatchEvent(new w.Event("change"));
      $("recoveryAccount").value = account;
      $("recoveryAccount").dispatchEvent(new w.Event("input"));
    }
    function fillApplication(kind = "username", account = "Human") {
      enterAccount(kind, account);
      $("recoveryMachine").value = "Machine / 3";
      $("recoveryRegistered").value = "约 2026 年 8 月";
      $("recoveryGames").value = "海龟汤";
    }
    function assertSuccess() {
      assert.equal($("forgotPasswordTitle").textContent, "申请已提交");
      assert.equal($("recoveryForm").hidden, true);
      assert.ok($("forgotPasswordSubmit").closest("[hidden]"), "the original submit button must be hidden");
      assert.equal($("recoverySuccess").hidden, false);
      assert.match($("recoverySuccessDetails").textContent, /工单号：17/);
      assert.ok($("recoverySuccessDetails").textContent.includes("查询码：" + secret));
      assert.match($("recoverySuccess").textContent, /24\s*小时内完成审核/);
      assert.deepEqual([...$("recoverySuccess").querySelectorAll("button")].map(b => b.textContent), ["复制查询码", "查询进度", "关闭"]);
    }
    function assertHandoff(kind, account) {
      $("recoverySuccessQuery").click();
      assertMode("query");
      assert.equal($("recoveryAccountKind").value, kind);
      assert.equal($("recoveryAccount").value, account);
      assert.equal($("recoveryQueryCode").value, secret);
    }
    await tick();
    assert.deepEqual(tabs.map(tab => tab.dataset.recoveryMode), ["apply", "query"]);
    assert.deepEqual(tabs.map(tab => tab.textContent), ["申请找回", "查询进度"]);
    assertMode("apply"); assertBlank();
    $("loginUser").value = "Human";
    w.localStorage.setItem("cedartoy_recovery_query:username:Human", secret);
    w.openForgotPasswordModal();
    assertMode("apply"); assertBlank();
    assert.match($("forgotPasswordHint").textContent, /24\s*小时内完成审核/);
    fillApplication();
    const application = w.submitForgotPassword();
    assert.equal($("forgotPasswordSubmit").disabled, true);
    await w.submitForgotPassword();
    await application;
    assert.equal(countCalls("/api/auth/recovery/submit"), 1, "double clicks must not submit twice");
    assertSuccess();
    assert.equal(w.localStorage.getItem("cedartoy_recovery_query:username:Human"), secret);
    await w.submitForgotPassword();
    assert.equal(countCalls("/api/auth/recovery/submit"), 1, "success view must not resubmit on Enter");
    $("recoveryCopyCode").click(); await tick();
    assert.equal(clipboard.at(-1), secret);
    assertHandoff("username", "Human");
    const titles = {pending: "审批中", approved: "已通过", rejected: "未通过", completed: "已完成", expired: "已过期", unavailable: "暂不可领取"};
    for (const [nextStatus, title] of Object.entries(titles)) {
      status = nextStatus;
      await w.submitForgotPassword();
      const panel = $("recoveryResultPanel");
      assert.equal(panel.hidden, false);
      assert.ok(panel.classList.contains("recovery-status-card"));
      assert.equal(panel.getAttribute("role"), "status");
      assert.equal(panel.getAttribute("aria-labelledby"), "recoveryResultTitle");
      assert.equal(panel.dataset.status, status);
      assert.equal($("recoveryResultTitle").textContent, title);
      assert.ok(panel.contains($("recoveryResultTitle")) && panel.contains($("recoveryResult")));
      assert.equal($("recoveryResult").classList.contains("modal-hint"), false);
      assert.equal($("forgotPasswordMsg").textContent, "", "normal states must not use the error area");
      assert.equal($("recoveryLinkBox").hidden, status !== "approved");
      if (status === "pending") assert.match($("recoveryResult").textContent, /24\s*小时内完成审核/);
      if (status === "approved") {
        assert.equal($("recoveryLink").href, resetUrl);
        assert.ok(panel.contains($("recoveryLinkBox")));
        assert.match($("recoveryLinkBox").textContent, /24\s*小时内有效/);
        assert.match($("recoveryLinkExpiry").textContent, /链接有效至/);
        $("recoveryCopy").click(); await tick();
        assert.equal(clipboard.at(-1), resetUrl);
      } else {
        assert.equal($("recoveryLink").hasAttribute("href"), false, "old reset links must be cleared");
      }
      assert.equal($("recoveryAdminNote").hidden, status !== "rejected");
      if (status === "rejected") {
        assert.match($("recoveryAdminNote").textContent, /请补充信息 <script>/);
        assert.equal(panel.querySelector("script"), null, "admin notes must be text, not HTML");
      }
    }
    queryError = true;
    await w.submitForgotPassword();
    assert.match($("forgotPasswordMsg").textContent, /账号或查询码错误/);
    assert.equal($("recoveryResultPanel").hidden, true);
    queryError = false;
    // A handoff is consumed once: manual tab changes and reopening start empty.
    tabs.find(tab => tab.dataset.recoveryMode === "apply").click();
    enterAccount();
    tabs.find(tab => tab.dataset.recoveryMode === "query").click();
    assertMode("query"); assertBlank();
    enterAccount();
    assert.equal($("recoveryQueryCode").value, "", "saved codes must not be displayed after typing an account");
    const before = countCalls("/api/auth/recovery/query");
    await w.submitForgotPassword();
    assert.match($("forgotPasswordMsg").textContent, /请输入.*查询码/);
    assert.equal(countCalls("/api/auth/recovery/query"), before);
    // A fresh device can query by manually entering all three fields.
    w.localStorage.clear();
    $("recoveryQueryCode").value = secret;
    status = "approved";
    await w.submitForgotPassword();
    assert.equal($("recoveryLink").href, resetUrl);
    assert.equal(w.localStorage.getItem("cedartoy_recovery_query:username:Human"), secret);
    enterAccount("username", "OtherHuman");
    assert.equal($("recoveryQueryCode").value, "");
    assert.equal($("recoveryResultPanel").hidden, true);
    assert.equal($("recoveryLink").hasAttribute("href"), false);
    await w.submitForgotPassword();
    assert.match($("forgotPasswordMsg").textContent, /请输入.*查询码/);
    assert.equal(countCalls("/api/auth/recovery/query"), before + 1);
    w.closeModals(); w.openForgotPasswordModal();
    assertMode("apply"); assertBlank();
    // Blocked storage must not break submission or the one-time handoff, including numeric IDs.
    const setItem = w.Storage.prototype.setItem, getItem = w.Storage.prototype.getItem;
    try {
      w.Storage.prototype.setItem = w.Storage.prototype.getItem = () => { throw new Error("storage blocked"); };
      fillApplication("id", "42");
      const submits = countCalls("/api/auth/recovery/submit");
      await w.submitForgotPassword();
      assert.equal(countCalls("/api/auth/recovery/submit"), submits + 1);
      assert.equal($("forgotPasswordMsg").textContent, "");
      assertSuccess();
      assertHandoff("id", "42");
      await w.submitForgotPassword();
      assert.equal($("recoveryLink").href, resetUrl);
      assert.deepEqual(calls.filter(c => c.url === "/api/auth/recovery/query").at(-1).body,
        {account_kind: "id", account: "42", query_code: secret});
      w.setRecoveryMode("query");
      assertBlank();
      enterAccount("id", "42"); $("recoveryQueryCode").value = secret;
      await w.submitForgotPassword();
      assert.equal($("forgotPasswordMsg").textContent, "");
      assert.equal($("recoveryLink").href, resetUrl);
    } finally {
      w.Storage.prototype.setItem = setItem;
      w.Storage.prototype.getItem = getItem;
    }
    w.setRecoveryMode("apply"); fillApplication();
    await w.submitForgotPassword(); assertSuccess();
    $("recoverySuccess").querySelector("[data-close-modal]").click();
    assert.equal($("forgotPasswordModal").classList.contains("show"), false);
    w.openForgotPasswordModal(); assertMode("apply"); assertBlank();
    w.setRecoveryMode("query"); assertMode("query"); assertBlank();
    assert.ok(!calls.some(c => c.url.startsWith("/api/auth/forgot-password")), "the email UI flag must not restore self-service recovery");
    console.log(`Forgot-password DOM (email flag ${emailEnabled}): two tabs, default apply, independent success, blank query, one-time handoff, six status cards, escaped notes, errors, copy and storage failure passed`);
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
    const tabs = d.querySelector(".admin-tabs");
    assert.deepEqual([...tabs.querySelectorAll("button")].map(button => button.textContent),
      ["用户管理", "找回工单", "运营看板"]);
    assert.equal(tabs.previousElementSibling.tagName, "HEADER");
    const panel = $("recoveryHeading").closest('[role="tabpanel"]');
    assert.equal(panel, $("recoveryPanel"));
    assert.equal($("recoveryTab").getAttribute("aria-controls"), panel.id);
    assert.equal(panel.getAttribute("aria-labelledby"), "recoveryTab");
    assert.ok(tabs.compareDocumentPosition(panel) & w.Node.DOCUMENT_POSITION_FOLLOWING);
    function assertActiveTab(name) {
      for (const tab of ["users", "recovery", "dashboard"]) {
        assert.equal($(tab + "Panel").hidden, tab !== name);
        assert.equal($(tab + "Tab").getAttribute("aria-selected"), String(tab === name));
      }
      assert.equal(w.location.hash, "#" + name);
    }
    assertActiveTab("users");
    assert.equal(w.getComputedStyle(panel).display, "none");
    assert.ok(!calls.some(c => c.url.startsWith("/api/admin/recovery?")));
    $("recoveryTab").click(); await tick();
    assertActiveTab("recovery");
    assert.notEqual(w.getComputedStyle(panel).display, "none");
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
    $("dashboardTab").click(); await tick();
    assertActiveTab("dashboard");
    $("usersTab").click(); await tick();
    assertActiveTab("users");
    $("recoveryTab").click(); await tick();
    assertActiveTab("recovery");
    assert.equal($("recoveryView").value, "processed");
    assert.equal($("recoveryTickets").querySelectorAll("textarea[readonly]").length, 3);
    console.log("Admin DOM: three peer tabs, default users, recovery visibility, inline escaped evidence, copy, review and processed records passed");
  } finally { dom.window.close(); }
}
(async () => { await checkForgotPassword(false); await checkForgotPassword(true); await checkAdmin(); })().catch(error => { console.error(error); process.exitCode = 1; });
