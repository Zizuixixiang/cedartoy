"use strict";
// Run via FEEDBACK_CLOSE_ONLY=1 node scripts/check_feedback_browser.js.
// All requests use that runner's disposable account DB and real HTTP handlers.
const assert = require("node:assert/strict");
const path = require("node:path");

exports.run = async ({page, backend, fixture, width, screenshots}) => {
  const base = "http://feedback.test";
  const close = page.locator("#feedbackCloseTicket");
  const modalClose = page.locator("#feedbackModal [data-close-modal]");
  const status = page.locator("#feedbackDetail .feedback-status");
  const draft = page.locator("#feedbackFollowupContent");
  const idle = () => page.waitForFunction(() => !feedbackBusy);
  const calls = [], dialogs = [];
  page.on("request", req => {
    if (/\/api\/feedback\/(close|message)$/.test(req.url())) calls.push({url: req.url(), body: req.postDataJSON()});
  });
  const count = op => calls.filter(c => c.url.endsWith("/" + op)).length;
  const api = async (operation, body, token = fixture.human, admin = false) => {
    const response = await backend({path: `/api/${admin ? "admin/" : ""}feedback/${operation}`, method: "POST",
      headers: {"Content-Type": "application/json", ...(token ? {Authorization: "Bearer " + token} : {})}, body: JSON.stringify(body)});
    assert.equal(response.status, 200); return response.body;
  };
  const shot = async name => {
    await modalClose.scrollIntoViewIfNeeded();
    if (screenshots) await page.screenshot({path: path.join(screenshots, `feedback-close-${name}-${width}.png`)});
  };
  const confirm = async (accept, keyboard = false) => {
    page.once("dialog", async dialog => {
      assert.equal(dialog.type(), "confirm");
      assert.match(dialog.message(), /确定结束.*历史消息会保留.*追问会重新打开/);
      dialogs.push({type: dialog.type(), message: dialog.message(), accepted: accept});
      await (accept ? dialog.accept() : dialog.dismiss());
    });
    if (keyboard) { await close.focus(); await page.keyboard.press("Enter"); }
    else await close.click();
  };
  await page.goto(base);
  await page.evaluate(token => localStorage.setItem("cedartoy_token", token), fixture.human);
  await page.locator('[data-bottom-action="feedback"]').click();
  assert.equal(await close.isVisible(), false, "no ticket selected");
  await page.locator('[data-feedback-mode="list"]').click(); await idle();
  assert.equal(await close.isVisible(), false, "list has no close-ticket action");
  await page.locator("#feedbackList button").first().click(); await idle();
  const tid = await page.evaluate(() => feedbackConversation.ticket.id);
  assert.equal(await status.getAttribute("data-status"), "pending");
  assert.equal(await close.isEnabled(), true);
  await close.scrollIntoViewIfNeeded();
  const geometry = await page.evaluate(() => {
    const measure = el => {
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      return {x: rect.x, y: rect.y, width: rect.width, height: rect.height, font: style.font,
        lineHeight: style.lineHeight, padding: style.padding, border: style.border, shadow: style.boxShadow};
    };
    const button = document.getElementById("feedbackCloseTicket"), row = button.parentElement;
    const box = document.querySelector("#feedbackModal .modal-box");
    return {end: measure(button), close: measure(row.querySelector("[data-close-modal]")),
      followup: measure(document.querySelector("#feedbackFollowupForm button")),
      outer: row.parentElement === box && row === box.lastElementChild,
      form: button.form, type: button.type, rowClass: row.className,
      belowDetail: row.getBoundingClientRect().top >= document.getElementById("feedbackDetail").getBoundingClientRect().bottom,
      gap: getComputedStyle(row).gap, overflow: box.scrollWidth > box.clientWidth,
      pageOverflow: document.documentElement.scrollWidth > innerWidth};
  });
  assert.equal(geometry.outer, true); assert.equal(geometry.form, null); assert.equal(geometry.type, "button");
  assert.equal(geometry.belowDetail, true); assert.match(geometry.rowClass, /modal-actions feedback-actions/);
  assert.ok(geometry.end.x < geometry.close.x);
  for (const key of ["y", "width", "height"]) assert.ok(Math.abs(geometry.end[key] - geometry.close[key]) < 1);
  for (const key of ["font", "padding", "border", "shadow"]) assert.equal(geometry.end[key], geometry.close[key]);
  assert.ok(geometry.end.height >= 44); assert.equal(geometry.overflow, false); assert.equal(geometry.pageOverflow, false);
  await shot("pending");
  await draft.fill("保留未发送的追问草稿");
  const history = await page.locator("#feedbackMessages").textContent();
  await confirm(false, true); await idle();
  assert.equal(count("close"), 0); assert.equal(count("message"), 0);
  assert.equal(await status.getAttribute("data-status"), "pending");
  assert.equal(await draft.inputValue(), "保留未发送的追问草稿");
  assert.equal((await api("detail", {ticket_id: tid})).ticket.status, "pending");

  // A rejected request must preserve status, history and draft and allow retry.
  await page.route("**/api/feedback/close", route => route.fulfill({status: 503, json: {error: "测试：暂时不可用"}}));
  await confirm(true); await idle();
  assert.match(await page.locator("#feedbackMessage").textContent(), /暂时不可用/);
  assert.equal(await status.getAttribute("data-status"), "pending");
  assert.equal(await close.isEnabled(), true);
  assert.equal(await page.locator("#feedbackMessages").textContent(), history);
  await page.unroute("**/api/feedback/close");

  // Hold the real request before dispatch to prove there is no optimistic status update.
  let release, reached;
  const held = new Promise(resolve => {release = resolve;});
  const arrived = new Promise(resolve => {reached = resolve;});
  await page.route("**/api/feedback/close", async route => {reached(); await held; await route.fallback();});
  await confirm(true); await arrived;
  assert.equal(await close.isDisabled(), true);
  assert.equal(await status.getAttribute("data-status"), "pending");
  assert.equal(count("message"), 0);
  release(); await idle(); await page.unroute("**/api/feedback/close");
  assert.equal(await status.getAttribute("data-status"), "closed");
  assert.equal(await close.isDisabled(), true);
  assert.equal(await draft.inputValue(), "保留未发送的追问草稿");
  assert.equal(await page.locator("#feedbackMessages").textContent(), history);
  await shot("closed");
  const sent = count("close");
  await close.evaluate(el => el.click()); await idle();
  assert.equal(count("close"), sent, "closed cannot submit twice");
  assert.equal(calls.filter(c => c.url.endsWith("/close")).every(c => c.body.confirm === true), true);
  await page.getByRole("button", {name: "收起对话", exact: true}).click(); await idle();
  assert.equal(await close.isVisible(), false);
  assert.equal(await page.locator("#feedbackList button").first().locator(".feedback-status").getAttribute("data-status"), "closed");
  await shot("list");
  await page.locator("#feedbackList button").first().click(); await idle();
  await draft.fill("结束后继续追问");
  await page.locator('#feedbackFollowupForm button[type="submit"]').click(); await idle();
  assert.equal(await status.getAttribute("data-status"), "pending"); assert.equal(await close.isEnabled(), true);
  await shot("reopened");
  await api("review", {ticket_id: tid, confirm: true, reply: "临时工单管理员答复"}, fixture.admin, true);
  await page.getByRole("button", {name: "收起对话", exact: true}).click(); await idle();
  await page.locator("#feedbackList button").first().click(); await idle();
  assert.equal(await status.getAttribute("data-status"), "processing");
  await shot("processing");
  await confirm(true); await idle();
  assert.equal(await status.getAttribute("data-status"), "closed");
  assert.equal((await api("detail", {ticket_id: tid})).ticket.status, "closed");

  // Anonymous code follows the same UI and exact-ticket capability path.
  const anonymous = await api("submit", {category: "other", content: "临时匿名工单"}, null);
  await page.keyboard.press("Escape");
  await page.evaluate(() => localStorage.removeItem("cedartoy_token"));
  await page.locator('[data-bottom-action="feedback"]').click();
  await page.locator('[data-feedback-mode="query"]').click();
  assert.equal(await close.isVisible(), false);
  await page.locator("#feedbackQueryCode").fill(anonymous.query_code);
  await page.locator("#feedbackQueryForm button").click(); await idle();
  await confirm(true); await idle();
  assert.equal(await status.getAttribute("data-status"), "closed");
  assert.equal(calls.at(-1).body.ticket_id, anonymous.ticket_id);
  assert.equal(calls.at(-1).body.query_code, anonymous.query_code);
  await page.locator("#feedbackQueryCode").fill("x".repeat(43));
  assert.equal(await close.isVisible(), false, "query changes clear close target");
  await page.locator("#feedbackQueryForm button").click(); await idle();
  assert.equal(await close.isVisible(), false);
  await page.keyboard.press("Escape");
  console.log(`${width}px PASS: outer-left geometry=${JSON.stringify(geometry)}; native confirmations=${JSON.stringify(dialogs)}; cancel/no request, failure/retry, slow success, draft/history, disabled repeat, list update, pending/processing close, followup reopen, anonymous code.`);
};
