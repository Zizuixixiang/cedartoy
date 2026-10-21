"use strict";
// Requires Playwright + Chromium. No server or real account is used.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const html = fs.readFileSync(path.join(__dirname, "../admin.html"), "utf8");

(async () => {
  const browser = await chromium.launch({headless: true, args: ["--no-sandbox"]});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem("cedartoy_token", "fixture-only"));
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.pathname === "/admin") return route.fulfill({contentType: "text/html", body: html});
      if (url.pathname === "/api/admin/users") return route.fulfill({json: {
        users: [{id: 1, username: "布局测试", is_ai: false}], total: 1, page: 1, page_size: 50,
      }});
      if (url.pathname === "/api/admin/activity") return route.fulfill({json: {
        range: {label: "1小时"}, overview: {ok: true, games: [
          {game: "rnss", name: "你在人际关系中的需求被满足了吗？", active_users: 2, human_users: 1, ai_users: 1},
          {game: "dt_know100", name: "关于你，我还想知道100件事", active_users: 1, human_users: 1, ai_users: 0},
          {game: "deeptalk", name: "DeepTalk（旧记录未细分）", active_users: 1, human_users: 0, ai_users: 1},
          {game: "gem_merchant", name: "gem_merchant", active_users: 1, human_users: 0, ai_users: 1},
          {game: "memoria", name: "Memoria 车站", active_users: 1, human_users: 0, ai_users: 1},
          {game: "aidep", name: "学习时，你有多依赖 AI？", active_users: 1, human_users: 1, ai_users: 0},
        ]}, duel: {ok: true, range: {game_distribution: [
          {game_type: "gem_merchant", rooms: 7},
          {game_type: "monopoly_plus", rooms: 3},
          {game_type: "uno", rooms: 2},
        ]}}, turtle: {ok: true},
      }});
      return route.abort();
    });
    await page.goto("https://toy.cedarstar.org/admin");
    await page.waitForFunction(() => document.getElementById("countText").textContent.includes("1"));
    for (const width of [320, 360, 375, 390, 430, 1280]) {
      await page.setViewportSize({width, height: 844});
      for (const tab of ["users", "dashboard"]) {
        await page.locator(`#${tab}Tab`).click();
        const statusId = tab === "users" ? "statusText" : "dashboardStatus";
        await page.waitForFunction(id => document.getElementById(id).textContent === "", statusId);
        const measure = () => page.evaluate(({tab, statusId}) => {
          const panel = document.getElementById(`${tab}Panel`);
          const toolbar = panel.children[0].getBoundingClientRect();
          const status = document.getElementById(statusId);
          const rect = status.getBoundingClientRect();
          const card = panel.children[2].getBoundingClientRect();
          return {
            statusHeight: rect.height, statusWidth: rect.width,
            statusDisplay: getComputedStyle(status).display,
            gap: card.top - toolbar.bottom, gridGap: parseFloat(getComputedStyle(panel).rowGap),
            pageWidth: document.documentElement.scrollWidth, viewport: innerWidth,
          };
        }, {tab, statusId});
        const empty = await measure();
        assert.equal(empty.statusHeight, 0, `${width}px ${tab}: ${JSON.stringify(empty)}`);
        assert.equal(empty.statusDisplay, "none");
        assert.equal(empty.gap, empty.gridGap, "empty status must not leave a grid row or margins");
        assert.equal(empty.pageWidth, width, "page must not overflow horizontally");
        if (tab === "dashboard") {
          const cells = await page.locator('#gameOverviewContent [role="cell"]').evaluateAll(elements =>
            elements.map(el => ({text: el.textContent, width: el.clientWidth, scroll: el.scrollWidth})));
          assert.equal(cells.length, 24);
          assert.ok(cells.every(cell => cell.scroll <= cell.width), "long titles and counts must wrap without clipping");
          assert.ok(cells.some(cell => cell.text === "宝石商人"));
          assert.ok(cells.some(cell => cell.text === "记忆车站"));
          assert.ok(cells.some(cell => cell.text === "深聊问答（旧记录未细分）"));
          assert.deepEqual(await page.locator("#duelContent .bar-label").allTextContents(), ["宝石商人", "大富翁·改", "优诺牌"]);
          assert.equal(await page.locator("#duelContent .bar-row").first().getAttribute("title"), "宝石商人: 7");
          assert.doesNotMatch(await page.locator("#dashboardPanel").innerText(), /gem_merchant|monopoly_plus|UNO|Memoria|DeepTalk/);
        }
        if (process.env.SCREENSHOT_DIR) {
          fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
          await page.screenshot({path: path.join(process.env.SCREENSHOT_DIR, `admin-${tab}-${width}.png`), fullPage: true});
        }
        for (const [message, error] of [["加载中...", false], ["操作完成", false], ["读取失败，请重试", true]]) {
          await page.evaluate(({tab, message, error}) => {
            (tab === "users" ? setStatus : setDashboardStatus)(message, error);
          }, {tab, message, error});
          const filled = await measure();
          assert.ok(filled.statusHeight > 0);
          assert.ok(filled.gap > empty.gap);
          assert.equal(await page.locator(`#${statusId}`).innerText(), message);
          assert.equal(await page.locator(`#${statusId}`).evaluate(el => el.classList.contains("error")), error);
        }
        await page.evaluate(tab => (tab === "users" ? setStatus : setDashboardStatus)(""), tab);
        assert.deepEqual(await measure(), empty, "clearing status must restore the compact layout");
        console.log(`${width}px ${tab}: empty status 0px; toolbar/card gap ${empty.gap}px; loading, notice and error visible`);
      }
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
