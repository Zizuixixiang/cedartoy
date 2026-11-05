"use strict";
// Intercepted admin page only: no live server, database, credentials or email.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {fixture} = require('./check_feedback_actions_ui');
const html = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8');
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
    executablePath: process.env.CHROMIUM_PATH || process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  try {
    for (const width of [360, 430, 1280]) {
      const state = fixture(), errors = [];
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 500});
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => localStorage.setItem('cedartoy_token', 'admin-stub'));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.origin !== 'http://feedback.test') return route.abort();
        if (url.pathname === '/admin') return route.fulfill({contentType: 'text/html', body: html});
        const response = await state.fetch(url.pathname + url.search, {body: req.postData()});
        await route.fulfill({status: response.status, json: await response.json()});
      });
      const idle = () => page.waitForFunction(() => !feedbackAdminBusy);
      const reviews = () => state.calls.filter(c => c.url === '/api/admin/feedback/review');
      const reply = page.locator('#feedbackAdminReply');
      await page.goto('http://feedback.test/admin');
      await page.locator('#recoveryTab').click();
      await page.locator('[data-ticket-kind="feedback"]').click(); await idle();
      await page.evaluate(() => openAdminFeedback(1));
      await reply.fill('已修好，请再试一次。');
      const geometry = await page.locator('.feedback-admin-actions button').evaluateAll(buttons => buttons.map(el => {
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return {font: s.font, lineHeight: s.lineHeight, border: s.borderWidth, shadow: s.boxShadow,
          padding: s.padding, height: r.height, inViewport: r.left >= 0 && r.right <= innerWidth};
      }));
      geometry.forEach(button => {
        assert.deepEqual(button, geometry[0]);
        assert.ok(button.inViewport && button.height >= 36);
      });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (process.env.SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
        await page.screenshot({path: path.join(process.env.SCREENSHOT_DIR, `feedback-review-${width}.png`), fullPage: true});
      }
      // Failed close retains the draft; retry sends reply and closure in one request.
      state.intercept = url => url.endsWith('/feedback/review')
        ? {status: 500, json: async () => ({error: '模拟失败'})} : null;
      await page.locator('#feedbackAdminClose').click(); await idle();
      assert.equal(await reply.inputValue(), '已修好，请再试一次。');
      state.intercept = null;
      const count = reviews().length;
      await page.locator('#feedbackAdminClose').focus(); await page.keyboard.press('Enter'); await idle();
      assert.equal(reviews().length, count + 1);
      assert.deepEqual(reviews().at(-1).body, {ticket_id: 1, confirm: true, status: 'closed', reply: '已修好，请再试一次。'});
      assert.equal(await reply.inputValue(), '');
      assert.equal(await page.locator('#feedbackAdminClose').isDisabled(), true);
      for (const draft of ['', '  \n ']) {
        state.status = 'pending'; await page.evaluate(() => openAdminFeedback(1));
        await reply.fill(draft);
        const before = reviews().length;
        await page.locator('#feedbackAdminClose').click(); await idle();
        assert.equal(reviews().length, before + 1);
        assert.deepEqual(reviews().at(-1).body, {ticket_id: 1, confirm: true, status: 'closed'});
      }
      state.status = 'pending'; await page.evaluate(() => openAdminFeedback(1));
      await reply.fill('保留的草稿');
      for (const id of ['feedbackAdminSaveNote', 'feedbackAdminProcessing']) {
        await page.locator('#' + id).click(); await idle();
        assert.equal(await reply.inputValue(), '保留的草稿');
        assert.equal('reply' in reviews().at(-1).body, false);
      }
      const before = reviews().length;
      await page.locator('#feedbackAdminReview button[type="submit"]').click(); await idle();
      assert.equal(reviews().length, before + 1);
      assert.deepEqual(reviews().at(-1).body, {ticket_id: 1, confirm: true, reply: '保留的草稿'});
      assert.equal(await reply.inputValue(), '');
      assert.equal(state.status, 'processing');
      assert.deepEqual(errors, []);
      console.log(`${width}px PASS: combined close, empty close, standalone reply, failure/retry, draft preservation; ${JSON.stringify(geometry[0])}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exitCode = 1;});
