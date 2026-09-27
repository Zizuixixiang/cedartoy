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
        range: {label: "1小时"}, overview: {ok: true, games: []}, duel: {ok: true}, turtle: {ok: true},
      }});
      return route.abort();
    });
    await page.goto("https://toy.cedarstar.org/admin");
    await page.waitForFunction(() => document.getElementById("countText").textContent.includes("1"));
    for (const width of [320, 375, 390, 430]) {
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
        if (process.env.SCREENSHOT_DIR) {
          fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
          await page.screenshot({path: path.join(process.env.SCREENSHOT_DIR, `admin-${tab}-${width}.png`)});
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
