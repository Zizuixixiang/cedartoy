"use strict";
// Rendered checks using intercepted in-memory stubs only; never contacts a server or database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {fixture} = require('./check_feedback_actions_ui');
const root = path.resolve(__dirname, '..');
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
    executablePath: process.env.CHROMIUM_PATH || process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  try {
    const out = process.env.SCREENSHOT_DIR;
    if (out) fs.mkdirSync(out, {recursive: true});
    for (const width of [360, 430, 1280]) {
      const state = fixture(), errors = [];
      const page = await browser.newPage({viewport: {width, height: width === 360 ? 780 : 900}, hasTouch: width < 500});
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.origin !== 'http://feedback.test') return route.abort();
        if (['/', '/admin'].includes(url.pathname)) return route.fulfill({contentType: 'text/html', body: fs.readFileSync(path.join(root, url.pathname === '/' ? 'index.html' : 'admin.html'), 'utf8')});
        const response = await state.fetch(url.pathname + url.search, {method: req.method(), body: req.postData(), headers: {Authorization: req.headers().authorization}});
        await route.fulfill({status: response.status, json: await response.json()});
      });
      const close = page.locator('#feedbackModal [data-close-modal]');
      const submit = page.locator('#feedbackSubmit');
      const mode = name => page.locator(`[data-feedback-mode="${name}"]`);
      const idle = () => page.waitForFunction(() => !feedbackBusy);
      const geometry = async () => {
        await submit.scrollIntoViewIfNeeded();
        const result = await page.evaluate(() => {
          const button = el => {
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return {x: r.x, y: r.y, width: r.width, height: r.height, font: s.font, padding: s.padding};
          };
          const box = document.querySelector('#feedbackModal .modal-box');
          return {submit: button(document.getElementById('feedbackSubmit')), close: button(document.querySelector('#feedbackModal [data-close-modal]')),
            overflow: box.scrollWidth > box.clientWidth, pageOverflow: document.documentElement.scrollWidth > innerWidth};
        });
        for (const key of ['y', 'width', 'height']) assert.ok(Math.abs(result.submit[key] - result.close[key]) < 1, `${width}: same ${key}: ${JSON.stringify(result)}`);
        for (const key of ['font', 'padding']) assert.equal(result.submit[key], result.close[key]);
        assert.ok(result.submit.height >= 44); assert.equal(result.overflow, false); assert.equal(result.pageOverflow, false);
        console.log(width + 'px feedback geometry: ' + JSON.stringify(result));
        if (out) await page.screenshot({path: path.join(out, `feedback-actions-${width}.png`)});
      };
      await page.goto('http://feedback.test');
      await page.locator('[data-bottom-action="feedback"]').click();
      assert.equal(await mode('list').isVisible(), false); assert.equal(await mode('query').isVisible(), true);
      assert.equal(await close.count(), 1); await geometry();
      await submit.click(); assert.equal(state.calls.filter(c => c.url === '/api/feedback/submit').length, 0);
      await page.locator('#feedbackContact').focus(); await page.keyboard.press('Tab');
      assert.equal(await submit.evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Tab'); assert.equal(await close.evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Tab'); assert.equal(await mode('submit').evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Shift+Tab'); assert.equal(await close.evaluate(el => el === document.activeElement), true);
      await page.locator('#feedbackContent').fill('匿名反馈');
      await page.locator('#feedbackContact').focus(); await page.keyboard.press('Enter');
      await page.locator('#feedbackReceipt').waitFor({state: 'visible'}); await idle();
      assert.equal(await submit.isVisible(), false); assert.equal(await close.isVisible(), true);
      await page.locator('#feedbackReceiptQuery').click(); await page.locator('#feedbackDetail').waitFor({state: 'visible'}); await idle();
      assert.equal(await submit.isVisible(), false);
      await page.locator('#feedbackFollowupContent').fill('继续追问');
      await page.locator('#feedbackFollowupForm button[type="submit"]').click(); await idle();
      assert.ok(state.calls.some(c => c.url === '/api/feedback/message'));
      await mode('submit').click(); await page.locator('#feedbackNew').click(); await geometry();
      await close.click(); assert.equal(await page.locator('#feedbackModal').isVisible(), false);
      await page.evaluate(() => localStorage.setItem('cedartoy_token', 'human-stub'));
      await page.locator('[data-bottom-action="feedback"]').click();
      assert.equal(await mode('query').isVisible(), false); assert.equal(await mode('list').isVisible(), true);
      await mode('list').click(); await idle(); assert.equal(await submit.isVisible(), false); assert.equal(await close.isVisible(), true);
      await mode('submit').click(); await page.locator('#feedbackContent').fill('登录反馈'); await submit.click(); await idle();
      assert.match(await page.locator('#feedbackMessage').innerText(), /我的反馈/);
      await page.keyboard.press('Escape'); assert.equal(await page.locator('#feedbackModal').isVisible(), false);
      await page.goto('http://feedback.test/admin');
      await page.waitForFunction(() => document.getElementById('recoveryCount').textContent === '1');
      const tab = page.locator('#recoveryTab'), badge = page.locator('#recoveryCount');
      const withBadge = await tab.boundingBox();
      for (const [r, f] of [[0, 1], [2, 3], [0, 0], [99, 1]]) {
        state.recovery = r; state.feedback = f; await page.evaluate(() => loadRecoveryCount());
        assert.equal(await badge.isVisible(), r + f > 0);
        assert.equal(await badge.textContent(), r + f > 99 ? '99+' : String(r + f));
        assert.deepEqual(await tab.boundingBox(), withBadge, 'badge must not alter tab geometry');
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (out) await page.screenshot({path: path.join(out, `admin-ticket-badge-${width}.png`)});
      assert.deepEqual(errors, []); await page.close();
    }
    console.log('Browser PASS: 360/430/1280 equal action geometry, keyboard/Enter/validation, identity modes, QA, badge totals and overflow.');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exitCode = 1;});
