"use strict";
// Executes the real homepage; all API responses stay in memory.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM, VirtualConsole} = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");
const surfaces = {feedback: "feedbackModal", leaderboard: "leaderboardModal", history: "historyModal", mine: "mineDrawer"};
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

async function check(signedIn) {
  const errors = [], pending = new Map();
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", error => errors.push(error.message));
  const dom = new JSDOM(html, {
    url: "https://bottom-nav.test/", runScripts: "dangerously", pretendToBeVisual: true, virtualConsole,
    beforeParse(window) {
      if (signedIn) window.localStorage.setItem("cedartoy_token", "fixture-token");
      window.fetch = async url => {
        if (pending.has(url)) return new Promise(resolve => pending.set(url, resolve));
        let data = {};
        if (url === "/api/auth/me") data = {user: {id: 1, username: "fixture", is_ai: false}, bindings: []};
        if (url === "/api/auth/avatar") data = {supported: true, type: "emoji"};
        if (url === "/api/auth/history") data = {self: {username: "fixture", saves: {}}, machines: []};
        if (url === "/api/announcements") data = {announcements: [], unread_count: 0};
        if (url === "/soup/api/rooms/") data = [];
        return {ok: true, status: 200, json: async () => data};
      };
    },
  });
  const {window: w} = dom, d = w.document;
  const byId = id => d.getElementById(id);
  const nav = action => d.querySelector(`[data-bottom-action="${action}"]`);
  const visible = id => byId(id).classList.contains("show");
  const active = expected => assert.deepEqual(
    [...d.querySelectorAll("[data-bottom-action].active")].map(el => el.dataset.bottomAction),
    expected ? [expected] : [], `signedIn=${signedIn}, expected active=${expected || "none"}`,
  );
  const escape = () => d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
  const close = (id, method) => {
    if (method === "button") (id === "mineDrawer" ? byId("mineClose") : byId(id).querySelector("[data-close-modal]")).click();
    if (method === "scrim") byId(id).click();
    if (method === "Escape") escape();
  };
  try {
    active(); // Also catch hard-coded selection before asynchronous initialization.
    await tick();
    active();
    for (const filter of ["test", "mini"]) {
      d.querySelector(`[data-filter="${filter}"]`).click();
      assert.equal(d.querySelector(".tab-button.active").dataset.filter, filter);
      active();
    }
    for (const [action, id] of Object.entries(surfaces)) {
      for (const method of ["button", "scrim", "Escape"]) {
        nav(action).click();
        const allowed = signedIn || !["history", "mine"].includes(action);
        active(allowed ? action : undefined);
        assert.equal(visible(id), allowed);
        if (!allowed) {
          assert.equal(visible("loginModal"), action === "mine");
          if (action === "mine") close("loginModal", method);
          active();
          continue;
        }
        await tick();
        active(action);
        // Content clicks must not act as backdrop clicks.
        byId(id).querySelector(id === "mineDrawer" ? ".drawer" : ".modal-box").click();
        active(action);
        close(id, method);
        assert.equal(visible(id), false);
        active();
      }
    }
    // A new modal (including a non-bottom function) owns the highlight.
    nav("leaderboard").click();
    active("leaderboard");
    nav("feedback").click();
    active("feedback");
    w.openModal("bindModal");
    active();
    escape();
    active();

    // Closing unrelated drawers must not clear an open modal's highlight.
    nav("feedback").click();
    w.closeDrawers();
    active("feedback");
    pending.set("/api/feedback/submit", null);
    byId("feedbackContent").value = "底栏回归测试反馈";
    byId("feedbackForm").dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true}));
    assert.equal(typeof pending.get("/api/feedback/submit"), "function");
    escape(); // Feedback intentionally refuses closing while a request is pending.
    byId("feedbackModal").click();
    assert.equal(visible("feedbackModal"), true);
    active("feedback");
    pending.get("/api/feedback/submit")({ok: false, status: 503, json: async () => ({error: "fixture failure"})});
    pending.delete("/api/feedback/submit");
    await tick();
    escape();
    active();

    if (signedIn) {
      byId("machinesOpen").click();
      active("mine");
      w.closeModals(); // Does not close the drawer.
      active("mine");
      byId("mineAvatarOpen").click();
      assert.equal(visible("mineDrawer"), false);
      assert.equal(visible("avatarModal"), true);
      active();
      byId("avatarInput").value = "🐼";
      await w.saveAvatar(); // Account flows reopen Mine without a bottom click.
      assert.equal(visible("mineDrawer"), true);
      active("mine");
      w.openGameDrawer();
      active();
      escape();
      nav("mine").click();
      nav("history").click(); // closeDrawers must not override the new modal.
      assert.equal(visible("mineDrawer"), false);
      active("history");
      escape();

      byId("historyOpen").click();
      active("history");
      escape();
      pending.set("/api/auth/history", null);
      nav("history").click();
      active("history");
      escape();
      pending.get("/api/auth/history")({ok: false, status: 503, json: async () => ({})});
      pending.delete("/api/auth/history");
      await tick();
      active(); // Late network completion must not resurrect selection.
      w.localStorage.removeItem("cedartoy_token");
      nav("history").click();
      assert.equal(visible("historyModal"), false);
      active();
    }
    assert.equal(d.querySelector(".tab-button.active").dataset.filter, "mini");
    assert.deepEqual(errors, [], "real page must not throw");
  } finally { w.close(); }
}

(async () => {
  await check(false);
  await check(true);
  console.log("Bottom nav: startup, auth, all four actions, button/scrim/Escape, modal/drawer transitions, pending feedback and late responses passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
