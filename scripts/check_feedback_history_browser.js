"use strict";
// Combined native focus/layout check; intercepted fixtures only, never production APIs.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");
const out = process.env.INTEGRATION_SCREENSHOTS || path.join(root, "artifacts/integration/combined");
const round = n => ({round_id: String(n), title: `问答 ${n}`, human_name: "玩家", ai_name: "小机", status: "complete"});
const history = () => ({self: {username: "玩家", saves: {eco: {slots: [{slot: 1, day: 3}]}},
  duel_history: {available: true, total: 11, matches: Array.from({length: 11}, () => ({game_type: "gomoku", outcome: "win", opponents: ["对手"]}))},
  deeptalk_history: {total: 3, rounds: [round(1), round(2)]}}, machines: []});
const conversation = {ticket: {id: 1, category: "bug", status: "processing", created_at_epoch: 1700000000},
  messages: Array.from({length: 8}, (_, i) => ({id: i + 1, role: i % 2 ? "admin" : "player", created_at_epoch: 1700000000 + i,
    content: `${i % 2 ? "管理员答复" : "玩家提问"} ${i + 1}：${"用于检查多轮对话是否在弹窗内滚动。".repeat(5)}`})),
  message_count: 8, next_before_id: null};
(async () => {
  const browser = await chromium.launch({headless: true, args: ["--no-sandbox"],
    executablePath: process.env.CHROMIUM_PATH || process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  try {
    fs.mkdirSync(out, {recursive: true});
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({viewport: {width, height: width === 360 ? 780 : 900}, hasTouch: width < 500});
      const errors = [], requests = [];
      let pendingDeepTalk;
      page.on("pageerror", e => errors.push(e.message));
      await page.route("**/*", async route => {
        const url = new URL(route.request().url());
        if (url.origin !== "http://integration.test") return route.abort();
        if (url.pathname === "/") return route.fulfill({contentType: "text/html", body: fs.readFileSync(path.join(root, "index.html"), "utf8")});
        requests.push(url.pathname);
        let data = {count: 0, announcements: [], unread_count: 0};
        if (url.pathname === "/api/auth/history") data = history();
        if (url.pathname === "/api/feedback/query") data = conversation;
        if (url.pathname === "/api/deeptalk") {
          await new Promise(resolve => {pendingDeepTalk = resolve;});
          data = {total: 3, rounds: [round(3)]};
        }
        return route.fulfill({json: data});
      });
      await page.goto("http://integration.test");
      await page.evaluate(() => {me = {user: {id: 1, username: "玩家"}, bindings: []}; localStorage.setItem("cedartoy_token", "fixture-only");});
      const tap = async selector => width < 500 ? page.locator(selector).tap() : page.locator(selector).click();
      const nav = action => `[data-bottom-action="${action}"]`;
      const close = async () => {await page.keyboard.press("Escape"); assert.equal(await page.locator("[data-feedback-inert]").count(), 0);};
      const bounds = async id => {
        const geometry = await page.evaluate(id => {
          const el = document.querySelector(`#${id} .modal-box`), box = el.getBoundingClientRect();
          return {top: box.top, bottom: box.bottom, height: box.height, viewportHeight: innerHeight,
            viewportWidth: innerWidth, pageWidth: document.documentElement.scrollWidth,
            maxHeight: getComputedStyle(el).maxHeight, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight};
        }, id);
        fs.writeFileSync(path.join(out, `${width}-${id}-bounds.json`), JSON.stringify(geometry, null, 2));
        assert.ok(geometry.top >= 0 && geometry.bottom <= geometry.viewportHeight + 1 &&
          geometry.pageWidth <= geometry.viewportWidth, `${id} within viewport: ${JSON.stringify(geometry)}`);
      };
      const feedback = async () => {
        await tap(nav("feedback"));
        await tap('[data-feedback-mode="query"]');
        await page.locator("#feedbackQueryCode").fill("x".repeat(43));
        const count = requests.filter(p => p === "/api/feedback/query").length;
        await tap("#feedbackQueryForm button");
        await page.locator("#feedbackDetail").waitFor({state: "visible"});
        assert.equal(requests.filter(p => p === "/api/feedback/query").length, count + 1);
        assert.equal(await page.locator("#feedbackMessages .feedback-recent .feedback-message").count(), 3);
        assert.equal(await page.locator("#feedbackMessages details").evaluate(el => el.open), false);
        await bounds("feedbackModal");
        await page.screenshot({path: path.join(out, `${width}-qa.png`)});
        await page.locator("#feedbackModal button").first().focus();
        await page.keyboard.press("Shift+Tab");
        assert.equal(await page.evaluate(() => document.activeElement ===
          document.querySelector("#feedbackModal [data-close-modal]")), true);
        await page.keyboard.press("Tab");
        assert.equal(await page.evaluate(() => document.activeElement === document.querySelector("#feedbackModal button")), true);
        await close();
        assert.equal(await page.evaluate(() => document.activeElement.dataset.bottomAction), "feedback");
      };
      const openHistory = async () => {
        await tap(nav("history")); await page.locator('[data-history-fold="duel"]').waitFor();
        await tap('[data-history-fold="duel"]');
        assert.equal(await page.locator('[data-history-section="duel"] .history-duel-match:visible').count(), 5);
        await tap("#duelHistoryMore");
        assert.equal(await page.locator('[data-history-section="duel"] .history-duel-match:visible').count(), 10);
        assert.equal(await page.locator("#historyContent").evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
        await bounds("historyModal"); await page.screenshot({path: path.join(out, `${width}-history.png`)});
      };
      await feedback(); await openHistory(); await close();
      await openHistory(); await close(); await feedback();
      await openHistory(); await tap('[data-history-fold="deeptalk"]'); await tap("#deeptalkHistoryMore");
      await page.waitForFunction(() => document.getElementById("deeptalkHistoryMore").disabled);
      await close(); await tap(nav("feedback")); await page.locator("#feedbackContent").focus();
      assert.ok(pendingDeepTalk); pendingDeepTalk();
      await page.waitForFunction(() => historyState.data.self.deeptalk_history.rounds.length === 3);
      assert.equal(await page.evaluate(() => document.activeElement.id), "feedbackContent");
      await close();
      await tap(nav("leaderboard")); await page.locator("#leaderboardModal").waitFor({state: "visible"}); await close();
      await tap(nav("mine")); await page.locator("#mineDrawer").waitFor({state: "visible"}); await close();
      assert.deepEqual(errors, []); console.log(`${width}px PASS: both opening orders, QA and history bounds, native focus, four navigation actions, late DeepTalk response`);
      await page.close();
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
