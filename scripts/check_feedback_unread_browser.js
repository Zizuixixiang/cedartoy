"use strict";
// Called by check_feedback_browser.js with its isolated real-handler fixture.
const assert = require("node:assert/strict");
const path = require("node:path");
exports.run = async ({page, backend, fixture, width, screenshots}) => {
  const api = async (operation, body = {}, admin = false) => {
    const response = await backend({path: `/api/${admin ? "admin/" : ""}feedback/${operation}`, method: "POST",
      headers: {"Content-Type": "application/json", Authorization: `Bearer ${admin ? fixture.admin : fixture.human}`}, body: JSON.stringify(body)});
    assert.equal(response.status, 200); return response.body;
  };
  const tickets = (await api("list")).tickets.slice(0, 2).map(t => t.id);
  const review = (id, body) => api("review", {ticket_id: id, confirm: true, ...body}, true);
  const badge = page.locator("#feedbackUnreadDot");
  const check = async visible => {
    await page.waitForFunction(value => document.getElementById("feedbackUnreadDot").hidden === !value, visible);
    assert.equal(await badge.isVisible(), visible);
  };
  const list = async () => {
    await page.locator('[data-bottom-action="feedback"]').click();
    await page.locator('[data-feedback-mode="list"]').click();
    await page.waitForFunction(() => !feedbackBusy);
  };
  const openTicket = async id => {
    const rows = (await api("list")).tickets;
    await page.locator("#feedbackList button").nth(rows.findIndex(t => t.id === id)).click();
    await page.waitForFunction(() => !feedbackBusy);
    assert.match(await page.locator("#feedbackDetail").innerText(), new RegExp(`#${id}`));
  };
  await page.reload(); await page.evaluate(() => refreshFeedbackUnread()); await check(false);
  const originalNav = await page.locator(".bottom-nav").boundingBox();
  await review(tickets[0], {status: "closed", internal_note: "fixture only"});
  await page.evaluate(() => refreshFeedbackUnread()); await check(false);
  for (const id of tickets) await review(id, {reply: "临时测试：管理员新回复"});
  await page.reload(); await check(true);
  assert.deepEqual(await page.locator(".bottom-nav").boundingBox(), originalNav);
  assert.match(await page.locator('[data-bottom-action="feedback"]').getAttribute("aria-label"), /新回复未读/);
  const geometry = await page.evaluate(() => {
    const button = document.querySelector('[data-bottom-action="feedback"]');
    const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}; };
    return {badge: rect(document.getElementById("feedbackUnreadDot")), label:rect(button.querySelector(".nav-label")), button:rect(button), overflow:document.documentElement.scrollWidth > innerWidth};
  });
  assert.equal(geometry.overflow, false);
  assert.ok(geometry.badge.bottom <= geometry.label.y, "Badge cannot cover the label");
  assert.ok(geometry.badge.x >= geometry.button.x && geometry.badge.right <= geometry.button.right);
  assert.ok(geometry.button.height >= 44);
  if (screenshots) {
    await page.screenshot({path:path.join(screenshots, `feedback-unread-${width}.png`), fullPage:true});
    await page.locator(".bottom-nav").screenshot({path:path.join(screenshots, `feedback-unread-nav-${width}.png`)});
  }
  await list(); await check(true);
  assert.equal((await api("unread")).unread_count, 2);
  await openTicket(tickets[0]); await check(true);
  assert.equal((await api("unread")).unread_count, 1);
  await page.keyboard.press("Escape"); await list(); await openTicket(tickets[1]); await check(false);
  await page.keyboard.press("Escape");
  await page.reload(); await page.evaluate(() => refreshFeedbackUnread()); await check(false);
  await review(tickets[0], {reply: "再次回复"});
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange"))); await check(true);
  await page.evaluate(() => { localStorage.removeItem("cedartoy_token"); renderAuth(); }); await check(false);
  await page.evaluate(value => { localStorage.setItem("cedartoy_token", value); renderAuth(); }, fixture.human); await check(true);
  await list(); await openTicket(tickets[0]); await check(false); await page.keyboard.press("Escape");
  if (screenshots) await page.locator(".bottom-nav").screenshot({path:path.join(screenshots, `feedback-read-nav-${width}.png`)});
  console.log(`${width}px: unread badge hidden/reappears; list preserves unread; independent detail reads; reload/login; no label overlap or layout shift`);
};
