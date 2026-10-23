"use strict";
// Functional DOM fallback only: deliberately makes no rendered-layout claims.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM, VirtualConsole} = require("jsdom");
exports.run = async ({backend, fixture, root}) => {
  const windows = [], errors = [];
  async function page(file, token = "") {
    const console = new VirtualConsole(); console.on("jsdomError", e => errors.push(e.message));
    const dom = new JSDOM(fs.readFileSync(path.join(root, file), "utf8"), {
      url: "http://feedback.test/" + (file === "admin.html" ? "admin" : ""),
      runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: console,
      beforeParse(window) {
        if (token) window.localStorage.setItem("cedartoy_token", token);
        window.fetch = async (url, options = {}) => {
          const response = await backend({path: url, method: options.method || "GET", headers: {...options.headers, "X-Feedback-Test-IP": "198.51.100.88"}, body: options.body || ""});
          return {ok: response.status < 400, status: response.status, json: async () => response.body};
        };
      },
    });
    windows.push(dom.window);
    await new Promise(resolve => setTimeout(resolve, 50));
    return dom.window;
  }
  async function until(condition, hint) {
    for (let i = 0; i < 300; i++) { if (condition()) return; await new Promise(resolve => setTimeout(resolve, 10)); }
    throw new Error("Timed out: " + hint + "; errors: " + errors.join(","));
  }
  try {
    const w = await page("index.html"), d = w.document, $ = id => d.getElementById(id);
    const click = selector => d.querySelector(selector).click();
    const submit = id => $(id).dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true}));
    const done = () => until(() => !w.eval("feedbackBusy"), "feedback request");
    assert.deepEqual([...d.querySelectorAll(".bottom-nav .nav-label")].map(el => el.textContent), ["反馈", "排行榜", "历史", "我的"]);
    d.querySelector('[data-bottom-action="feedback"]').focus(); click('[data-bottom-action="feedback"]');
    assert.match($("feedbackIdentity").textContent, /无需登录/);
    click('[data-feedback-mode="list"]'); assert.match($("feedbackMessage").textContent, /凭码查询/);
    click('[data-feedback-mode="submit"]');
    $("feedbackContent").value = '<img src=x onerror="window.feedbackXss=1">反馈问题';
    $("feedbackContact").value = "别人账号不能改变归属";
    submit("feedbackForm"); await done();
    assert.equal($("feedbackReceipt").hidden, false, $("feedbackMessage").textContent);
    const code = $("feedbackReceiptCode").value; assert.equal(code.length, 43);
    assert.match($("feedbackReceipt").textContent, /请立即保存/);
    $("feedbackReceiptQuery").click(); await done();
    assert.equal($("feedbackDetail").hidden, false, $("feedbackMessage").textContent);
    assert.match($("feedbackDetail").textContent, /待处理/);
    assert.equal($("feedbackDetail").querySelectorAll("img").length, 0);
    $("feedbackQueryCode").value = "x".repeat(43); submit("feedbackQueryForm"); await done();
    assert.match($("feedbackMessage").textContent, /未找到/); assert.equal($("feedbackDetail").hidden, true);
    w.closeModals(); assert.equal(d.activeElement.dataset.bottomAction, "feedback");
    assert.equal(d.querySelectorAll("[data-feedback-inert]").length, 0);
    // Submit receipt survives closing/reopening, until user explicitly starts a new feedback.
    click('[data-bottom-action="feedback"]'); assert.equal($("feedbackReceiptCode").value, code);
    $("feedbackNew").click(); assert.equal($("feedbackForm").hidden, false);
    w.closeModals();
    const a = await page("admin.html", fixture.admin), ad = a.document, A = id => ad.getElementById(id);
    A("recoveryTab").click(); await until(() => !a.eval("recoveryLoading"), "recovery list");
    ad.querySelector('[data-ticket-kind="feedback"]').click();
    const adminDone = () => until(() => !a.eval("feedbackAdminBusy"), "admin request");
    await adminDone();
    A("feedbackAdminTickets").querySelector("button").click(); await adminDone();
    assert.equal(A("feedbackAdminDetail").hidden, false);
    assert.match(A("feedbackAdminDetail").textContent, /匿名/);
    assert.equal(A("feedbackAdminDetail").querySelectorAll("img").length, 0);
    const originalDetail = a.eval("feedbackAdminConversation");
    const longEmail = "a".repeat(64) + "@" + "b".repeat(63) + "." + "c".repeat(63) + ".invalid";
    for (const email of ["notice@example.invalid", longEmail, '<img src=x onerror="window.feedbackXss=1">&\'@example.invalid', "", null, undefined]) {
      a.renderAdminFeedbackDetail({...originalDetail, ticket: {...originalDetail.ticket, contact_email: email}});
      const facts = [...A("feedbackAdminDetail").querySelectorAll(".recovery-fact")];
      const fact = facts.find(el => el.querySelector("dt").textContent === (email ? "通知邮箱" : "邮件通知"));
      assert.ok(fact);
      assert.equal(fact.querySelector("dd").textContent, email || "未启用");
      assert.equal(A("feedbackAdminDetail").querySelectorAll("img,script").length, 0);
      assert.equal(a.feedbackXss, undefined);
    }
    a.renderAdminFeedbackDetail(originalDetail);
    const reply = async text => {
      A("feedbackAdminReply").value = text;
      A("feedbackAdminReview").dispatchEvent(new a.Event("submit", {bubbles: true, cancelable: true}));
      await adminDone();
      assert.equal(A("feedbackAdminReply").value, "", A("feedbackAdminStatus").textContent);
    };
    A("feedbackAdminNote").value = '内部秘密 <img src=x onerror="window.feedbackXss=1">';
    await reply('<script>window.feedbackXss=1</script>已修复');
    assert.match(A("feedbackAdminDetail").textContent, /处理中/);
    for (let i=0;i<9;i++) await reply("管理员补充 " + i);
    assert.equal(A("feedbackAdminMessages").querySelectorAll(".feedback-recent .feedback-message").length, 3);
    let history = A("feedbackAdminMessages").querySelector("details");
    assert.equal(history.open, false); assert.match(history.textContent, /展开历史消息/);
    history.open = true; history.querySelector("button").click(); await adminDone();
    history = A("feedbackAdminMessages").querySelector("details");
    assert.equal(history.open, true);
    assert.equal(A("feedbackAdminMessages").querySelectorAll(".feedback-message").length, 11);
    history.open = false;
    A("feedbackAdminClose").click(); await adminDone();
    assert.match(A("feedbackAdminDetail").textContent, /已结束/);
    A("feedbackAdminBack").click(); await adminDone();
    A("feedbackAdminFilter").value = "closed"; A("feedbackAdminFilter").dispatchEvent(new a.Event("change")); await adminDone();
    assert.equal(A("feedbackAdminTickets").querySelectorAll("button").length, 1);
    assert.equal(A("feedbackAdminDetail").hidden, true);
    ad.querySelector('[data-ticket-kind="recovery"]').click();
    assert.equal(A("recoveryQueue").hidden, false); assert.equal(A("feedbackQueue").hidden, true);
    assert.equal(A("recoveryView").options.length, 2);
    click('[data-bottom-action="feedback"]'); click('[data-feedback-mode="query"]');
    $("feedbackQueryCode").value = code; submit("feedbackQueryForm"); await done();
    assert.match($("feedbackDetail").textContent, /已结束/); assert.doesNotMatch($("feedbackDetail").textContent, /内部秘密/);
    assert.equal($("feedbackMessages").querySelectorAll(".feedback-recent .feedback-message").length, 3);
    history = $("feedbackMessages").querySelector("details");
    assert.equal(history.open, false); history.open = true;
    history.querySelector("button").click(); await done();
    assert.match($("feedbackMessages").textContent, /已修复/);
    assert.equal($("feedbackMessages").querySelectorAll(".feedback-message").length, 11);
    $("feedbackFollowupContent").value = "还有问题 <img src=x onerror=alert(1)>"; submit("feedbackFollowupForm"); await done();
    assert.match($("feedbackDetail").textContent, /待处理/);
    assert.equal($("feedbackMessages").querySelector("details").open, false);
    assert.equal($("feedbackMessages").querySelectorAll(".feedback-recent .feedback-message").length, 3);
    ad.querySelector('[data-ticket-kind="feedback"]').click(); await adminDone();
    A("feedbackAdminFilter").value = "pending"; A("feedbackAdminFilter").dispatchEvent(new a.Event("change")); await adminDone();
    assert.match(A("feedbackAdminTickets").querySelector("button").textContent, /玩家新留言/);
    A("feedbackAdminTickets").querySelector("button").click(); await adminDone();
    assert.match(A("feedbackAdminMessages").textContent, /还有问题/);
    assert.equal(A("feedbackAdminMessages").querySelectorAll("img,script").length, 0);
    assert.equal(w.feedbackXss, undefined); assert.equal(a.feedbackXss, undefined);
    w.closeModals(); w.localStorage.setItem("cedartoy_token", fixture.human);
    click('[data-bottom-action="feedback"]'); click('[data-feedback-mode="list"]'); await done();
    assert.equal($("feedbackList").children.length, 20);
    $("feedbackNext").click(); await done(); assert.equal($("feedbackList").children.length, 2);
    $("feedbackList").querySelector("button").click(); await done();
    assert.equal($("feedbackDetail").hidden, false);
    // Cross-tab identity change clears cached private content.
    w.closeModals();
    const api = async (operation, body = {}, admin = false) => {
      const response = await backend({path: `/api/${admin ? "admin/" : ""}feedback/${operation}`, method: "POST",
        headers: {"Content-Type": "application/json", Authorization: `Bearer ${admin ? fixture.admin : fixture.human}`}, body: JSON.stringify(body)});
      assert.equal(response.status, 200); return response.body;
    };
    const tickets = (await api("list")).tickets.slice(0, 2).map(t => t.id);
    const review = (id, body) => api("review", {ticket_id:id, confirm:true, ...body}, true);
    const openTicket = async id => {
      const rows = (await api("list")).tickets;
      $("feedbackList").querySelectorAll("button")[rows.findIndex(t => t.id === id)].click(); await done();
    };
    await w.refreshFeedbackUnread(); assert.equal($("feedbackUnreadDot").hidden, true);
    await review(tickets[0], {status:"closed", internal_note:"only note"});
    await w.refreshFeedbackUnread(); assert.equal($("feedbackUnreadDot").hidden, true);
    for (const id of tickets) await review(id, {reply:"new admin reply"});
    await w.refreshFeedbackUnread(); assert.equal($("feedbackUnreadDot").hidden, false);
    click('[data-bottom-action="feedback"]'); click('[data-feedback-mode="list"]'); await done();
    assert.equal($("feedbackUnreadDot").hidden, false);
    assert.equal((await api("unread")).unread_count, 2);
    await openTicket(tickets[0]); assert.equal($("feedbackUnreadDot").hidden, false);
    assert.equal((await api("unread")).unread_count, 1);
    click('[data-feedback-mode="list"]'); await done(); await openTicket(tickets[1]);
    assert.equal($("feedbackUnreadDot").hidden, true);
    await review(tickets[0], {reply:"another reply"});
    // Delay an old unread response while a detail read is requested. Its snapshot
    // must not resurrect the badge after the queued detail has acknowledged it.
    const fetch = w.fetch;
    let release, entered;
    const waiting = new Promise(resolve => { entered = resolve; });
    w.fetch = async (url, options) => {
      const response = await fetch(url, options);
      if (url === "/api/feedback/unread") { entered(); await new Promise(resolve => { release = resolve; }); }
      return response;
    };
    const oldSnapshot = w.refreshFeedbackUnread(); await waiting;
    const read = w.feedbackRequest("detail", {ticket_id:tickets[0]});
    release(); await oldSnapshot; await read; w.fetch = fetch;
    assert.equal($("feedbackUnreadDot").hidden, true);
    await review(tickets[0], {reply:"after read"});
    await w.refreshFeedbackUnread(); assert.equal($("feedbackUnreadDot").hidden, false);
    const reloaded = await page("index.html", fixture.human);
    await reloaded.refreshFeedbackUnread(); assert.equal(reloaded.document.getElementById("feedbackUnreadDot").hidden, false);
    w.localStorage.removeItem("cedartoy_token");
    w.dispatchEvent(new w.StorageEvent("storage", {key: "cedartoy_token"}));
    assert.equal($("feedbackDetail").textContent, ""); assert.equal($("feedbackList").textContent, "");
    assert.equal($("feedbackReceiptCode").value, "");
    assert.equal($("feedbackUnreadDot").hidden, true);
    w.localStorage.setItem("cedartoy_token", fixture.human);
    w.dispatchEvent(new w.StorageEvent("storage", {key:"cedartoy_token"}));
    await w.refreshFeedbackUnread(); assert.equal($("feedbackUnreadDot").hidden, false);
    await w.feedbackRequest("detail", {ticket_id:tickets[0]}); assert.equal($("feedbackUnreadDot").hidden, true);
    assert.deepEqual(errors, []);
    console.log("DOM QA checks passed: unread badge clear/reappear, per-ticket reads, list preserves unread, slow-response ordering, reload/login; immutable rounds, history, anonymous code, notes, XSS and pagination. Rendered layout NOT verified.");
  } finally { windows.forEach(w => w.close()); }
};
