// Offline UI acceptance. Point PLAYWRIGHT_MODULE / PLAYWRIGHT_EXECUTABLE_PATH at a local installation.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const art = fs.readFileSync(path.join(root, "vendor/travel-mcp/assets/postcards/paris-1.jpg"));
const slots = [
  {player: "1", owner: "我", slot: 1},
  {player: "2:2", owner: "小机甲", slot: 2},
  {player: "3:5", owner: "小机乙", slot: 5},
];
const message = "从塞纳河边寄给你。\n<原文> " + "雨停了。".repeat(180);

async function check(browser, width) {
  const mobile = width < 600;
  const page = await browser.newPage({viewport: {width, height: mobile ? 740 : 900}, hasTouch: mobile});
  try {
    let empty = false;
    await page.addInitScript(() => localStorage.setItem("cedartoy_token", "fixture"));
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.hostname !== "travel.test") return route.abort();
      if (url.pathname === "/") return route.fulfill({contentType: "text/html", body: html});
      if (url.pathname === "/api/auth/me") return route.fulfill({json: {user: {id: 1, username: "人类"}, bindings: []}});
      if (url.pathname === "/api/travel/postcards") {
        assert.equal(route.request().headers().authorization, "Bearer fixture");
        const player = url.searchParams.get("player");
        if (!player) return route.fulfill({json: {slots: empty ? [] : slots}});
        return route.fulfill({json: {postcards: player === "1" ? [] : [{
          destination: player === "2:2" ? "巴黎" : "东京", date: "2025-07-25",
          message, image: player === "2:2" ? "/api/travel/postcard-art/paris-1.jpg" : null,
        }]}});
      }
      if (url.pathname === "/api/travel/postcard-art/paris-1.jpg") {
        return route.fulfill({contentType: "image/jpeg", body: art});
      }
      if (url.pathname.startsWith("/api/")) return route.fulfill({json: {}});
      return route.abort();
    });
    await page.goto("http://travel.test/");
    await page.waitForFunction(() => Boolean(me?.user));
    const open = async () => {
      if (mobile && await page.locator("#gameDrawer.show").count()) {
        await page.locator("#drawerWatchButton").tap();
        return;
      }
      await page.locator("#searchInput").fill("旅行");
      const target = page.locator('[data-game="travel"] .card-open').first();
      if (mobile) {
        await target.tap();
        await page.locator("#drawerWatchButton").tap();
      } else {
        await target.focus();
        await target.press("Enter");
      }
    };
    const dialog = page.locator("#travelCards");
    const visible = async state => {
      const metrics = await dialog.evaluate(el => {
        const r = el.getBoundingClientRect();
        const content = el.querySelector(".travel-body");
        return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: innerWidth, height: innerHeight,
          overflow: content.scrollWidth - content.clientWidth,
          buttonHeight: el.querySelector("#travelDismiss").getBoundingClientRect().height};
      });
      assert(metrics.left >= 12 && metrics.right <= metrics.width - 12 && metrics.top >= 0 &&
        metrics.bottom <= metrics.height && metrics.overflow <= 1 && metrics.buttonHeight >= 44, JSON.stringify(metrics));
      if (process.env.TRAVEL_SCREENSHOTS) {
        await page.screenshot({path: path.join(process.env.TRAVEL_SCREENSHOTS, width + "-" + state + ".png")});
      }
    };
    await open();
    await dialog.getByText("这个槽位还没有明信片。").waitFor();
    await visible("empty-slot");
    await page.locator("#travelSlot").selectOption("2:2");
    const row = dialog.getByRole("button", {name: /巴黎.*2025-07-25/});
    await row.waitFor();
    await visible("list");
    if (mobile) await row.tap(); else await row.press("Enter");
    await dialog.locator("img").evaluate(img => img.decode());
    assert.equal(await dialog.locator("img").evaluate(img => img.naturalWidth), 1280);
    assert.equal(await dialog.locator("#travelDetail p").textContent(), message);
    await visible("detail");
    await dialog.getByRole("button", {name: "返回列表"}).click();
    assert(await row.evaluate(el => el === document.activeElement));
    await page.locator("#travelSlot").selectOption("3:5");
    await dialog.getByRole("button", {name: /东京/}).click();
    await dialog.getByText("原图暂不可用").waitFor();
    await page.keyboard.press("Escape");
    await dialog.waitFor({state: "hidden"});
    empty = true;
    await open();
    await dialog.getByText("还没有收到旅行明信片。").waitFor();
    await dialog.getByRole("button", {name: "关闭"}).click();
    console.log(width + "px: entry, slots, history, art, long message, return, close and empty states passed");
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH, headless: true});
  try {
    for (const width of [360, 428, 1280]) await check(browser, width);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
