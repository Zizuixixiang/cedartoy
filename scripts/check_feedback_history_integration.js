"use strict";
// Cross-feature DOM regression only. Stubbed requests; no production/network/data access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM, VirtualConsole} = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");
const errors = [], requests = [];
const round = n => ({round_id: String(n), title: `问答 ${n}`, human_name: "玩家", ai_name: "小机", status: "complete"});
const fixture = () => ({self: {username: "玩家", saves: {eco: {slots: [{slot: 1, day: 3}]}},
  duel_history: {available: true, total: 11, matches: Array.from({length: 11}, () => ({game_type: "gomoku", outcome: "win", opponents: ["对手"]}))},
  deeptalk_history: {total: 3, rounds: [round(1), round(2)]}}, machines: []});
let finishDeeptalk, finishFeedback, holdFeedback = false;
const consoleProxy = new VirtualConsole();
consoleProxy.on("jsdomError", e => errors.push(e.message));
const dom = new JSDOM(html, {url: "https://integration.test", runScripts: "dangerously", pretendToBeVisual: true,
  virtualConsole: consoleProxy, beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      requests.push({url, options});
      let data = {count: 0, announcements: [], unread_count: 0};
      if (url === "/api/auth/history") data = fixture();
      if (url.startsWith("/api/deeptalk?")) {
        await new Promise(resolve => {finishDeeptalk = resolve;});
        data = {total: 3, rounds: [round(3)]};
      }
      if (url === "/api/feedback/submit") {
        if (holdFeedback) await new Promise(resolve => {finishFeedback = resolve;});
        data = {ticket_id: 1};
      }
      return {ok: true, status: 200, json: async () => data};
    };
  }});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
const nav = action => d.querySelector(`[data-bottom-action="${action}"]`);
const clickNav = action => {nav(action).focus(); nav(action).click();};
const fold = key => d.querySelector(`[data-history-fold="${key}"]`);
const escape = () => d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
const cleared = () => {
  assert.equal(d.querySelectorAll(".modal.show").length, 0);
  assert.equal(d.querySelectorAll("[data-feedback-inert]").length, 0);
  for (const action of ["feedback", "leaderboard", "history", "mine"]) assert.ok(!nav(action).closest("[inert]"));
};
(async () => {
  try {
    await tick();
    w.localStorage.setItem("cedartoy_token", "fixture-only");
    w.eval('me = {user: {id: 1, username: "玩家"}, bindings: []}');
    const ids = [...d.querySelectorAll("[id]")].map(el => el.id);
    assert.equal(new Set(ids).size, ids.length, "merged page IDs are unique");
    const checkFeedback = async close => {
      const before = requests.filter(r => r.url === "/api/feedback/submit").length;
      clickNav("feedback");
      assert.ok($("feedbackModal").classList.contains("show"));
      assert.equal($("historyModal").inert, true);
      assert.ok($("feedbackModal").contains(d.activeElement));
      $("feedbackContent").value = "交叉测试反馈";
      $("feedbackForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true}));
      await tick();
      assert.equal(requests.filter(r => r.url === "/api/feedback/submit").length, before + 1, "one submit listener");
      assert.match($("feedbackMessage").textContent, /已提交/);
      close(); cleared();
      assert.equal(d.activeElement, nav("feedback"));
    };
    const checkHistory = async close => {
      const before = requests.filter(r => r.url === "/api/auth/history").length;
      clickNav("history"); await tick();
      assert.equal(requests.filter(r => r.url === "/api/auth/history").length, before + 1);
      assert.ok(!$("historyModal").inert);
      assert.equal(fold("duel").getAttribute("aria-expanded"), "false");
      fold("duel").focus(); fold("duel").click();
      $("duelHistoryMore").click();
      assert.equal(d.querySelectorAll('[data-history-section="duel"] .history-duel-match').length, 10, "one pagination listener");
      assert.equal(d.activeElement, fold("duel"));
      close(); cleared();
    };
    for (const mode of ["escape", "button", "backdrop"]) {
      const close = id => () => mode === "escape" ? escape() : mode === "button" ? $(id).querySelector("[data-close-modal]").click() : $(id).click();
      await checkFeedback(close("feedbackModal")); await checkHistory(close("historyModal"));
      await checkHistory(close("historyModal")); await checkFeedback(close("feedbackModal"));
    }
    // A pending DeepTalk page may complete after history closes and feedback takes focus.
    clickNav("history"); await tick(); fold("deeptalk").click(); $("deeptalkHistoryMore").click(); await tick();
    escape(); clickNav("feedback"); $("feedbackContent").focus();
    const feedbackMarkup = $("feedbackModal").innerHTML;
    finishDeeptalk(); await tick();
    assert.equal($("feedbackModal").innerHTML, feedbackMarkup);
    assert.equal(d.activeElement, $("feedbackContent"));
    assert.equal(d.querySelectorAll('[data-history-section="deeptalk"] .history-duel-match').length, 3);
    escape(); cleared();
    // Exercise the trap's boundary handler with explicitly synthetic visibility (not layout proof).
    clickNav("feedback");
    const visibleControls = [...$("feedbackModal").querySelectorAll("button, input, select, textarea, [tabindex='-1']")]
      .filter(el => !el.disabled && !el.closest("[hidden]"));
    visibleControls.forEach(el => {el.getClientRects = () => [{}];});
    const first = visibleControls[0], last = visibleControls.at(-1);
    first.focus(); first.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Tab", shiftKey: true, bubbles: true, cancelable: true}));
    assert.equal(d.activeElement, last);
    last.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Tab", bubbles: true, cancelable: true}));
    assert.equal(d.activeElement, first);
    holdFeedback = true; $("feedbackContent").value = "稍慢的提交";
    $("feedbackForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true}));
    await tick(); escape(); assert.ok($("feedbackModal").classList.contains("show"));
    finishFeedback(); await tick(); escape(); cleared();
    clickNav("leaderboard"); await tick(); assert.ok($("leaderboardModal").classList.contains("show")); escape();
    clickNav("mine"); await tick(); assert.ok($("mineDrawer").classList.contains("show")); escape();
    assert.ok(!$("mineDrawer").classList.contains("show")); cleared();
    assert.deepEqual(errors, []);
    console.log("PASS: both opening orders × three close paths, one submit/page listener, inert cleanup, feedback focus/trap handler, late DeepTalk response, busy-close guard, all four navigation actions. DOM only; native focus and geometry require Chromium.");
  } finally {w.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
