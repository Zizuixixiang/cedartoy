/* Local build + intercepted fixtures only: no live rooms, accounts or model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const build = process.env.SOUP_UI_BUILD || '/tmp/soup-ask-quota-review/build';
const output = process.env.SOUP_UI_SCREENSHOTS || '/tmp/soup-ask-quota-review/screenshots';
const message = '今日个人次数已达上限（300 次），0 点重置';
fs.mkdirSync(output, {recursive: true});

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const measurements = [];
    for (const width of [360, 428, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600, reducedMotion: 'reduce'});
      const errors = [], writes = [], alerts = [];
      let used = 299, admin = false, stale = false, modelFailure = false;
      let resetAt = new Date(Date.now() + 86400000).toISOString();
      const quota = () => ({used, limit: admin ? null : 300, reset_at: resetAt});
      const player = () => ({id: 7, username: '测试玩家', is_guest: false, is_admin: admin});
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', async dialog => {alerts.push(dialog.message()); await dialog.dismiss();});
      await page.addInitScript(() => {
        localStorage.setItem('turtle_soup_token', 'quota-fixture');
        window.streams = [];
        window.EventSource = class {
          constructor() {this.handlers = {}; window.streams.push(this);}
          addEventListener(name, fn) {this.handlers[name] = fn;}
          close() {}
        };
      });
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.host !== 'soup.test') return route.abort();
        if (url.pathname.startsWith('/soup/api/')) {
          const endpoint = url.pathname.slice('/soup/api'.length);
          let json = {}, status = 200;
          if (req.method() !== 'GET') writes.push(endpoint);
          if (endpoint === '/auth/me') json = {player: player()};
          else if (endpoint === '/game/ask-quota') json = quota();
          else if (endpoint === '/game/public-settings') json = {answer_reveal_prompt_count: 50};
          else if (endpoint.startsWith('/rooms/')) json = {
            id: 'QUOTA123', title: '电梯与雨伞', surface: '男人每天坐电梯到十楼，再爬楼梯回家。下雨时却能直接回家。为什么？', tags: '日常',
            status: 'playing', created_by: 7, active_players: 2, ask_count: 100,
            ask_quota: quota(), notes: [], manual_hint_count: 0,
            logs: [{id: 1, player_id: 8, username: '另一位玩家', type: 'ask', content: '他住在高层吗？', judgment: 'yes'}],
          };
          else if (['/game/ask', '/game/guess', '/game/hint/request'].includes(endpoint)) {
            if (stale && endpoint === '/game/ask') {used = 300; status = 429; json = {detail: message};}
            else {
              if (endpoint === '/game/ask') used += 1;
              if (modelFailure) {status = 503; json = {detail: '上游暂时不可用'};}
              else json = endpoint === '/game/hint/request'
                ? {log_id: 15, hint_text: '留意雨伞', manual_hint_remaining: 2}
                : {id: 14, player_id: 7, username: '测试玩家', type: endpoint.endsWith('guess') ? 'guess' : 'ask', content: '测试操作', judgment: 'no'};
            }
          } else if (endpoint === '/game/reveal-answer') json = {answer_revealed: true, answer: '雨伞帮助他按到电梯高处的按钮。'};
          return route.fulfill({status, json});
        }
        const relative = url.pathname.startsWith('/soup/assets/') ? url.pathname.slice('/soup/'.length) : 'index.html';
        return route.fulfill({body: fs.readFileSync(path.join(build, relative)), contentType: relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html'});
      });
      const input = page.locator('.composer-row textarea');
      const checkLocked = async () => {
        await page.waitForFunction(() => document.querySelector('.composer-row textarea')?.disabled);
        assert.equal(await input.getAttribute('placeholder'), message);
        assert.equal(await input.inputValue(), '', 'a retained draft must not hide the limit placeholder');
        assert.equal(await input.getAttribute('rows'), '2');
        for (const selector of ['.send-btn', '.hint-request-btn', '.composer-tabs button']) {
          for (const button of await page.locator(selector).all()) assert.ok(await button.isDisabled());
        }
        assert.ok(await page.locator('.notepad-drawer-tab').isEnabled());
        assert.ok(await page.locator('.soup-logout-link').isEnabled());
        assert.match(await page.locator('.session-log-stream').innerText(), /他住在高层吗/);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      };
      const refresh = async () => {
        const response = page.waitForResponse('**/soup/api/game/ask-quota');
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await response;
      };
      await page.goto('http://soup.test/soup/room/QUOTA123');
      await input.waitFor();
      assert.ok(await input.isEnabled());
      // The final allowed ask locks the whole web composer, but not reveal/notes/exit.
      await input.fill('他个子很矮吗？');
      await page.locator('.send-btn').click();
      await checkLocked();
      assert.equal(writes.filter(x => x === '/game/ask').length, 1);
      const geometry = await page.evaluate(() => {
        const input = document.querySelector('.composer-row textarea'), button = document.querySelector('.hint-request-btn');
        const style = getComputedStyle(input), box = input.getBoundingClientRect();
        return {width: innerWidth, inputWidth: box.width, inputHeight: box.height, font: style.fontSize,
          placeholderFont: getComputedStyle(input, '::placeholder').fontSize, lineHeight: style.lineHeight, hintHeight: button.getBoundingClientRect().height,
          logFont: getComputedStyle(document.querySelector('.log-text')).fontSize,
          padding: parseFloat(style.paddingTop) + parseFloat(style.paddingBottom),
          border: parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)};
      });
      assert.ok(geometry.inputHeight >= 2 * parseFloat(geometry.lineHeight) + geometry.padding + geometry.border - 1);
      assert.ok(parseFloat(geometry.placeholderFont) <= parseFloat(geometry.logFont));
      measurements.push(geometry);
      await page.screenshot({path: path.join(output, `quota-${width}.png`), fullPage: true});
      await page.locator('.notepad-drawer-tab').click();
      assert.ok(await page.locator('.notepad-input').isEnabled());
      await page.keyboard.press('Escape');
      // Close using the existing backdrop (the drawer has no Escape handler).
      await page.locator('.notepad-drawer').click({position: {x: 1, y: 1}, force: true});
      await page.getByRole('button', {name: '接受', exact: true}).click();
      assert.ok(await page.getByRole('button', {name: '确认公布', exact: true}).isEnabled());
      await page.getByRole('button', {name: '返回', exact: true}).click();
      // Other players' SSE logs do not change this player's quota.
      used = 12;
      await refresh();
      await page.waitForFunction(() => !document.querySelector('.composer-row textarea').disabled);
      await page.evaluate(() => window.streams.at(-1).handlers.new_log({data: JSON.stringify({id: 20, type: 'ask', player_id: 8, content: '其他玩家的问题'})}));
      assert.ok(await input.isEnabled());
      // Guess and manual hints refresh the ask quota but never consume it.
      for (const action of ['guess', 'hint']) {
        used = 299;
        await refresh();
        const refreshed = page.waitForResponse('**/soup/api/game/ask-quota');
        if (action === 'guess') {
          await page.getByRole('button', {name: '猜测汤底', exact: true}).click();
          await input.fill('他个子矮，下雨时用雨伞按高处的电梯按钮。');
          await page.locator('.send-btn').click();
        } else {
          await page.waitForFunction(() => !document.querySelector('.hint-request-btn').disabled);
          await page.locator('.hint-request-btn').click();
          await page.getByRole('button', {name: '确认请求', exact: true}).click();
        }
        await refreshed;
        assert.equal(used, 299);
        assert.ok(await input.isEnabled());
        assert.ok(await page.locator('.composer-tabs button').first().isEnabled());
      }
      // Another tab/MCP may consume the last action: a 429 updates the inline state.
      used = 299;
      await refresh();
      await page.waitForFunction(() => !document.querySelector('.composer-row textarea').disabled);
      stale = true;
      await page.getByRole('button', {name: '提问', exact: true}).click();
      await input.fill('保留的草稿');
      await page.locator('.send-btn').click();
      await checkLocked();
      assert.deepEqual(alerts, [], 'quota exhaustion uses the input, not an extra alert');
      stale = false;
      // A failed ask also locks the UI after using ask 300.
      used = 299;
      await refresh();
      await page.waitForFunction(() => !document.querySelector('.composer-row textarea').disabled);
      modelFailure = true;
      await input.fill('失败后仍保留的草稿');
      await page.locator('.send-btn').click();
      await checkLocked();
      assert.deepEqual(alerts, ['上游暂时不可用']);
      modelFailure = false;
      // Refresh at the server's reset timestamp, without reloading or local quota guesses.
      await page.clock.install();
      resetAt = new Date(Date.now() + 10000).toISOString();
      await refresh();
      used = 0;
      await page.clock.fastForward(11000);
      await page.waitForFunction(() => !document.querySelector('.composer-row textarea').disabled);
      assert.equal(await input.inputValue(), '失败后仍保留的草稿');
      await input.focus();
      await page.keyboard.press('Tab');
      assert.ok(await page.locator('.send-btn').evaluate(el => el === document.activeElement));
      // Exemption comes from the server, even if the recorded count is 300.
      used = 300; admin = true;
      await refresh();
      assert.ok(await input.isEnabled());
      assert.ok(await page.locator('.composer-tabs button').first().isEnabled());
      // Reloading an already exhausted personal quota also starts locked.
      admin = false;
      await page.evaluate(() => localStorage.removeItem('answer_reveal_prompt_last_QUOTA123'));
      await page.reload();
      await checkLocked();
      await page.getByRole('button', {name: '接受', exact: true}).click();
      await page.getByRole('button', {name: '确认公布', exact: true}).click();
      await page.locator('.private-answer-reveal').waitFor();
      assert.match(await page.locator('.private-answer-reveal').innerText(), /雨伞帮助/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(JSON.stringify(measurements, null, 2));
    console.log('turtle ask quota browser checks passed: ask-only counting, whole-composer lock, 429, failure, reset, admin, personal quota, non-model controls');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
