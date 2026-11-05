"use strict";
// Isolated real HTTP/MCP handlers over pipes. No listener, production DB, or external requests.
// FEEDBACK_DOM_ONLY=1 is a functional fallback, never rendered visual evidence.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawn} = require("node:child_process");
const root = path.resolve(__dirname, "..");
const python = String.raw`
import io,json,sys
from unittest.mock import Mock,patch
from tests_toy.test_feedback import FeedbackTests
import server
case=FeedbackTests('runTest')
case.setUp()
print(json.dumps({'human':case.human_token,'ai':case.ai_token,'admin':case.admin_token}),flush=True)
try:
    for line in sys.stdin:
        req=json.loads(line)
        if req['path']=='/mcp':
            with patch.object(server,'_mcp_forced_announcement',return_value=None):
                body=server._handle_root_mcp(json.loads(req['body']),bearer_token=req['headers']['Authorization'][7:],client_ip=req['headers'].get('X-Test-IP','fixture'))
            status=200
        elif req['path'].startswith('/api/auth/me'):
            body={'user':server._current_account(req['headers']['Authorization'][7:]),'bindings':[]}
            status=200
        elif req['path'].startswith('/api/feedback/') or req['path'].startswith('/api/admin/feedback/'):
            handler=object.__new__(server.CedarToyHandler)
            handler.path=req['path']
            raw=req.get('body','').encode()
            handler.headers={k.title():v for k,v in req.get('headers',{}).items()}
            handler.headers['Content-Length']=str(len(raw))
            handler.rfile=io.BytesIO(raw)
            handler._client_ip=Mock(return_value=handler.headers.get('X-Test-Ip','fixture'))
            handler._send_json=Mock()
            with patch.object(server,'_check_request_rate_limit',return_value=True): handler.do_POST()
            response=handler._send_json.call_args
            body,status=response.args[0],response.kwargs.get('status',200)
        else: body,status={},200
        print(json.dumps({'id':req['id'],'status':status,'body':body}),flush=True)
finally: case.doCleanups()
`;
(async () => {
  const child = spawn("python3", ["-u", "-c", python], {cwd: root, stdio: ["pipe", "pipe", "pipe"]});
  const pending = new Map(); let buffer = "", sequence = 0, stderr = "", ready, rejectReady, browser;
  const initialized = new Promise((resolve, reject) => { ready = resolve; rejectReady = reject; });
  child.stderr.on("data", chunk => stderr += chunk);
  child.on("exit", code => { if (code) { const error = new Error(stderr); rejectReady(error); for (const p of pending.values()) p.reject(error); } });
  child.stdout.on("data", chunk => {
    buffer += chunk;
    while (buffer.includes("\n")) {
      const end = buffer.indexOf("\n"), result = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
      if (!result.id) ready(result); else { pending.get(result.id).resolve(result); pending.delete(result.id); }
    }
  });
  const backend = request => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, {resolve, reject}); child.stdin.write(JSON.stringify({id, ...request}) + "\n");
  });
  try {
    const fixture = await initialized;
    const headers = who => ({"Content-Type": "application/json", Authorization: `Bearer ${fixture[who]}`, "X-Test-IP": who});
    const mcp = async (action, params = {}) => {
      const response = await backend({path: "/mcp", headers: headers("ai"), body: JSON.stringify({jsonrpc: "2.0", id: 1, method: "tools/call", params: {name: "feedback", arguments: {action, params}}})});
      assert.equal(response.body.result.isError, false, JSON.stringify(response.body));
      return JSON.parse(response.body.result.content[0].text);
    };
    const http = async (op, body = {}, who = "human") => {
      const response = await backend({path: `/api/feedback/${op}`, headers: headers(who), body: JSON.stringify(body)});
      assert.equal(response.status, 200, JSON.stringify(response)); return response.body;
    };
    if (!process.env.FEEDBACK_DOM_ONLY) {
      const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
      browser = await chromium.launch({headless: true, args: ["--no-sandbox"], ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    }
    for (const width of (browser ? [360, 430, 1280] : [0])) {
      const errors = []; let driver, context, window;
      if (browser) {
        context = await browser.newContext({viewport: {width, height: 900}});
        await context.addInitScript(token => localStorage.setItem("cedartoy_token", token), fixture.human);
        await context.route("**/*", async route => {
          const req = route.request(), url = new URL(req.url());
          if (url.origin !== "http://feedback.test") return route.abort();
          if (url.pathname === "/") return route.fulfill({contentType: "text/html", body: fs.readFileSync(path.join(root, "index.html"), "utf8")});
          const response = await backend({path: url.pathname, headers: Object.fromEntries(Object.entries(req.headers()).map(([k,v]) => [k.toLowerCase() === "authorization" ? "Authorization" : k,v])), body: req.postData() || ""});
          return route.fulfill({status: response.status, json: response.body});
        });
        driver = await context.newPage(); driver.on("pageerror", e => errors.push(e.message));
        driver.on("dialog", dialog => dialog.accept()); await driver.goto("http://feedback.test");
      } else {
        const {JSDOM, VirtualConsole} = require("jsdom");
        const virtualConsole = new VirtualConsole(); virtualConsole.on("jsdomError", e => errors.push(e.message));
        window = new JSDOM(fs.readFileSync(path.join(root, "index.html"), "utf8"), {
          url: "http://feedback.test", runScripts: "dangerously", pretendToBeVisual: true, virtualConsole,
          beforeParse(w) {
            w.localStorage.setItem("cedartoy_token", fixture.human); w.confirm = () => true;
            w.matchMedia = query => ({media: query, matches: false, addEventListener() {}, removeEventListener() {}});
            w.fetch = async (url, options = {}) => {
              const response = await backend({path: url, headers: options.headers || {}, body: options.body || ""});
              return {ok: response.status < 400, status: response.status, json: async () => response.body};
            };
          },
        }).window;
        driver = {evaluate: (fn, arg) => window.eval(`(${fn.toString()})(${JSON.stringify(arg)})`)};
      }
      const evaluate = (fn, arg) => driver.evaluate(fn, arg);
      const click = async selector => browser ? driver.locator(selector).click() : evaluate(s => document.querySelector(s).click(), selector);
      const until = async fn => {
        for (let i = 0; i < 300; i++) { if (await evaluate(fn)) return; await new Promise(r => setTimeout(r, 10)); }
        throw new Error("UI did not settle: " + errors.join("; "));
      };
      const idle = () => until(() => !feedbackBusy);
      const badge = () => evaluate(() => !document.getElementById("feedbackUnreadDot").hidden);
      await evaluate(() => refreshFeedbackUnread());
      const tid = (await mcp("submit", {category: "bug", content: `小机共享工单 ${width}`})).ticket_id;
      await evaluate(() => refreshFeedbackUnread()); assert.equal(await badge(), true);
      await click('[data-bottom-action="feedback"]');
      assert.match(await evaluate(() => document.getElementById("feedbackIdentity").textContent), /直接绑定/);
      await click('[data-feedback-mode="list"]'); await idle();
      await evaluate(subject => [...document.querySelectorAll("#feedbackList button")].find(b => b.textContent.includes(subject)).click(), `小机共享工单 ${width}`);
      await idle(); assert.equal(await badge(), false);
      assert.match(await evaluate(() => document.getElementById("feedbackMessages").textContent), /小机留言/);
      await evaluate(() => { document.getElementById("feedbackFollowupContent").value = "人类回复 <img src=x onerror=alert(1)>"; document.getElementById("feedbackFollowupForm").requestSubmit(); });
      await idle();
      const newMessages = await mcp("detail", {ticket_id: tid});
      assert.equal(newMessages.messages.length, 1); assert.equal(newMessages.messages[0].actor_kind, "human");
      await mcp("reply", {ticket_id: tid, content: "小机再补充"});
      await evaluate(() => refreshFeedbackUnread()); assert.equal(await badge(), true);
      await evaluate(() => closeModals()); await click('[data-bottom-action="feedback"]');
      await click('[data-feedback-mode="list"]'); await idle();
      await evaluate(subject => [...document.querySelectorAll("#feedbackList button")].find(b => b.textContent.includes(subject)).click(), `小机共享工单 ${width}`);
      await idle(); assert.equal(await badge(), false);
      assert.equal(await evaluate(() => document.querySelectorAll("#feedbackDetail img").length), 0);
      await click("#feedbackCloseTicket"); await idle();
      assert.equal((await http("detail", {ticket_id: tid})).ticket.status, "closed");
      const humanTid = (await http("submit", {category: "suggestion", content: `人类共享工单 ${width}`})).ticket_id;
      assert.ok((await mcp("list", {unread_only: true})).tickets.some(t => t.id === humanTid));
      assert.equal((await mcp("detail", {ticket_id: humanTid})).messages[0].actor_kind, "human");
      assert.equal((await http("unread")).unread_count, 0);
      if (browser) {
        assert.equal(await evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (process.env.SCREENSHOT_DIR) {
          fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
          await driver.screenshot({path: path.join(process.env.SCREENSHOT_DIR, `feedback-shared-${width}.png`)});
        }
        await driver.keyboard.press("Escape");
        assert.equal(await driver.locator("#feedbackModal").isVisible(), false);
        await context.close();
      } else window.close();
      assert.deepEqual(errors, []);
      console.log(`${browser ? width + "px browser" : "DOM only"}: shared submit/reply/close, speaker kind, independent unread and XSS passed`);
    }
  } finally {
    if (browser) await browser.close();
    child.stdin.end();
    await new Promise(resolve => child.exitCode !== null ? resolve() : child.once("exit", resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
