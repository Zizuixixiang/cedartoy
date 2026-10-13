"use strict";
// Isolated rendered regression: intercepted fixtures only, no server or database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const chromium = process.env.FEEDBACK_DOM_ONLY ? null : require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium;
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const statuses = ['pending', 'processing', 'closed'];
const labels = ['待处理', '处理中', '已结束'];
const tickets = statuses.map((status, i) => ({id: i + 1, status, category: 'bug',
  preview: i === 1 ? '很长的反馈内容用于检查手机上的摘要换行与状态辨认。'.repeat(8) : '反馈列表显示问题',
  message_count: i + 1, created_at_epoch: 1700000000, updated_at_epoch: 1700000100}));
const luminance = rgb => rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map(n => {
  n /= 255; return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4;
}).reduce((sum, n, i) => sum + n * [.2126, .7152, .0722][i], 0);
async function checkDOM() {
  const {JSDOM, VirtualConsole} = require('jsdom');
  const errors = [], vc = new VirtualConsole();
  vc.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, {url: 'http://feedback.test', runScripts: 'dangerously',
    pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
      w.fetch = async (url, options = {}) => {
        let data = {announcements: [], unread_count: 0};
        if (url === '/api/feedback/list') data = {tickets, total: tickets.length};
        if (url === '/api/feedback/detail') {
          const ticket = tickets.find(t => t.id === JSON.parse(options.body).ticket_id);
          data = {ticket, message_count: ticket.message_count, messages: [], next_before_id: null};
        }
        return {ok: true, json: async () => data};
      };
    }});
  try {
    const w = dom.window, d = w.document;
    w.localStorage.setItem('cedartoy_token', 'fixture-only'); w.openFeedback();
    const done = async () => {
      for (let i = 0; i < 100; i++) {
        if (!w.eval('feedbackBusy')) return;
        await new Promise(resolve => setTimeout(resolve, 5));
      }
      throw new Error('Feedback request did not finish');
    };
    for (let i = 0; i < 3; i++) {
      d.querySelector('[data-feedback-mode="list"]').click(); await done();
      const card = d.querySelectorAll('#feedbackList button')[i];
      assert.equal(card.type, 'button');
      assert.equal(card.querySelector('.feedback-status > span:last-child').textContent, labels[i]);
      assert.equal(card.querySelector('.feedback-status > span').getAttribute('aria-hidden'), 'true');
      const meta = card.querySelector('.feedback-message-meta').textContent;
      assert.match(meta, new RegExp(`${i + 1} 条消息.*2023`));
      assert.doesNotMatch(meta, /待处理|处理中|已结束/);
      card.click(); await done();
      const title = d.querySelector('#feedbackDetail h3');
      assert.equal(title.querySelectorAll('.feedback-status').length, 1);
      assert.equal(title.textContent.split(labels[i]).length - 1, 1);
      assert.match(d.querySelector('#feedbackDetail > .feedback-message-meta').textContent, /条消息.*更新于.*2023/);
      assert.equal(d.activeElement.id, 'feedbackDetail');
    }
    for (const status of ['__proto__', '<img src=x onerror="window.feedbackXss=1">']) {
      tickets[0].status = status; tickets[0].preview = '<img src=x onerror="window.feedbackXss=1">';
      d.querySelector('[data-feedback-mode="list"]').click(); await done();
      assert.match(d.querySelector('#feedbackList').textContent, /<img src=x.*状态未知/);
      d.querySelector('#feedbackList button').click(); await done();
      assert.match(d.querySelector('#feedbackDetail h3').textContent, /状态未知/);
      assert.equal(d.querySelectorAll('#feedbackModal img').length, 0);
      assert.equal(w.feedbackXss, undefined);
    }
    assert.deepEqual(errors, []);
    console.log('DOM PASS: three statuses in list/detail, one status per heading, metadata retained, text-only output, unknown status, detail focus. Rendered layout NOT verified.');
  } finally { dom.window.close(); }
}
(async () => {
  if (process.env.FEEDBACK_DOM_ONLY) return checkDOM();
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
    executablePath: process.env.CHROMIUM_PATH || process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  try {
    const out = process.env.SCREENSHOT_DIR;
    if (out) fs.mkdirSync(out, {recursive: true});
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, locale: 'zh-CN'});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const req = route.request(), url = new URL(req.url());
        if (url.origin !== 'http://feedback.test') return route.abort();
        if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
        let data = {announcements: [], unread_count: 0};
        if (url.pathname === '/api/feedback/list') data = {tickets, total: tickets.length};
        if (url.pathname === '/api/feedback/detail') {
          const ticket = tickets.find(t => t.id === req.postDataJSON().ticket_id);
          data = {ticket, message_count: ticket.message_count, next_before_id: null,
            messages: [{id: 1, role: 'player', content: ticket.preview, created_at_epoch: ticket.created_at_epoch}]};
        }
        return route.fulfill({json: data});
      });
      const idle = () => page.waitForFunction(() => !feedbackBusy);
      const screenshot = async name => { if (out) await page.screenshot({path: path.join(out, `${width}-${name}.png`)}); };
      const bounds = async () => {
        assert.equal(await page.locator('#feedbackModal .modal-box').evaluate(el => el.scrollWidth <= el.clientWidth), true);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      };
      await page.goto('http://feedback.test');
      await page.evaluate(() => localStorage.setItem('cedartoy_token', 'fixture-only'));
      await page.locator('[data-bottom-action="feedback"]').click();
      await page.locator('[data-feedback-mode="list"]').click(); await idle();
      const cards = page.locator('#feedbackList button');
      assert.equal(await cards.count(), 3);
      const geometry = [];
      for (let i = 0; i < 3; i++) {
        const card = cards.nth(i), badge = card.locator('.feedback-status');
        assert.equal(await badge.textContent(), `${['◷', '↻', '✓'][i]}${labels[i]}`);
        assert.equal(await badge.locator('[aria-hidden="true"]').count(), 1);
        assert.equal(await card.getAttribute('type'), 'button');
        const meta = card.locator('.feedback-message-meta');
        assert.match(await meta.innerText(), new RegExp(`${i + 1} 条消息.*2023`));
        assert.doesNotMatch(await meta.innerText(), /待处理|处理中|已结束/);
        const b = await badge.boundingBox(), m = await meta.boundingBox(), c = await card.boundingBox();
        assert.ok(m.y >= b.y + b.height, 'metadata stays below and outside status');
        assert.ok(b.x >= c.x && b.x + b.width <= c.x + c.width, 'badge stays within card');
        const style = await badge.evaluate(el => ({color: getComputedStyle(el).color, bg: getComputedStyle(el).backgroundColor}));
        const contrast = (luminance(style.bg) + .05) / (luminance(style.color) + .05);
        assert.ok(contrast >= 4.5, `${labels[i]} contrast ${contrast}`);
        assert.ok(c.height >= 38); geometry.push({status: statuses[i], height: c.height, contrast: +contrast.toFixed(2)});
      }
      await bounds(); await screenshot('list');
      // Native Tab lands on each card; Enter opens it, with a single named status.
      await page.locator('[data-feedback-mode="list"]').focus(); await page.keyboard.press('Tab');
      for (let i = 0; i < 3; i++) {
        if (i) { await cards.first().focus(); for (let n = 0; n < i; n++) await page.keyboard.press('Tab'); }
        assert.equal(await cards.nth(i).evaluate(el => el === document.activeElement && el.matches(':focus-visible')), true);
        assert.equal(await cards.nth(i).evaluate(el => getComputedStyle(el).outlineStyle), 'solid');
        await page.keyboard.press('Enter'); await idle();
        const heading = page.locator('#feedbackDetail h3');
        assert.equal(await heading.locator('.feedback-status').count(), 1);
        assert.equal((await heading.innerText()).split(labels[i]).length - 1, 1);
        assert.match(await page.locator('#feedbackDetail > .feedback-message-meta').innerText(), new RegExp(`${i + 1} 条消息.*更新于.*2023`));
        await bounds(); await screenshot(statuses[i]);
        await page.keyboard.press('Tab');
        assert.equal(await page.locator('#feedbackFollowupContent').evaluate(el => el === document.activeElement), true);
        await page.locator('#feedbackFollowupForm button').last().click(); await idle();
      }
      // Untrusted preview/status values stay text-only; unknown keys cannot hit Object.prototype.
      tickets[0].preview = '<img src=x onerror="window.feedbackXss=1">'; tickets[0].status = '__proto__';
      await page.locator('[data-feedback-mode="list"]').click(); await idle();
      assert.match(await cards.first().innerText(), /<img src=x/);
      assert.match(await cards.first().innerText(), /状态未知/);
      assert.equal(await cards.locator('img').count(), 0);
      await cards.first().click(); await idle();
      assert.match(await page.locator('#feedbackDetail h3').innerText(), /状态未知/);
      assert.equal(await page.locator('#feedbackDetail img').count(), 0);
      assert.equal(await page.evaluate(() => window.feedbackXss), undefined);
      tickets[0].preview = '反馈列表显示问题'; tickets[0].status = 'pending';
      await page.locator('#feedbackFollowupForm button').last().click(); await idle();
      // Larger text must reflow without losing the status or metadata.
      await page.addStyleTag({content: '#feedbackList .feedback-list-item { font-size: 26px; } #feedbackModal .feedback-status { font-size: 24px; } #feedbackModal .feedback-message-meta { font-size: 22px; }'});
      await bounds();
      for (const badge of await cards.locator('.feedback-status').all()) assert.equal(await badge.isVisible(), true);
      await screenshot('enlarged');
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(() => document.activeElement.dataset.bottomAction), 'feedback');
      assert.deepEqual(errors, []);
      console.log(`${width}px PASS: three statuses in list/detail, metadata, contrast, keyboard, XSS, reflow; ${JSON.stringify(geometry)}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
