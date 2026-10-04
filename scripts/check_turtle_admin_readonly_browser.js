/* Local build + intercepted fixtures only; never joins a live room. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const build = process.env.SOUP_UI_BUILD || '/tmp/soup-admin-readonly-review/build';
const output = process.env.SOUP_UI_SCREENSHOTS || '/tmp/soup-admin-readonly-review/screenshots';
fs.mkdirSync(output, {recursive: true});
const player = {id: 7, user_id: 201, username: '南杉', is_guest: false, is_admin: true};
const baseRoom = {
  id: 'LOCK1234', title: '电梯与雨伞', surface: '下雨天，他终于能直接回家。为什么？', tags: '本格',
  status: 'playing', is_locked: 1, active_players: 0, ask_count: 100, created_by: 1,
  created_at: '2026-10-01 12:00:00', admin_readonly: true,
  logs: [
    {id: 1, type: 'ask', content: '他住在高层吗？', judgment: 'yes', username: '房主', player_id: 1},
    {id: 2, type: 'auto_hint', content: '留意雨伞的用途。'},
    {id: 3, type: 'hint_offer', hint_text: '电梯是关键。', resolved: 0, player_id: 7},
  ],
};
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const measurements = [];
    for (const width of [360, 428, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600, reducedMotion: 'reduce'});
      const errors = [], writes = [], sseRequests = [];
      let finished = false, fail = false;
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        localStorage.setItem('cedartoy_user_id', '201');
        localStorage.setItem('cedartoy_token', 'platform-fixture');
        localStorage.setItem('hint_decisions_LOCK1234', JSON.stringify({2: 'reject'}));
        window.streams = [];
        window.EventSource = class {
          constructor(url) { this.url = url; this.closed = false; window.streams.push(this); }
          addEventListener() {}
          close() { this.closed = true; }
        };
      });
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.host !== 'soup.test') return route.abort();
        if (url.pathname.startsWith('/soup/api/')) {
          const endpoint = url.pathname.slice('/soup/api'.length);
          let json = {}, status = 200;
          if (req.method() !== 'GET' && endpoint !== '/auth/guest') writes.push(endpoint);
          if (endpoint.startsWith('/sse/')) sseRequests.push(endpoint);
          if (endpoint === '/auth/guest') json = {token: 'soup-fixture', player};
          else if (endpoint === '/auth/me') json = {player};
          else if (endpoint === '/rooms/') json = [baseRoom];
          else if (endpoint === '/game/public-settings') json = {answer_reveal_prompt_count: 100};
          else if (endpoint === '/puzzles/public') json = [];
          else if (endpoint.startsWith('/rooms/')) {
            const id = endpoint.slice('/rooms/'.length);
            if (fail) { status = 403; json = {detail: '这是锁房，仅限创建者本人和同一绑定关系的小机进入'}; }
            else json = {...baseRoom, id, status: finished ? 'finished' : 'playing', admin_readonly: id !== 'MEMBER',
              ...(id === 'MEMBER' ? {ask_count: 0, logs: [], notes: [], created_by: player.id} : {})};
          }
          return route.fulfill({status, json});
        }
        const relative = url.pathname.startsWith('/soup/assets/') ? url.pathname.slice('/soup/'.length) : 'index.html';
        return route.fulfill({body: fs.readFileSync(path.join(build, relative)), contentType: relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html'});
      });
      const checkReadonly = async () => {
        await page.getByRole('status').filter({hasText: '管理员只读查看'}).waitFor();
        assert.equal(await page.locator('.room-composer, .notepad-drawer, .notepad-drawer-tab, .hint-actions, .log-hint-label-note, .answer-reveal-prompt, .room-close-backdrop, .close-room-btn').count(), 0);
        assert.equal(await page.getByRole('textbox').count(), 0);
        assert.equal(await page.locator('.room-play').getByRole('button').count(), 0);
        assert.match(await page.locator('.session-log-stream').innerText(), /他住在高层吗/);
        assert.match(await page.locator('.session-log-stream').innerText(), /留意雨伞/);
        assert.match(await page.locator('.surface-head-meta').innerText(), /在房 0/);
        assert.equal(await page.evaluate(() => localStorage.getItem('answer_reveal_prompt_last_LOCK1234')), null);
        assert.equal(await page.evaluate(() => localStorage.getItem('hint_decisions_LOCK1234')), '{"2":"reject"}');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      };
      await page.goto('http://soup.test/soup/');
      await page.locator('.pixel-room-card').first().click();
      await checkReadonly();
      assert.equal(await page.evaluate(() => window.streams.length), 0);
      measurements.push(await page.evaluate(() => {
        const notice = document.querySelector('.room-readonly-notice'), surface = document.querySelector('.colored-surface') || document.querySelector('.room-surface-panel p');
        const style = getComputedStyle(notice);
        return {width: innerWidth, noticeFont: style.fontSize, noticeLineHeight: style.lineHeight, surfaceFont: getComputedStyle(surface).fontSize,
          logHeight: document.querySelector('.session-log-panel').getBoundingClientRect().height};
      }));
      assert.ok(parseFloat(measurements.at(-1).noticeFont) <= parseFloat(measurements.at(-1).surfaceFont));
      await page.screenshot({path: path.join(output, `admin-readonly-${width}.png`), fullPage: true});
      if (width < 600) {
        await page.getByRole('button', {name: '收起汤面'}).focus();
        await page.keyboard.press('Enter');
        await page.getByRole('button', {name: '展开汤面'}).click();
      }
      // Finished logs must not write hint expiry or enable any player controls.
      finished = true;
      await page.reload();
      await checkReadonly();
      assert.equal(await page.evaluate(() => window.streams.length), 0);
      // Navigation reuses Room: old member state must never open a readonly SSE.
      finished = false;
      await page.goto('http://soup.test/soup/room/MEMBER');
      await page.locator('.room-composer').waitFor();
      await page.waitForFunction(() => window.streams.some(stream => !stream.closed));
      assert.ok(await page.locator('.notepad-drawer-tab').isVisible());
      await page.evaluate(() => { history.pushState({}, '', '/soup/room/LOCK1234'); dispatchEvent(new PopStateEvent('popstate')); });
      await checkReadonly();
      assert.ok(await page.evaluate(() => window.streams.every(stream => stream.closed && stream.url.includes('/MEMBER?'))));
      // Revoked permission gives the existing lock error, no stale controls.
      fail = true;
      await page.reload();
      await page.getByRole('alert').waitFor();
      assert.equal(await page.locator('.room-play').count(), 0);
      assert.equal(await page.evaluate(() => window.streams.length), 0);
      assert.deepEqual(writes, []);
      assert.deepEqual(sseRequests, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(JSON.stringify(measurements, null, 2));
    console.log('turtle admin readonly browser checks passed: lobby entry, no SSE/writes/actions, hints, finished, member transition, revocation');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
