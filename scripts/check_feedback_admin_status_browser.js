"use strict";
// Isolated browser fixtures; no live service, database, credentials or email.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const chromium = process.env.FEEDBACK_DOM_ONLY ? null : require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium;
const {fixture} = require('./check_feedback_actions_ui');
const html = fs.readFileSync(path.join(__dirname, '../admin.html'), 'utf8');
const statuses = ['pending', 'processing', 'closed'];
const labels = ['待处理', '处理中', '已结束'];
const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number).map(n => {
  n /= 255; return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4;
}).reduce((sum, n, i) => sum + n * [.2126, .7152, .0722][i], 0);

async function checkDOM() {
  const {JSDOM, VirtualConsole} = require('jsdom');
  const state = fixture(), errors = [], vc = new VirtualConsole(), evidence = [];
  vc.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, {url: 'http://feedback.test/admin', runScripts: 'dangerously',
    pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
      w.localStorage.setItem('cedartoy_token', 'admin-fixture'); w.fetch = state.fetch;
    }});
  const w = dom.window, d = w.document, $ = id => d.getElementById(id);
  const idle = async () => {
    for (let i = 0; i < 100; i++) {
      if (!w.eval('feedbackAdminBusy')) return;
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    throw new Error('Feedback request did not finish');
  };
  try {
    for (const [i, status] of statuses.entries()) {
      state.status = status; await w.loadAdminFeedback(); await idle();
      const card = $('feedbackAdminTickets').querySelector('button');
      const badge = card.querySelector('.feedback-status');
      assert.equal(card.querySelectorAll('.feedback-status').length, 1);
      assert.equal(badge.dataset.status, status);
      assert.equal(badge.textContent, `${['◷', '↻', '✓'][i]}${labels[i]}`);
      assert.equal(badge.firstChild.getAttribute('aria-hidden'), 'true');
      assert.doesNotMatch(card.querySelector('.feedback-message-meta').textContent, /待处理|处理中|已结束/);
      if (!i) assert.match(card.textContent, /玩家新留言/);
      card.click(); await idle();
      const heading = $('feedbackAdminDetail').querySelector('.recovery-card-heading');
      assert.equal(heading.querySelector('.feedback-status').dataset.status, status);
      assert.doesNotMatch(heading.querySelector('h3').textContent, /待处理|处理中|已结束/);
      assert.equal($('feedbackAdminClose').disabled, status === 'closed');
      assert.equal(d.activeElement, $('feedbackAdminDetail'));
      evidence.push({status, listBadge: badge.outerHTML, detailHeading: heading.outerHTML});
      $('feedbackAdminBack').click(); await idle();
      $('feedbackAdminFilter').value = status;
      $('feedbackAdminFilter').dispatchEvent(new w.Event('change')); await idle();
      assert.ok(state.calls.some(c => c.url === '/api/admin/feedback/list' && c.body.status === status));
    }
    // Existing review actions must refresh the detail badge along with the state.
    state.status = 'pending'; await w.openAdminFeedback(1); await idle();
    $('feedbackAdminProcessing').click(); await idle();
    assert.equal($('feedbackAdminDetail').querySelector('.feedback-status').dataset.status, 'processing');
    $('feedbackAdminClose').click(); await idle();
    assert.equal($('feedbackAdminDetail').querySelector('.feedback-status').dataset.status, 'closed');
    for (const status of ['__proto__', '<img src=x onerror="window.feedbackXss=1">']) {
      state.status = status; await w.loadAdminFeedback(); await idle();
      assert.match($('feedbackAdminTickets').textContent, /状态未知/);
      await w.openAdminFeedback(1); await idle();
      assert.match($('feedbackAdminDetail').querySelector('.feedback-status').textContent, /状态未知/);
      assert.equal($('feedbackQueue').querySelectorAll('img,script').length, 0);
      assert.equal(w.feedbackXss, undefined);
    }
    assert.deepEqual(errors, []);
    if (process.env.SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive: true});
      fs.writeFileSync(path.join(process.env.SCREENSHOT_DIR, 'dom-only-evidence.json'),
        JSON.stringify({renderedLayoutVerified: false, evidence}, null, 2) + '\n');
    }
    console.log('DOM PASS: three list/detail badges, no duplicated status text, player-message marker, filter requests, review transitions, focus, unknown/XSS. Rendered layout NOT verified.');
  } finally { w.close(); }
}

