/* Build to /tmp first. Intercept every request: no live API or DB access. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const build = process.env.SOUP_UI_BUILD || '/tmp/soup-leaderboard-review/build';
const output = process.env.SOUP_UI_SCREENSHOTS || '/tmp/soup-leaderboard-review/screenshots';
(async () => {
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    for (const width of [360, 428, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      const errors = [], requests = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.host !== 'soup.test') return route.abort();
        if (url.pathname.startsWith('/soup/api/')) {
          const endpoint = url.pathname.slice('/soup/api'.length);
          const player = {id: 1, username: '测试游客', is_guest: true};
          let json = endpoint === '/auth/guest' ? {token: 'fixture-token', player}
            : endpoint === '/auth/me' ? {player} : [];
          if (endpoint.startsWith('/leaderboard/')) {
            requests.push(endpoint + url.search);
            json = url.searchParams.get('scope') === 'today' ? [] : [
              {id: 1, username: '像素侦探', is_ai: 0, score: 125},
              {id: 2, username: '机器侦探', is_ai: 1, score: 99},
              {id: 3, username: '很长的玩家名称用来检查手机排行榜文本截断效果', is_ai: 1, score: 88},
            ];
          }
          return route.fulfill({json});
        }
        const relative = url.pathname.startsWith('/soup/assets/') ? url.pathname.slice('/soup/'.length) : 'index.html';
        return route.fulfill({body: fs.readFileSync(path.join(build, relative)), contentType:
          relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html'});
      });
      await page.goto('http://soup.test/soup/');
      await page.getByRole('button', {name: '排行榜', exact: true}).click();
      await page.locator('.rank-list li').nth(1).waitFor();
      assert.deepEqual(await page.locator('.rank-list li span').allTextContents(), [
        '像素侦探', '机器侦探 🤖', '很长的玩家名称用来检查手机排行榜文本截断效果 🤖',
      ]);
      const names = await page.locator('.rank-list li span').evaluateAll(elements => elements.map(el => {
        const style = getComputedStyle(el);
        const score = el.nextElementSibling.getBoundingClientRect();
        const box = el.getBoundingClientRect();
        return {whiteSpace: style.whiteSpace, overflow: style.overflow, textOverflow: style.textOverflow,
          fits: el.scrollWidth <= el.clientWidth, separated: box.right <= score.left, height: box.height};
      }));
      assert.ok(names.every(n => n.whiteSpace === 'nowrap' && n.overflow === 'hidden' && n.textOverflow === 'ellipsis' && n.separated));
      assert.ok(names[1].fits, 'short AI name and emoji remain fully visible');
      if (width < 600) assert.equal(names[2].fits, false, 'long AI name retains existing truncation');
      assert.equal(names[1].height, names[0].height, 'emoji does not increase the row height');
      const measure = () => page.locator('.leaderboard-panel').evaluate(panel => {
        const rect = el => {const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height};};
        const heading = document.querySelector('.leaderboard-heading');
        const group = heading.querySelector('.leaderboard-scope');
        const title = heading.querySelector('h1');
        const back = heading.parentElement.querySelector(':scope > button');
        const tabs = panel.querySelector('.leaderboard-tabs');
        return {panel: rect(panel), title: rect(title), group: rect(group), back: {...rect(back), display: getComputedStyle(back).display}, tabs: rect(tabs), buttons: [...group.children].map(b => ({...rect(b), font: getComputedStyle(b).fontSize, border: getComputedStyle(b).borderWidth})), metricFonts: [...tabs.children].map(b => getComputedStyle(b).fontSize), pageWidth: document.documentElement.scrollWidth};
      });
      const before = await measure();
      assert.equal(before.buttons[0].font, '12px');
      assert.ok(before.buttons.every(b => b.height >= 30 && b.width >= 24));
      assert.ok(before.metricFonts.every(f => f === '14px'));
      assert.ok(before.group.width < before.panel.width / 2, 'scope stays compact');
      assert.equal(await page.locator('.leaderboard-scope').count(), 1);
      assert.equal(await page.locator('.leaderboard-panel .leaderboard-scope').count(), 0);
      assert.ok(Math.abs(before.title.y + before.title.height / 2 - before.group.y - before.group.height / 2) < 1, 'title and scope share the same row');
      const titleGap = before.group.x - before.title.x - before.title.width;
      assert.ok(titleGap >= 6 && titleGap <= 12, 'scope sits immediately beside the title');
      assert.ok(before.group.y + before.group.height <= before.panel.y, 'scope is outside and above the panel');
      assert.ok(before.group.x + before.group.width <= width, 'scope remains inside the viewport');
      if (width < 600) assert.equal(before.back.display, 'none', 'mobile return button stays hidden');
      else assert.ok(before.back.x > before.group.x + before.group.width && before.back.width > 0, 'desktop return button stays on the right');
      assert.ok(before.pageWidth <= width, 'no page overflow');
      await page.screenshot({path: path.join(output, `leaderboard-all-${width}.png`), fullPage: true});
      const metrics = page.locator('.leaderboard-tabs button');
      await metrics.nth(4).click();
      await page.waitForFunction(() => document.querySelectorAll('.rank-list li').length === 3);
      const today = page.getByRole('button', {name: '今日', exact: true});
      if (width < 600) await today.tap();
      else {await today.focus(); await page.keyboard.press('Space');}
      await page.waitForFunction(() => document.querySelectorAll('.rank-list li').length === 0);
      assert.equal(requests.at(-1), '/leaderboard/no?scope=today');
      assert.equal(await metrics.nth(4).getAttribute('class'), 'active');
      assert.equal(await today.getAttribute('aria-pressed'), 'true');
      const after = await measure();
      assert.deepEqual(after.tabs, before.tabs, 'scope change leaves metric geometry unchanged');
      assert.deepEqual(after.group, before.group, 'scope selection does not resize controls');
      await page.screenshot({path: path.join(output, `leaderboard-today-${width}.png`), fullPage: true});
      for (let i = 0; i < 5; i++) {
        await metrics.nth(i).click();
        await page.waitForFunction(index => document.querySelectorAll('.leaderboard-tabs button')[index].classList.contains('active'), i);
        const box = await metrics.nth(i).boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= width, 'each dimension can scroll into view');
      }
      await page.getByRole('button', {name: '总榜', exact: true}).click();
      await page.locator('.rank-list li').nth(1).waitFor();
      await page.emulateMedia({reducedMotion: 'reduce'});
      await page.evaluate(() => {document.documentElement.style.fontSize = '200%';});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({width, ...before}));
      await page.close();
    }
    console.log('turtle leaderboard browser checks passed: 360/428/1280, touch/keyboard, long names, empty, scope/metric stability');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
