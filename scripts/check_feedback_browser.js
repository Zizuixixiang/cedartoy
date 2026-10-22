"use strict";
// Uses an isolated Python fixture with real feedback request handlers over pipes and temporary accounts.
// Reuse an installed Playwright/Chromium; never install dependencies or contact production.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawn} = require("node:child_process");
const chromium = process.env.FEEDBACK_DOM_ONLY ? null : require(process.env.PLAYWRIGHT_MODULE || "playwright").chromium;
const root = path.resolve(__dirname, "..");
const python = String.raw`
import io, json, sys
from unittest.mock import Mock, patch
from pathlib import Path
from tests_toy.test_feedback import FeedbackTests
import server
case = FeedbackTests('runTest')
case.setUp()
# Exercise real pagination and private details with disposable fixtures.
for i in range(5): case.submit(case.human_id, ip='192.0.2.'+str(30+i))
with case._connect() as conn:
    for i in range(17):
        cursor=conn.execute("INSERT INTO feedback_tickets(owner_id,ip_hash,category,content,created_at_epoch) VALUES (?,?,'bug',?,?)", (case.human_id,'fixture','分页反馈 '+str(i),1700000000))
        case.store._message(conn,cursor.lastrowid,'player',case.human_id,'分页反馈 '+str(i),1700000000,'fixture')
print(json.dumps({'human':case.human_token,'admin':case.admin_token}),flush=True)
try:
    for line in sys.stdin:
        request=json.loads(line)
        handler=object.__new__(server.CedarToyHandler)
        handler.path=request['path']
        raw=request.get('body','').encode()
        handler.headers={key.title():value for key,value in request.get('headers',{}).items()}
        handler.headers['Content-Length']=str(len(raw))
        handler.rfile=io.BytesIO(raw)
        handler._client_ip=Mock(return_value=handler.headers.get('X-Feedback-Test-Ip','198.51.100.9'))
        handler._send_json=Mock()
        if request['method']=='POST':
            # Fast UI sequences bypass only the in-memory request bucket; persistent write limits stay real.
            with patch.object(server, '_check_request_rate_limit', return_value=True): handler.do_POST()
        elif handler.path.startswith('/api/auth/me'):
            try: handler._send_json({'user':server._current_account(server._extract_bearer(handler.headers)), 'bindings':[]})
            except server._McpError: handler._send_json({'error':'未登录'},status=401)
        elif handler.path.startswith('/api/admin/recovery'): handler._handle_admin_recovery()
        elif handler.path.startswith('/api/admin/users'): handler._send_json({'users':[], 'total':0, 'page':1, 'page_size':50})
        else: handler._send_json({})
        response=handler._send_json.call_args
        print(json.dumps({'id':request['id'],'status':response.kwargs.get('status',200),'body':response.args[0]},ensure_ascii=False),flush=True)
finally: case.doCleanups()
`;
(async () => {
  const child = spawn("python3", ["-u", "-c", python], {cwd: root, stdio: ["pipe", "pipe", "pipe"]});
  let browser, stderr = "";
  child.stderr.on("data", data => stderr += data);
  try {
    const pending = new Map();
    let sequence = 0, buffer = "", ready;
    const fixturePromise = new Promise(resolve => { ready = resolve; });
    child.stdout.on("data", data => {
      buffer += data;
      while (buffer.includes("\n")) {
        const end = buffer.indexOf("\n"), response = JSON.parse(buffer.slice(0, end));
        buffer = buffer.slice(end + 1);
        if (!response.id) ready(response);
        else { pending.get(response.id)?.(response); pending.delete(response.id); }
      }
    });
    const fixture = await Promise.race([fixturePromise, new Promise((_, reject) => {
      child.on("exit", code => reject(new Error("Fixture exited " + code + ": " + stderr)));
    })]);
    const backend = request => new Promise(resolve => {
      const id = ++sequence; pending.set(id, resolve); child.stdin.write(JSON.stringify({id, ...request}) + "\n");
    });
    const base = "http://feedback.test";
    if (process.env.FEEDBACK_DOM_ONLY) { await require("./check_feedback_dom.js").run({backend, fixture, root}); return; }
    browser = await chromium.launch({headless: true, args: ["--no-sandbox"], ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    const screenshots = process.env.SCREENSHOT_DIR;
    if (screenshots) fs.mkdirSync(screenshots, {recursive: true});
    for (const width of [360, 430, 1280]) {
      const context = await browser.newContext({viewport: {width, height: 900}, extraHTTPHeaders: {"X-Feedback-Test-IP": `198.51.100.${width % 251}`}});
      await context.route("**/*", async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.origin !== base) return route.abort();
        if (["/", "/admin"].includes(url.pathname)) return route.fulfill({contentType: "text/html", body: fs.readFileSync(path.join(root, url.pathname === "/admin" ? "admin.html" : "index.html"), "utf8")});
        const response = await backend({path: url.pathname + url.search, method: req.method(), headers: req.headers(), body: req.postData() || ""});
        return route.fulfill({status: response.status, json: response.body});
      });
      const page = await context.newPage(), errors = [];
      page.on("pageerror", err => errors.push(err.message));
      await page.goto(base);
      const nav = page.locator(".bottom-nav .nav-label");
      assert.deepEqual(await nav.allTextContents(), ["反馈", "排行榜", "历史", "我的"]);
      await page.locator('[data-bottom-action="feedback"]').click();
      const notify = page.locator("#feedbackNotifyEmail"), email = page.locator("#feedbackContactEmail");
      assert.equal(await notify.isChecked(), false);
      assert.equal(await email.isVisible(), false);
      await notify.check();
      assert.equal(await email.isVisible(), true);
      await email.fill("nanshan2368@gmail.com");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await notify.uncheck();
      assert.equal(await email.isVisible(), false);
      await page.locator("#feedbackContent").fill('界面反馈 <img src=x onerror="window.feedbackXss=1">');
      if (screenshots) await page.screenshot({path: path.join(screenshots, `feedback-form-${width}.png`)});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const before = await page.locator(".bottom-nav").boundingBox();
      await page.locator('#feedbackSubmit').click();
      await page.locator("#feedbackReceipt").waitFor({state: "visible"});
      const code = await page.locator("#feedbackReceiptCode").inputValue();
      assert.equal(code.length, 43);
      assert.match(await page.locator("#feedbackReceipt").innerText(), /请立即保存/);
      await page.locator("#feedbackReceiptQuery").click();
      await page.locator("#feedbackDetail").waitFor({state: "visible"});
      assert.match(await page.locator("#feedbackDetail").innerText(), /待处理/);
      assert.equal(await page.locator("#feedbackDetail img").count(), 0);
      assert.equal(await page.evaluate(() => window.feedbackXss), undefined);
      assert.deepEqual(await page.locator(".bottom-nav").boundingBox(), before);
      await page.locator("#feedbackQueryCode").fill("x".repeat(43));
      await page.locator('#feedbackQueryForm button').click();
      await page.waitForFunction(() => document.getElementById("feedbackMessage").textContent.includes("未找到"));
      assert.equal(await page.locator("#feedbackDetail").isVisible(), false);
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#feedbackModal").isVisible(), false);
      assert.equal(await page.evaluate(() => document.activeElement.dataset.bottomAction), "feedback");
      // Admin opens the same anonymous ticket and replies through the real API.
      await page.evaluate(value => localStorage.setItem("cedartoy_token", value), fixture.admin);
      await page.goto(base + "/admin");
      await page.locator("#recoveryTab").click();
      await page.locator('[data-ticket-kind="feedback"]').click();
      await page.locator("#feedbackAdminTickets button").first().waitFor();
      if (screenshots) await page.screenshot({path: path.join(screenshots, `feedback-admin-list-${width}.png`)});
      await page.locator("#feedbackAdminTickets button").first().click();
      await page.locator("#feedbackAdminDetail").waitFor({state: "visible"});
      await page.locator("#feedbackAdminNote").fill("管理员内部，玩家不可见");
      for (let i = 0; i < 3; i++) {
        await page.locator("#feedbackAdminReply").fill(`答复 ${i} <script>window.feedbackXss=2</script>`);
        await page.locator('#feedbackAdminReview button[type="submit"]').click();
        await page.waitForFunction(() => !feedbackAdminBusy);
        assert.equal(await page.locator("#feedbackAdminReply").inputValue(), "");
      }
      assert.equal(await page.locator("#feedbackAdminMessages .feedback-recent .feedback-message").count(), 3);
      assert.equal(await page.locator("#feedbackAdminMessages details").evaluate(el => el.open), false);
      assert.equal(await page.locator("#feedbackAdminTickets").isVisible(), false);
      if (screenshots) await page.screenshot({path: path.join(screenshots, `feedback-admin-${width}.png`)});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.locator("#feedbackAdminMessages summary").click();
      assert.equal(await page.locator("#feedbackAdminMessages .feedback-message:visible").count(), 4);
      await page.locator("#feedbackAdminMessages summary").click();
      await page.locator("#feedbackAdminClose").click();
      await page.waitForFunction(() => !feedbackAdminBusy);
      assert.match(await page.locator("#feedbackAdminDetail h3").innerText(), /已结束/);
      await page.locator("#feedbackAdminBack").click();
      await page.waitForFunction(() => !feedbackAdminBusy);
      await page.locator("#feedbackAdminFilter").selectOption("closed");
      await page.locator("#feedbackAdminTickets button").first().click();
      await page.waitForFunction(() => !feedbackAdminBusy);
      assert.equal(await page.locator("#feedbackAdminNote").inputValue(), "管理员内部，玩家不可见");
      await page.locator('[data-ticket-kind="recovery"]').click();
      assert.equal(await page.locator("#recoveryView").isVisible(), true);
      assert.equal(await page.locator("#feedbackAdminFilter").isVisible(), false);
      // Anonymous query exposes only the public reply; no markup executes.
      await page.evaluate(() => localStorage.removeItem("cedartoy_token"));
      await page.goto(base);
      await page.locator('[data-bottom-action="feedback"]').click();
      await page.locator('[data-feedback-mode="query"]').click();
      await page.locator("#feedbackQueryCode").fill(code);
      await page.locator('#feedbackQueryForm button').click();
      await page.locator("#feedbackDetail").waitFor({state: "visible"});
      assert.match(await page.locator("#feedbackDetail").innerText(), /已结束/);
      assert.equal(await page.locator("#feedbackMessages .feedback-recent .feedback-message").count(), 3);
      assert.equal(await page.locator("#feedbackMessages details").evaluate(el => el.open), false);
      await page.locator("#feedbackMessages summary").click();
      assert.equal(await page.locator("#feedbackMessages .feedback-message:visible").count(), 4);
      await page.locator("#feedbackMessages summary").click();
      await page.locator("#feedbackFollowupContent").fill("再次追问，结单后自动重新打开");
      await page.locator('#feedbackFollowupForm button[type="submit"]').click();
      await page.waitForFunction(() => !feedbackBusy);
      assert.match(await page.locator("#feedbackDetail h3").innerText(), /待处理/);
      assert.equal(await page.locator("#feedbackMessages .feedback-recent .feedback-message").count(), 3);
      assert.doesNotMatch(await page.locator("#feedbackDetail").innerText(), /管理员内部/);
      assert.equal(await page.evaluate(() => window.feedbackXss), undefined);
      if (screenshots) await page.screenshot({path: path.join(screenshots, `feedback-reply-${width}.png`)});
      await page.keyboard.press("Escape");
      await page.evaluate(value => localStorage.setItem("cedartoy_token", value), fixture.human);
      await page.locator('[data-bottom-action="feedback"]').click();
      await page.locator('[data-feedback-mode="list"]').click();
      await page.locator("#feedbackList button").first().waitFor();
      assert.equal(await page.locator("#feedbackList button").count(), 20);
      if (screenshots) await page.screenshot({path: path.join(screenshots, `feedback-list-${width}.png`)});
      await page.locator("#feedbackNext").click();
      await page.waitForFunction(() => document.getElementById("feedbackPage").textContent === "第 2 页");
      assert.equal(await page.locator("#feedbackList button").count(), 2);
      await page.locator("#feedbackList button").first().click();
      await page.locator("#feedbackDetail").waitFor({state: "visible"});
      await page.keyboard.press("Escape");
      await require("./check_feedback_unread_browser.js").run({page, backend, fixture, width, screenshots});
      assert.deepEqual(errors, [], "No JavaScript errors");
      console.log(`${width}px: four navigation items; anonymous QA/close/reopen; recent-three/history folding; admin reply/filter/switch; private notes hidden; XSS text-only; account pagination; focus restored; no horizontal overflow`);
      await context.close();
    }
  } finally {
    if (browser) await browser.close();
    child.stdin.end();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