(async () => {
  if (process.env.FEEDBACK_DOM_ONLY) return checkDOM();
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
    executablePath: process.env.CHROMIUM_PATH || process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  const out = process.env.SCREENSHOT_DIR;
  if (out) fs.mkdirSync(out, {recursive: true});
  const evidence = [];
  try {
    for (const width of [360, 430, 1280]) {
      const state = fixture(), errors = [];
      const tickets = statuses.map((status, i) => ({id: i + 1, status, category: 'bug',
        preview: i === 1 ? '很长的反馈摘要，检查状态标签与正文层级以及移动端换行。'.repeat(8) : '反馈列表显示问题',
        contact: '', internal_note: '', message_count: i + 1, last_message_role: i ? 'admin' : 'player',
        created_at_epoch: 1700000000, updated_at_epoch: 1700000100}));
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 500, locale: 'zh-CN'});
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => localStorage.setItem('cedartoy_token', 'admin-fixture'));
      await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin !== 'http://feedback.test') return route.abort();
        if (url.pathname === '/admin') return route.fulfill({contentType: 'text/html', body: html});
        if (url.pathname === '/api/admin/feedback/list') {
          const {status} = request.postDataJSON();
          const rows = tickets.filter(t => status === 'all' || t.status === status);
          return route.fulfill({json: {tickets: rows, total: rows.length}});
        }
        if (url.pathname === '/api/admin/feedback/detail') {
          const ticket = tickets.find(t => t.id === request.postDataJSON().ticket_id);
          return route.fulfill({json: {ticket, message_count: ticket.message_count, next_before_id: null,
            messages: [{id: 1, role: 'player', content: ticket.preview, created_at_epoch: ticket.created_at_epoch}]}});
        }
        const response = await state.fetch(url.pathname + url.search, {body: request.postData()});
        return route.fulfill({status: response.status, json: await response.json()});
      });
      const idle = () => page.waitForFunction(() => !feedbackAdminBusy);
      const screenshot = async name => {
        if (out) await page.screenshot({path: path.join(out, `${width}-${name}.png`), fullPage: true});
      };
      const bounds = async () => {
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        assert.equal(await page.locator('#feedbackQueue').evaluate(el => el.scrollWidth <= el.clientWidth), true);
      };
      await page.goto('http://feedback.test/admin');
      await page.locator('#recoveryTab').click();
      await page.locator('[data-ticket-kind="feedback"]').click(); await idle();
      const cards = page.locator('#feedbackAdminTickets > button');
      assert.equal(await cards.count(), 3);
      const geometry = [];
      for (let i = 0; i < 3; i++) {
        const card = cards.nth(i), badge = card.locator('.feedback-status');
        assert.equal(await badge.count(), 1);
        assert.equal(await badge.getAttribute('data-status'), statuses[i]);
        assert.equal((await badge.textContent()).replace(/\\s+/g, ''), `${['◷', '↻', '✓'][i]}${labels[i]}`);
        assert.equal(await badge.locator('[aria-hidden="true"]').count(), 1);
        assert.doesNotMatch(await card.locator('.feedback-message-meta').innerText(), /待处理|处理中|已结束/);
        if (!i) assert.match(await card.innerText(), /玩家新留言/);
        const style = await badge.evaluate(el => {
          const s = getComputedStyle(el), b = el.getBoundingClientRect(), c = el.closest('button').getBoundingClientRect();
          return {color: s.color, background: s.backgroundColor, fontSize: s.fontSize, lineHeight: s.lineHeight,
            border: s.borderWidth, radius: s.borderRadius, shadow: s.boxShadow, padding: s.padding,
            contained: b.left >= c.left && b.right <= c.right && b.bottom <= c.bottom, cardHeight: c.height};
        });
        const contrast = (luminance(style.background) + .05) / (luminance(style.color) + .05);
        assert.ok(contrast >= 4.5 && style.contained && style.cardHeight >= 36);
        assert.equal(style.fontSize, '12px');
        geometry.push({status: statuses[i], ...style, contrast: +contrast.toFixed(2)});
      }
      assert.equal(new Set(geometry.map(g => g.background)).size, 3);
      await bounds(); await screenshot('list');
      for (let i = 0; i < 3; i++) {
        await cards.nth(i).focus();
        assert.equal(await cards.nth(i).evaluate(el => document.activeElement === el), true);
        await page.keyboard.press('Enter'); await idle();
        const header = page.locator('#feedbackAdminDetail .recovery-card-header');
        assert.equal(await header.locator('.feedback-status').getAttribute('data-status'), statuses[i]);
        assert.match(await header.locator('.feedback-status').innerText(), new RegExp(labels[i]));
        assert.doesNotMatch(await header.locator('h3').innerText(), /待处理|处理中|已结束/);
        assert.equal(await page.locator('#feedbackAdminClose').isDisabled(), i === 2);
        const buttons = await page.locator('#feedbackAdminDetail button').evaluateAll(elements => elements.map(el => {
          const r = el.getBoundingClientRect();
          return {height: r.height, contained: r.left >= 0 && r.right <= innerWidth};
        }));
        assert.ok(buttons.every(b => b.height >= 36 && b.contained));
        await bounds(); await screenshot(statuses[i]);
        await page.locator('#feedbackAdminBack').click(); await idle();
      }
      for (const status of statuses) {
        await page.locator('#feedbackAdminFilter').selectOption(status); await idle();
        assert.equal(await cards.count(), 1);
        assert.equal(await cards.locator('.feedback-status').getAttribute('data-status'), status);
      }
      await page.locator('#feedbackAdminFilter').selectOption('all'); await idle();
      // Unknown values remain text-only, even in a refreshed detail heading.
      tickets[0].status = '__proto__'; tickets[0].preview = '<img src=x onerror="window.feedbackXss=1">';
      await page.locator('#feedbackAdminRefresh').click(); await idle();
      assert.match(await cards.first().innerText(), /状态未知/);
      await cards.first().click(); await idle();
      assert.match(await page.locator('#feedbackAdminDetail .feedback-status').innerText(), /状态未知/);
      assert.equal(await page.locator('#feedbackQueue img').count(), 0);
      assert.equal(await page.evaluate(() => window.feedbackXss), undefined);
      await page.locator('#feedbackAdminBack').click(); await idle();
      tickets[0].status = 'pending';
      await page.locator('#feedbackAdminRefresh').click(); await idle();
      await page.addStyleTag({content: '#feedbackQueue .feedback-status { font-size: 24px; } #feedbackQueue .feedback-message-meta { font-size: 24px; } #feedbackQueue .feedback-ticket-title { font-size: 28px; }'});
      await bounds(); await screenshot('enlarged');
      await cards.first().click(); await idle(); await bounds();
      assert.deepEqual(errors, []);
      evidence.push({width, horizontalOverflow: false, statuses, detailStatuses: statuses, geometry});
      console.log(`${width}px PASS: list/detail badges, filters, player-message marker, contrast, button sizes, keyboard, XSS, enlarged text, no overflow`);
      await page.close();
    }
    if (out) fs.writeFileSync(path.join(out, 'dom-evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
