'use strict';
// Isolated rendered verification; all requests are intercepted, no live API/DB.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {fixture} = require('./check_feedback_actions_ui');
const html = fs.readFileSync(process.env.FEEDBACK_ADMIN_HTML || path.join(__dirname, '../admin.html'), 'utf8');
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
    executablePath: process.env.CHROMIUM_PATH || process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  const out = process.env.SCREENSHOT_DIR;
  if (out) fs.mkdirSync(out, {recursive: true});
  try {
    for (const width of [360, 430, 1280]) {
      const state = fixture(), errors = [];
      const ticket = {id: 1, category: 'bug', status: 'processing', internal_note: '', created_at_epoch: 1700000000};
      let own = {id: 3, role: 'admin', admin_label: '管理员Clio', content: '已检查反馈，正在确认具体问题。'.repeat(5), version: 1,
        can_manage: true, created_at_epoch: 1700000002};
      const conversation = () => ({ticket, message_count: 3, next_before_id: null, messages: [
        {id: 1, role: 'player', content: '玩家反馈和追问永久保留。', created_at_epoch: 1700000000},
        {id: 2, role: 'admin', content: '另一位管理员的回复。', can_manage: false, version: 1, created_at_epoch: 1700000001}, own]});
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 500, locale: 'zh-CN'});
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => dialog.accept());
      await page.addInitScript(() => localStorage.setItem('cedartoy_token', 'admin-fixture'));
      await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin !== 'http://feedback.test') return route.abort();
        if (url.pathname === '/admin') return route.fulfill({contentType: 'text/html', body: html});
        const body = request.postData() ? request.postDataJSON() : {};
        let result;
        if (url.pathname === '/api/admin/feedback/detail') {
          result = body.message_id ? {history: [{version: 1, action: 'original', content: '原始回复', admin_label: '管理员Clio', created_at_epoch: 1700000002}], next_before_version: null} : conversation();
        } else if (url.pathname === '/api/admin/feedback/edit' || url.pathname === '/api/admin/feedback/withdraw') {
          assert.equal(body.expected_version, own.version);
          assert.equal(body.confirm, true);
          const withdrawn = url.pathname.endsWith('/withdraw');
          own = {...own, content: withdrawn ? '此回复已撤回' : body.content, version: own.version + 1,
            can_manage: !withdrawn, edited: !withdrawn, withdrawn};
          result = {ok: true, message: own};
        } else {
          result = await (await state.fetch(url.pathname + url.search, {body: JSON.stringify(body)})).json();
        }
        return route.fulfill({json: result});
      });
      const idle = () => page.waitForFunction(() => !feedbackAdminBusy);
      const message = page.locator('#feedbackAdminMessages .feedback-message').last();
      const capture = async name => {
        const dimensions = await page.locator('#feedbackQueue').evaluate(el => {
          const body = getComputedStyle(el), meta = getComputedStyle(el.querySelector('.feedback-message-meta'));
          return {width: el.clientWidth, scrollWidth: el.scrollWidth, bodyFont: body.fontSize, metaFont: meta.fontSize,
            targets: [...el.querySelectorAll('.feedback-message-actions button')].filter(el => el.getClientRects().length)
              .map(el => ({text: el.textContent, width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height}))};
        });
        assert.ok(dimensions.scrollWidth <= dimensions.width);
        assert.ok(parseFloat(dimensions.metaFont) <= parseFloat(dimensions.bodyFont));
        assert.ok(dimensions.targets.every(target => target.width >= 24 && target.height >= 36));
        console.log(`${width}px ${name}: ${JSON.stringify(dimensions)}`);
        if (out) await page.screenshot({path: path.join(out, `feedback-message-${width}-${name}.png`), fullPage: true});
      };
      await page.goto('http://feedback.test/admin');
      await page.locator('#recoveryTab').click();
      await page.locator('[data-ticket-kind="feedback"]').click(); await idle();
      await page.locator('#feedbackAdminTickets > button').first().click(); await idle();
      assert.equal(await page.locator('.feedback-message-actions button').count(), 2);
      await capture('reply');
      await page.locator('#feedbackAdminReply').fill('保留追加草稿');
      await message.getByRole('button', {name: '编辑', exact: true}).click();
      const input = message.locator('textarea');
      await input.fill('修改后长正文'.repeat(70)); await capture('editing');
      await page.keyboard.press('Escape');
      assert.equal(await input.count(), 0);
      await page.keyboard.press('Enter');
      await input.fill('更正后的回复');
      await message.getByRole('button', {name: '保存修改', exact: true}).click(); await idle();
      assert.match(await message.innerText(), /已编辑/);
      assert.equal(await page.locator('#feedbackAdminReply').inputValue(), '保留追加草稿');
      await message.locator('.feedback-audit summary').click(); await idle();
      await message.locator('.feedback-audit-record').waitFor(); await capture('audit');
      await message.getByRole('button', {name: '撤回', exact: true}).click(); await idle();
      assert.match(await message.innerText(), /已撤回/);
      assert.equal(await message.locator('.feedback-message-actions').count(), 0);
      await capture('withdrawn');
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
