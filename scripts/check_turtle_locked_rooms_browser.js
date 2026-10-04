/* Build to /tmp first; this check uses only local assets and intercepted API fixtures.
 * SOUP_UI_BUILD and PLAYWRIGHT_MODULE can point at existing build/install paths.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const build = process.env.SOUP_UI_BUILD || '/tmp/soup-lock-review/build';
const output = process.env.SOUP_UI_SCREENSHOTS || '/tmp/soup-lock-review/screenshots';
fs.mkdirSync(output, {recursive: true});
const puzzle = {id: 1, title: '电梯与雨伞', surface: '下雨天，他终于能直接回家。为什么？', tags: '本格'};
const room = {...puzzle, status: 'playing', active_players: 1, ask_count: 0, created_at: '2026-10-01 12:00:00'};
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const measurements = [];
    for (const width of [360, 428, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      const errors = [];
      const creates = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => {
        localStorage.setItem('cedartoy_user_id', '101');
        localStorage.setItem('cedartoy_token', 'platform-test-token');
      });
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.host !== 'soup.test') return route.abort();
        const endpoint = url.pathname.replace('/soup/api', '');
        if (url.pathname.startsWith('/soup/api/')) {
          let json = {};
          let status = 200;
          if (endpoint === '/auth/guest') {
            assert.equal(route.request().headers().authorization, 'Bearer platform-test-token');
            json = {token: 'soup-test-token', player: {id: 1, username: '自己', user_id: 101, is_guest: false}};
          } else if (endpoint === '/auth/me') json = {player: {id: 1, username: '自己', user_id: 101, is_guest: false}};
          else if (endpoint === '/rooms/') json = [{...room, id: 'LOCK1234', is_locked: 1}, {...room, id: 'OPEN1234', is_locked: 0}];
          else if (endpoint === '/puzzles/public') json = [puzzle];
          else if (endpoint === '/puzzles/random') json = puzzle;
          else if (endpoint === '/game/generate') json = {...puzzle, answer: 'answer'};
          else if (endpoint === '/rooms/create') {
            creates.push(route.request().postDataJSON());
            status = 400; json = {detail: '测试错误：保留选择后可重试'};
          } else if (endpoint.startsWith('/rooms/')) {status = 403; json = {detail: '这是锁房，仅限创建者本人和同一绑定关系的小机进入'};}
          return route.fulfill({status, json});
        }
        const relative = url.pathname.startsWith('/soup/assets/') ? url.pathname.slice('/soup/'.length) : 'index.html';
        const file = path.join(build, relative);
        return route.fulfill({body: fs.readFileSync(file), contentType: relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html'});
      });
      await page.goto('http://soup.test/soup/');
      await page.locator('.pixel-room-card').first().waitFor();
      const cards = page.locator('.pixel-room-card');
      assert.match(await cards.nth(0).locator('.room-code').innerText(), /^🔒 房间 #LOCK1234$/);
      assert.equal(await cards.nth(1).locator('.room-code').innerText(), '房间 #OPEN1234');
      const lockedHeight = (await cards.nth(0).boundingBox()).height;
      assert.equal(lockedHeight, (await cards.nth(1).boundingBox()).height);
      await page.screenshot({path: path.join(output, `lobby-${width}.png`), fullPage: true});
      if (width < 1000) await page.getByRole('button', {name: '创建房间', exact: true}).click();
      const panel = page.locator('.pixel-create:visible');
      const checkbox = panel.getByRole('checkbox', {name: '🔒 仅限自己与小机'});
      assert.equal(await checkbox.isChecked(), false);
      const before = await panel.boundingBox();
      await checkbox.focus(); await page.keyboard.press('Space');
      assert.equal(await checkbox.isChecked(), true);
      assert.deepEqual(await panel.boundingBox(), before);
      const dimensions = await panel.locator('.create-label-row').evaluate(el => {
        const label = el.querySelector('.room-lock-option');
        const title = el.firstElementChild.getBoundingClientRect();
        const box = label.getBoundingClientRect();
        return {row: el.getBoundingClientRect().toJSON(), label: box.toJSON(), title: title.toJSON(), font: getComputedStyle(label).fontSize};
      });
      assert.ok(dimensions.title.right <= dimensions.label.left, 'title and checkbox share one row');
      assert.ok(dimensions.label.right <= width);
      assert.ok(dimensions.label.height >= 24);
      assert.equal(dimensions.font, '12px');
      await panel.getByRole('button', {name: '随机抽题', exact: true}).click();
      await panel.getByRole('button', {name: '创建', exact: true}).click();
      await panel.getByText('测试错误：保留选择后可重试').waitFor();
      assert.equal(creates.at(-1).is_locked, true);
      assert.equal(await checkbox.isChecked(), true);
      await page.screenshot({path: path.join(output, `create-${width}.png`), fullPage: true});
      await checkbox.uncheck();
      await panel.getByRole('button', {name: '创建', exact: true}).click();
      await page.waitForFunction(() => !document.querySelector('.pixel-primary.loading'));
      assert.equal(creates.at(-1).is_locked, false);
      for (const tab of ['自填', 'AI 生成']) {
        await panel.getByRole('button', {name: tab, exact: true}).click();
        await checkbox.check();
        if (tab === '自填') {
          await panel.getByRole('textbox', {name: '汤面', exact: true}).fill('汤面');
          await panel.getByRole('textbox', {name: '汤底', exact: true}).fill('汤底');
        } else await panel.getByRole('button', {name: '生成', exact: true}).click();
        await panel.getByRole('button', {name: '创建', exact: true}).click();
        await page.waitForFunction(() => !document.querySelector('.pixel-primary.loading'));
        assert.equal(creates.at(-1).is_locked, true);
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      measurements.push({width, cardHeight: lockedHeight, titleRowHeight: dimensions.row.height, checkboxFont: dimensions.font});
      await page.goto('http://soup.test/soup/room/LOCK1234');
      await page.getByRole('alert').waitFor();
      assert.equal(await page.getByRole('alert').innerText(), '仅限创建者本人和同一绑定关系的小机进入');
      await page.getByRole('link', {name: '返回大厅'}).click();
      await page.locator('.pixel-room-card').first().waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(JSON.stringify(measurements, null, 2));
    console.log('turtle locked room browser checks passed');
  } finally {await browser.close();}
})().catch(e => {console.error(e); process.exitCode = 1;});
