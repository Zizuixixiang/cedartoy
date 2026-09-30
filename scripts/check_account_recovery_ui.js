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
  const secret = "AB3X-9K2Q-7MNP";
  let expectedCode = secret;
  const resetUrl = "https://toy.cedarstar.org/?reset_token=fixture";
  const html = fs.readFileSync(path.join(root, "index.html"), "utf8").replace("const EMAIL_SECURITY_UI_ENABLED = false;", `const EMAIL_SECURITY_UI_ENABLED = ${emailEnabled};`);
  const dom = new JSDOM(html, {
    url: "https://toy.cedarstar.org/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(window) {
      window.navigator.clipboard = {writeText: async text => clipboard.push(text)};
      window.fetch = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : {};
        calls.push({url, body});
        if (url === "/api/auth/recovery/submit") {
          assert.deepEqual(Object.keys(body).sort(), ["account", "account_kind", "explanation", "games", "machine", "registered_about"]);
          return reply({ticket_id: 17, query_code: secret, message: "请保存查询码，用于查看审核结果；一般会在24小时内完成审核"});
        }
        if (url === "/api/auth/recovery/query") {
          assert.deepEqual(body, {query_code: expectedCode});
          if (queryError) return reply({error: "查询码错误"}, false);
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
      const visibleFields = [...$("recoveryFields").querySelectorAll("input, select, textarea")].filter(el => !el.closest("[hidden]"));
      assert.deepEqual(visibleFields.map(el => el.id), mode === "query" ? ["recoveryQueryCode"] :
        ["recoveryAccountKind", "recoveryAccount", "recoveryMachine", "recoveryRegistered", "recoveryGames", "recoveryExplanation"]);
      if (mode === "query") {
        assert.equal(w.getComputedStyle($("recoveryApplyFields")).display, "none");
        assert.ok($("recoveryAccount").closest("[hidden]"));
        assert.ok($("recoveryAccountKind").closest("[hidden]"));
        assert.match($("forgotPasswordHint").textContent, /只需查询码/);
        assert.doesNotMatch($("forgotPasswordHint").textContent, /账号|ID/);
      }
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
    function assertHandoff() {
      $("recoverySuccessQuery").click();
      assertMode("query");
      assert.equal($("recoveryAccountKind").value, "");
      assert.equal($("recoveryAccount").value, "");
      assert.equal($("recoveryQueryCode").value, secret);
    }
    await tick();
    assert.deepEqual(tabs.map(tab => tab.dataset.recoveryMode), ["apply", "query"]);
    assert.deepEqual(tabs.map(tab => tab.textContent), ["申请找回", "查询进度"]);
    assertMode("apply"); assertBlank();
    const accountRow = $("recoveryAccountKind").parentElement;
    assert.equal(accountRow, $("recoveryAccount").parentElement);
    assert.equal(w.getComputedStyle(accountRow).gridTemplateColumns, "minmax(0, 104px) minmax(0, 1fr)");
    assert.equal(w.getComputedStyle($("recoveryAccountKind")).paddingLeft, "4px");
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
    assertHandoff();
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
    assert.match($("forgotPasswordMsg").textContent, /查询码错误/);
    assert.equal($("recoveryResultPanel").hidden, true);
    queryError = false;
    // A handoff is consumed once: manual tab changes and reopening start empty.
    tabs.find(tab => tab.dataset.recoveryMode === "apply").click();
    enterAccount();
    tabs.find(tab => tab.dataset.recoveryMode === "query").click();
    assertMode("query"); assertBlank();
    assert.equal($("recoveryQueryCode").value, "", "saved codes must not be displayed on query entry");
    const before = countCalls("/api/auth/recovery/query");
    await w.submitForgotPassword();
    assert.match($("forgotPasswordMsg").textContent, /请输入.*查询码/);
    assert.equal(countCalls("/api/auth/recovery/query"), before);
    // A fresh device can query with only the code, preserving legacy case and pasted formatting.
    w.localStorage.clear();
    status = "approved";
    let successfulQueries = 0;
    for (const pasted of [secret, secret.replaceAll("-", ""), "  " + secret.toLowerCase() + "  ", "Ab0I9z", "aB1O9zQ", "Ab3X9k2Q"]) {
      expectedCode = pasted.trim();
      $("recoveryQueryCode").value = pasted;
      $("recoveryQueryCode").dispatchEvent(new w.Event("input"));
      await w.submitForgotPassword();
      successfulQueries++;
      assert.equal($("recoveryLink").href, resetUrl);
      assert.equal(w.localStorage.getItem("cedartoy_recovery_query:username:Human"), null);
    }
    expectedCode = secret;
    $("recoveryQueryCode").value = "";
    $("recoveryQueryCode").dispatchEvent(new w.Event("input"));
    assert.equal($("recoveryResultPanel").hidden, true);
    assert.equal($("recoveryLink").hasAttribute("href"), false);
    for (const invalid of ["", "abc", "ABCD--EFGH-JKLM", "OOOO-OOOO-OOOO"]) {
      $("recoveryQueryCode").value = invalid;
      await w.submitForgotPassword();
      assert.match($("forgotPasswordMsg").textContent, /请输入.*查询码/);
    }
    assert.equal(countCalls("/api/auth/recovery/query"), before + successfulQueries);
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
      assertHandoff();
      await w.submitForgotPassword();
      assert.equal($("recoveryLink").href, resetUrl);
      assert.deepEqual(calls.filter(c => c.url === "/api/auth/recovery/query").at(-1).body,
        {query_code: secret});
      w.setRecoveryMode("query");
      assertBlank();
      $("recoveryQueryCode").value = secret;
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
    console.log(`Forgot-password DOM (email flag ${emailEnabled}): two tabs, default apply, independent success, blank code-only query, new/legacy codes, code-only handoff, six status cards, escaped notes, errors, copy and storage failure passed`);
  } finally { dom.window.close(); }
}

async function checkAdmin() {
  const calls = [], clipboard = [];
  const pending = {id: 7, account_kind: "username", account: "Human", machine: "<img src=x onerror=alert(1)>",
    registered_about: "八月", games: "海龟汤", explanation: "补充信息", created_at_epoch: 1800000000, status: "pending", admin_note: ""};
  let tickets = [pending], total = 1, pendingCount = 1, listError = false, copyError = false;
  const dom = new JSDOM(fs.readFileSync(path.join(root, "admin.html"), "utf8"), {
    url: "https://toy.cedarstar.org/admin", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(window) {
      window.localStorage.setItem("cedartoy_token", "admin-fixture");
      window.navigator.clipboard = {writeText: async text => {
        if (copyError) throw new Error("clipboard unavailable");
        clipboard.push(text);
      }};
      window.fetch = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : {};
        calls.push({url, body, options});
        if (url.startsWith("/api/admin/recovery?")) return listError ? reply({error: "加载失败"}, false) : reply({tickets, total, pending_count: pendingCount});
        if (url === "/api/admin/recovery/review") { tickets = []; total = pendingCount = 0; return reply({ok: true}); }
        if (url.startsWith("/api/admin/users")) return reply({users: [], total: 0, page: 1, page_size: 50});
        return reply({});
      };
    },
  });
  try {
    const w = dom.window, d = w.document, $ = id => d.getElementById(id);
    const style = el => w.getComputedStyle(el);
    const lastListCall = () => calls.filter(c => c.url.startsWith("/api/admin/recovery?")).at(-1);
    const refresh = async () => { $("recoveryRefresh").click(); await tick(); };
    const filter = async value => { $("recoveryView").value = value; $("recoveryView").dispatchEvent(new w.Event("change")); await tick(); };
    function assertCount(text, hidden = false) {
      assert.equal($("recoveryCount").textContent, text);
      assert.equal($("recoveryCount").hidden, hidden);
      if (hidden) assert.equal(style($("recoveryCount")).display, "none");
    }
    function assertActiveTab(name) {
      for (const tab of ["users", "recovery", "dashboard"]) {
        assert.equal($(tab + "Panel").hidden, tab !== name);
        assert.equal($(tab + "Tab").getAttribute("aria-selected"), String(tab === name));
      }
      assert.equal(w.location.hash, "#" + name);
    }
    function assertEmptyStatus() {
      assert.equal($("recoveryStatus").textContent, "");
      assert.equal(style($("recoveryStatus")).display, "none");
      assert.equal(style($("recoveryStatus")).minHeight, "0px");
      assert.equal(style($("recoveryStatus")).margin, "0px");
    }
    await tick();
    const tabs = d.querySelector(".admin-tabs"), panel = $("recoveryPanel");
    assert.deepEqual([...tabs.children].map(button => button.firstChild.textContent), ["用户管理", "找回工单", "运营看板"]);
    assert.equal(tabs.previousElementSibling.tagName, "HEADER");
    assert.equal($("recoveryTab").getAttribute("aria-controls"), panel.id);
    assert.equal(panel.getAttribute("aria-labelledby"), "recoveryTab");
    assert.ok(tabs.compareDocumentPosition(panel) & w.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.equal($("recoveryCount").parentElement, $("recoveryTab"));
    assert.equal(style($("recoveryCount")).position, "absolute");
    assert.equal(style($("recoveryCount")).backgroundColor, "rgb(198, 55, 55)");
    assert.equal(style($("recoveryTab")).position, "relative");
    assertActiveTab("users");
    assert.equal(style(panel).display, "none");
    assert.equal(calls.filter(c => c.url.startsWith("/api/admin/recovery?")).length, 1, "badge loads before entering recovery");
    assert.equal(lastListCall().options.headers.Authorization, "Bearer admin-fixture");
    assertCount("1");
    assert.match($("recoveryTab").getAttribute("aria-label"), /待审批 1 条/);
    assert.equal($("recoveryTickets").children.length, 0, "startup badge does not render the queue");
    assertEmptyStatus();
    assert.equal(panel.querySelector("h1, h2, .sub, .badge"), null);
    const assertQueueChrome = () => assert.doesNotMatch(panel.textContent, /账号找回工单|记录|请核验|备注会|待审批\s*\d|[（(](?:pending|approved|rejected|completed)[）)]/);
    assertQueueChrome();
    const toolbar = panel.querySelector(".recovery-toolbar");
    assert.deepEqual([...toolbar.children].map(el => el.id), ["recoveryView", "recoveryRefresh"]);
    assert.deepEqual([...$("recoveryView").options].map(el => [el.value, el.textContent]), [["pending", "待审批"], ["processed", "已处理"]]);
    assert.equal($("recoveryRefresh").textContent, "刷新");
    assert.equal(style(toolbar).display, "flex");
    assert.equal(style(toolbar).flexWrap, "nowrap");
    assert.equal(style(toolbar).justifyContent, "flex-end");
    assert.equal(style(toolbar).alignItems, "center");
    assert.equal(style($("recoveryView")).height, style($("recoveryRefresh")).height);
    assert.equal(style($("recoveryView")).borderRadius, style($("recoveryRefresh")).borderRadius);
    assert.equal($("recoveryPagination").previousElementSibling, $("recoveryTickets"));
    $("recoveryTab").click();
    assert.equal($("recoveryStatus").textContent, "加载中…");
    assert.notEqual(style($("recoveryStatus")).display, "none");
    assert.equal($("recoveryView").disabled, true);
    await tick();
    assertActiveTab("recovery");
    assert.notEqual(style(panel).display, "none");
    assert.equal($("recoveryView").value, "pending");
    assert.equal($("recoveryPagination").hidden, true);
    assert.equal(style($("recoveryPagination")).display, "none");
    assertEmptyStatus();
    const card = $("recoveryTickets").firstElementChild;
    assert.deepEqual([...card.children].map(el => el.className), ["recovery-card-header", "recovery-facts", "recovery-review"]);
    assert.equal(card.querySelector("h3").textContent, "工单 #7");
    assert.equal(style(card.querySelector("h3")).fontSize, "16px");
    assert.equal(style(card.querySelector("h3")).fontWeight, "700");
    assert.equal(card.querySelector(".recovery-pill").textContent, "审批中");
    assert.doesNotMatch(card.textContent, /pending/);
    assert.equal(card.querySelector("pre, img"), null, "facts are structured and submitted text is escaped");
    assert.deepEqual([...card.querySelectorAll("dt")].map(el => el.textContent), ["账号名", "绑定小机", "约注册时间", "玩过游戏", "补充说明", "提交时间"]);
    for (const value of card.querySelectorAll("dd")) assert.equal(style(value).fontSize, "14px");
    assert.equal(style(card.querySelector(".recovery-pill")).fontSize, "12px");
    assert.equal(style(card.querySelector(".recovery-pill")).fontWeight, "700");
    assert.equal(card.querySelectorAll("dd")[1].textContent, pending.machine);
    assert.equal(card.querySelector(".recovery-copy").parentElement, card.querySelector("header"));
    assert.equal(style(card.querySelector("header")).flexWrap, "nowrap");
    assert.equal(style(card.querySelector(".recovery-copy")).flexShrink, "0");
    const reviewArea = card.querySelector(".recovery-review");
    assert.equal(reviewArea.querySelector("label").control, reviewArea.querySelector("textarea"));
    assert.deepEqual([...reviewArea.querySelectorAll("button")].map(el => el.textContent), ["通过", "不通过"]);
    assert.equal(style(reviewArea.querySelector(".recovery-review-actions")).gridTemplateColumns, "repeat(2, minmax(0, 1fr))");
    assert.equal(style(reviewArea.querySelector("button")).height, style(reviewArea.querySelector("button.danger")).height);
    assert.equal(style(card.querySelector(".recovery-copy")).height, style($("recoveryRefresh")).height);
    assert.equal(style(reviewArea.querySelector("textarea")).borderRadius, style($("recoveryRefresh")).borderRadius);
    assert.equal(style(reviewArea.querySelector("label")).fontSize, "13px");
    assert.equal(style(reviewArea.querySelector("label")).fontWeight, "600");
    assertQueueChrome();
    d.querySelector('[data-recovery-action="copy"]').click(); await tick();
    assert.equal($("recoveryStatus").textContent, "核验信息已复制");
    assert.notEqual(style($("recoveryStatus")).display, "none");
    assert.equal(clipboard[0], `工单 ID：7\n账号名：Human\n绑定小机：${pending.machine}\n约注册时间：八月\n玩过游戏：海龟汤\n补充说明：补充信息\n提交时间：${w.formatEpoch(pending.created_at_epoch)}`);
    copyError = true;
    d.querySelector('[data-recovery-action="copy"]').click(); await tick();
    assert.match($("recoveryStatus").textContent, /复制失败/);
    assert.notEqual(style($("recoveryStatus")).display, "none");
    copyError = false;
    d.querySelector('[data-recovery-action="rejected"]').click(); await tick();
    assert.match($("recoveryStatus").textContent, /不通过的原因/);
    assert.notEqual(style($("recoveryStatus")).display, "none");
    assert.equal(calls.filter(c => c.url === "/api/admin/recovery/review").length, 0);
    reviewArea.querySelector("textarea").value = "已核验";
    d.querySelector('[data-recovery-action="approved"]').click(); await tick();
    const review = calls.find(c => c.url === "/api/admin/recovery/review");
    assert.deepEqual(review.body, {ticket_id: 7, decision: "approved", admin_note: "已核验"});
    assert.equal(review.options.headers.Authorization, "Bearer admin-fixture");
    assert.ok(!calls.some(c => c.url.includes("generate-reset-link")), "approval never requests a token");
    assertCount("0", true);
    assertEmptyStatus();
    assert.equal($("recoveryPagination").hidden, true);
    tickets = ["rejected", "approved", "completed"].map((status, i) => ({id: 8 + i, account_kind: "id", account: "42", machine: "小机", registered_about: "夏天", games: "花园", explanation: "", created_at_epoch: 1800000000, status, admin_note: i === 2 ? "  " : "补充资料 <script>", reviewed_at_epoch: i === 2 ? null : 1800000010}));
    total = 3;
    await filter("processed");
    assert.equal(lastListCall().url, "/api/admin/recovery?view=processed&page=1");
    assert.deepEqual([...$("recoveryTickets").querySelectorAll(".recovery-pill")].map(el => el.textContent), ["未通过", "已通过", "已完成"]);
    for (const processed of $("recoveryTickets").children) {
      assert.equal(processed.querySelector("textarea, .recovery-review, script"), null);
      assert.equal(processed.querySelectorAll("button").length, 1);
      assert.equal(processed.querySelector("button").dataset.recoveryAction, "copy");
      const facts = Object.fromEntries([...processed.querySelectorAll(".recovery-fact")].map(el => [el.querySelector("dt").textContent, el.querySelector("dd").textContent]));
      assert.equal(facts["账号ID"], "42");
      assert.equal(facts["补充说明"], "—");
      assert.equal(facts["管理员备注"], processed.dataset.status === "completed" ? "—" : "补充资料 <script>");
      assert.equal(facts["审核时间"], processed.dataset.status === "completed" ? "—" : w.formatEpoch(1800000010));
    }
    assertQueueChrome();
    d.querySelector('[data-recovery-action="copy"]').click(); await tick();
    assert.match(clipboard.at(-1), /账号 ID：42/);
    assert.match(clipboard.at(-1), /补充说明：无/);
    assert.equal(d.querySelector('[data-recovery-action="approved"], [data-recovery-action="rejected"]'), null);
    $("dashboardTab").click(); await tick(); assertActiveTab("dashboard");
    $("usersTab").click(); await tick(); assertActiveTab("users");
    $("recoveryTab").click(); await tick(); assertActiveTab("recovery");
    assert.equal($("recoveryView").value, "processed");
    assert.equal($("recoveryTickets").querySelector("textarea"), null);

    total = 41; pendingCount = 105;
    await refresh(); assertCount("99+");
    assert.equal($("recoveryPagination").hidden, false);
    assert.notEqual(style($("recoveryPagination")).display, "none");
    assert.equal($("recoveryPrev").disabled, true);
    $("recoveryNext").click(); await tick();
    assert.equal(lastListCall().url, "/api/admin/recovery?view=processed&page=2");
    $("recoveryNext").click(); await tick();
    assert.equal($("recoveryPage").textContent, "第 3 / 3 页");
    assert.equal($("recoveryNext").disabled, true);
    $("recoveryPrev").click(); await tick();
    assert.equal(lastListCall().url, "/api/admin/recovery?view=processed&page=2");
    tickets = [pending]; total = 20; pendingCount = 99;
    await filter("pending"); assertCount("99");
    assert.equal(lastListCall().url, "/api/admin/recovery?view=pending&page=1");
    assert.equal($("recoveryPagination").hidden, true);
    $("recoveryTickets").querySelector("textarea").value = "请补充资料";
    d.querySelector('[data-recovery-action="rejected"]').click(); await tick();
    assert.deepEqual(calls.filter(c => c.url === "/api/admin/recovery/review").at(-1).body, {ticket_id: 7, decision: "rejected", admin_note: "请补充资料"});
    tickets = [{id: 99, status: "pending"}]; total = 1;
    await refresh();
    assert.ok([...$("recoveryTickets").querySelectorAll("dd")].every(el => el.textContent === "—"));
    listError = true; await refresh();
    assertCount("0", true);
    assert.equal($("recoveryStatus").textContent, "加载失败");
    assert.equal($("recoveryPagination").hidden, true);
    assert.equal($("recoveryView").disabled, false);
    listError = false; await refresh(); assertEmptyStatus();

    // jsdom does not lay out media queries: inspect CSSOM for mobile rules explicitly.
    const rules = [...d.styleSheets[0].cssRules];
    const mobile = rules.find(rule => rule.conditionText === "(max-width: 760px)");
    const mobileStyle = selector => [...mobile.cssRules].filter(rule => rule.selectorText === selector).at(-1).style;
    const mobileTabs = [...mobile.cssRules].find(rule => rule.selectorText === ".admin-tabs button").style;
    // jsdom's CSS parser drops the existing unitless-zero flex shorthand.
    assert.match(d.querySelector("style").textContent, /@media \(max-width: 760px\)[\s\S]*?\.admin-tabs button\s*\{[^}]*flex: 1 1 0;/);
    assert.equal(parseFloat(mobileTabs.getPropertyValue("min-width")), 0);
    assert.equal(mobileStyle(".admin-tabs button").getPropertyValue("font-size"), "14px");
    assert.equal(mobileStyle(".recovery-card").getPropertyValue("padding"), "12px");
    assert.equal(mobileStyle(".recovery-facts").getPropertyValue("grid-template-columns"), "minmax(0, 1fr)");
    assert.ok(["none", ""].includes(style($("recoveryTickets")).maxHeight), "the queue must use page scrolling");
    assert.ok(!toolbar.classList.contains("toolbar"), "generic mobile toolbar grid must not affect recovery");
    console.log("Admin DOM/CSS: peer tabs, startup badge/zero/99+, compact toolbar, structured cards, escaped facts/notes, unchanged copy, approve/reject, processed fields, pagination, tab/filter changes, empty status and mobile layout rules passed");
  } finally { dom.window.close(); }
}

async function checkAdminBadgeFailure() {
  for (const failure of ["http", "network", "json", "stale"]) {
    let finishBadge, recoveryCalls = 0;
    const dom = new JSDOM(fs.readFileSync(path.join(root, "admin.html"), "utf8"), {
      url: "https://toy.cedarstar.org/admin", runScripts: "dangerously", pretendToBeVisual: true,
      beforeParse(window) {
        window.localStorage.setItem("cedartoy_token", "admin-fixture");
        window.fetch = async url => {
          if (url.startsWith("/api/admin/users")) return reply({users: [], total: 0, page: 1, page_size: 50});
          if (url.startsWith("/api/admin/recovery?")) {
            recoveryCalls++;
            if (failure === "stale") {
              if (recoveryCalls === 1) return new Promise(resolve => { finishBadge = resolve; });
              return reply({tickets: [], total: 0, pending_count: 2});
            }
            if (failure === "network") throw new Error("offline");
            if (failure === "json") return {ok: true, json: async () => { throw new Error("bad JSON"); }};
            return {ok: false, status: 401};
          }
          return reply({});
        };
      },
    });
    try {
      await tick();
      const w = dom.window, $ = id => w.document.getElementById(id);
      if (failure === "stale") {
        $("recoveryTab").click(); await tick();
        finishBadge(reply({pending_count: 77})); await tick();
        assert.equal($("recoveryCount").textContent, "2", "slow startup badge must not overwrite the queue count");
      } else {
        assert.equal($("recoveryCount").hidden, true);
        assert.equal($("usersPanel").hidden, false);
        assert.equal($("statusText").textContent, "");
        assert.equal($("recoveryStatus").textContent, "");
        assert.equal(w.localStorage.getItem("cedartoy_token"), "admin-fixture", "badge failure must not log out another panel");
      }
    } finally { dom.window.close(); }
  }
  console.log("Admin badge: HTTP/network/JSON failures remain silent; stale startup response cannot replace the current count");
}

(async () => { await checkForgotPassword(false); await checkForgotPassword(true); await checkAdmin(); await checkAdminBadgeFailure(); })().catch(error => { console.error(error); process.exitCode = 1; });
