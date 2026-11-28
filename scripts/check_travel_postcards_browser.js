// Offline UI acceptance. Point PLAYWRIGHT_MODULE / PLAYWRIGHT_EXECUTABLE_PATH at a local installation.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const art = fs.readFileSync(path.join(root, "vendor/travel-mcp/assets/postcards/paris-1.jpg"));
const machines = ["小机甲", "小机乙", "小机丙", "小机丁", "小机戊"].map((username, i) => ({
  id: i + 2, username, is_ai: true,
}));
const slots = [
  {player: "2:2", owner: "小机甲", slot: 2},   // historical postcards only
  {player: "3:5", owner: "小机乙", slot: 5},   // journey, no cards
];
const message = "从塞纳河边寄给你。\n<原文> " + "雨停了。".repeat(180);

async function check(browser, width) {
  const mobile = width < 600;
  const page = await browser.newPage({viewport: {width, height: mobile ? 740 : 900}, hasTouch: mobile});
  try {
    let roster = machines, saves = slots, deny = false, meRequests = 0;
    const reads = [];
    page.setDefaultTimeout(8000);
    await page.addInitScript(() => localStorage.setItem("cedartoy_token", "fixture"));
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.hostname !== "travel.test") return route.abort();
      if (url.pathname === "/") return route.fulfill({contentType: "text/html", body: html});
      if (/^\/assets\/icons\/[\w-]+\.(png|webp)$/.test(url.pathname)) {
        const file = path.join(root, url.pathname);
        if (fs.existsSync(file)) return route.fulfill({path: file});
      }
      if (url.pathname === "/api/auth/me") {
        meRequests++;
        return route.fulfill({json: {user: {id: 1, username: "人类"}, bindings: roster}});
      }
      if (url.pathname === "/api/travel/postcards") {
        assert.equal(route.request().headers().authorization, "Bearer fixture");
        const player = url.searchParams.get("player");
        if (!player) return route.fulfill({json: {machines: roster, slots: saves}});
        reads.push(player);
        if (deny) return route.fulfill({status: 403, json: {error: "无权查看这个存档"}});
        return route.fulfill({json: {postcards: ["2:2", "1:3"].includes(player) ? [{
          destination: player === "2:2" ? "巴黎" : "东京", date: "2025-07-25",
          message, image: player === "2:2" ? "/api/travel/postcard-art/paris-1.jpg" : null,
        }] : []}});
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
    const picker = page.locator("#bankPickerPanel");
    const options = picker.getByRole("button");
    const close = async () => {
      await dialog.getByRole("button", {name: "关闭"}).click();
      await dialog.waitFor({state: "hidden"});
    };
    const checkEmpty = async player => {
      await dialog.getByText("这个槽位还没有明信片。").waitFor();
      assert.equal(reads.at(-1), player);
      await close();
    };
    const checkPicker = async (title, labels, state) => {
      await picker.getByText(title, {exact: true}).waitFor();
      await options.first().waitFor();
      assert.deepEqual(await options.allTextContents().then(items => items.map(x => x.trim())), labels);
      const bounds = await picker.boundingBox();
      assert(bounds && bounds.x >= 12 && bounds.x + bounds.width <= width - 12 && bounds.y >= 0 &&
        bounds.y + bounds.height <= page.viewportSize().height);
      if (process.env.TRAVEL_SCREENSHOTS) {
        await page.screenshot({path: path.join(process.env.TRAVEL_SCREENSHOTS, width + "-" + state + ".png")});
      }
    };
    // A fresh login may not yet have hydrated its bindings.
    await page.evaluate(() => { me.bindings = null; });
    const previous = meRequests;
    await open();
    await checkPicker("选择小机", machines.map(m => m.username), "machines");
    assert(meRequests > previous);
    assert.deepEqual(reads, []);
    await picker.getByRole("button", {name: "小机乙"}).click();
    await dialog.getByText("这个槽位还没有明信片。").waitFor();
    await visible("empty-slot");
    await checkEmpty("3:5");

    await open();
    await picker.getByRole("button", {name: "小机丙"}).click();
    await checkEmpty("4");

    await open();
    await picker.getByRole("button", {name: "小机甲"}).click();
    const row = dialog.getByRole("button", {name: /巴黎.*2025-07-25/});
    await row.waitFor();
    assert.equal(reads.at(-1), "2:2");
    assert.equal(await dialog.locator("#travelOwner").textContent(), "小机甲 · 槽2");
    await visible("list");
    if (mobile) await row.tap(); else await row.press("Enter");
    await dialog.locator("img").evaluate(img => img.decode());
    assert.equal(await dialog.locator("img").evaluate(img => img.naturalWidth), 1280);
    assert.equal(await dialog.locator("#travelDetail p").textContent(), message);
    await visible("detail");
    await dialog.getByRole("button", {name: "返回列表"}).click();
    assert(await row.evaluate(el => el === document.activeElement));
    await close();

    roster = [machines[0]];
    await open();
    await row.waitFor(); // one machine with one non-default slot
    await close();
    saves = [];
    await open();
    await checkEmpty("2"); // one machine, no travel files
    saves = [slots[0], {player: "2:4", slot: 4}];
    await open();
    await checkPicker("选择存档", ["槽2", "槽4"], "slots");
    await picker.getByRole("button", {name: "槽4"}).click();
    await checkEmpty("2:4");

    saves = [slots[0], {player: "1:3", slot: 3}];
    await open();
    await checkPicker("选择小机", ["小机甲", "本人存档"], "self");
    await picker.getByRole("button", {name: "本人存档"}).click();
    assert.equal(await dialog.locator("#travelOwner").textContent(), "本人存档 · 槽3");
    await dialog.getByRole("button", {name: /东京/}).click();
    assert.equal(reads.at(-1), "1:3");
    await dialog.getByText("原图暂不可用").waitFor();
    await page.keyboard.press("Escape");
    await dialog.waitFor({state: "hidden"});

    roster = [];
    await open(); // personal saves still work without any binding
    await dialog.getByRole("button", {name: /东京/}).waitFor();
    await close();
    roster = machines;
    await open();
    await picker.getByRole("button", {name: "小机甲"}).waitFor();
    roster = [];
    saves = [];
    deny = true; // selected machine has just been unbound
    await picker.getByRole("button", {name: "小机甲"}).click();
    await dialog.getByText("当前账号无权查看这个存档。").waitFor();
    assert.equal(await dialog.locator("#travelList button").count(), 0);
    assert.equal(await dialog.locator("select").count(), 0);
    await close();
    await open();
    await picker.getByText("还没有绑定小机，本人也暂无旅行存档。").waitFor();
    assert.equal(await options.count(), 0);
    console.log(width + "px: five machines, empty journey/slot, single, multiple slots, self, unbind and reader passed");
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
