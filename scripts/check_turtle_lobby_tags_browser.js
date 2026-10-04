/* Local build + intercepted API fixtures only; no running service or database.
 * npm run build -- --outDir /tmp/soup-tags-review/build (from turtle-soup/frontend)
 * SOUP_UI_BUILD / SOUP_UI_SCREENSHOTS / PLAYWRIGHT_MODULE may override paths.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const build = process.env.SOUP_UI_BUILD || '/tmp/soup-tags-review/build';
const output = process.env.SOUP_UI_SCREENSHOTS || '/tmp/soup-tags-review/screenshots';
const tags = ['本格', '变格', '清汤', '黑汤', '红汤', '规则怪谈'];
const base = {surface: '下雨天，他终于能直接回家。为什么？', active_players: 1, ask_count: 1, created_at: '2026-10-01 12:00:00'};
const rooms = [
  {...base, id: 'RED', title: '雨中的电梯', status: 'playing', tags: '本格， 红汤 '},
  {...base, id: 'BLACK', title: '门后的脚步', status: 'finished', tags: ['变格', '黑汤']},
  {...base, id: 'RULE', title: '夜班守则', status: 'waiting', tags: '清汤、规则怪谈'},
];

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    for (const width of [360, 428, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.host !== 'soup.test') return route.abort();
        if (url.pathname.startsWith('/soup/api/')) {
          const endpoint = url.pathname.slice('/soup/api'.length);
          const player = {id: 1, username: '测试游客', is_guest: true};
          const json = endpoint === '/auth/guest' ? {token: 'fixture-token', player}
            : endpoint === '/auth/me' ? {player}
            : endpoint === '/rooms/' ? rooms
            : endpoint === '/puzzles/public' ? [] : {};
          return route.fulfill({json});
        }
        const relative = url.pathname.startsWith('/soup/assets/') ? url.pathname.slice('/soup/'.length) : 'index.html';
        return route.fulfill({body: fs.readFileSync(path.join(build, relative)), contentType:
          relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html'});
      });
      await page.goto('http://soup.test/soup/');
      const cards = page.locator('.pixel-room-card');
      await cards.nth(2).waitFor();
      await page.evaluate(() => document.fonts.ready);
      const filters = page.getByRole('group', {name: '按标签筛选', exact: true});
      assert.deepEqual(await filters.getByRole('button').allTextContents(), tags);
      const measure = () => filters.evaluate(el => {
        const box = el.getBoundingClientRect();
        return {left: box.left, right: box.right, width: box.width, height: box.height,
          scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
          buttons: [...el.children].map(button => {
            const rect = button.getBoundingClientRect();
            const style = getComputedStyle(button);
            const range = document.createRange();
            range.selectNodeContents(button);
            return {text: button.textContent.trim(), x: rect.x, y: rect.y, right: rect.right,
              width: rect.width, height: rect.height, font: style.fontSize,
              padding: style.padding, border: style.borderWidth, shadow: style.boxShadow,
              textRects: [...range.getClientRects()].map(r => ({left: r.left, right: r.right}))};
          })};
      });
      const before = await measure();
      console.log(JSON.stringify({width, ...before}));
      await page.screenshot({path: path.join(output, `lobby-tags-${width}.png`), fullPage: true});
      assert.equal(new Set(before.buttons.map(b => b.y)).size, 1, 'all six filters share one row');
      assert.ok(before.scrollWidth <= before.clientWidth, 'filter row needs no horizontal scrolling');
      for (const button of before.buttons) {
        assert.ok(button.x >= before.left && button.right <= before.right + 1, `${button.text} fits the content area`);
        assert.equal(button.textRects.length, 1, `${button.text} stays on one line`);
        assert.ok(button.textRects[0].left >= button.x && button.textRects[0].right <= button.right, 'full text fits the button');
        assert.equal(button.font, width < 900 ? '12px' : '14px');
        assert.ok(button.width >= 24 && button.height >= 30, 'existing touch height is retained');
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const badges = await cards.locator('.soup-badge').evaluateAll(elements => elements.map(el => ({
        text: el.textContent, className: el.className, background: getComputedStyle(el).backgroundColor,
        border: getComputedStyle(el).borderTopColor,
      })));
      for (const tag of tags) {
        const badge = badges.find(b => b.text === tag);
        assert.ok(badge, `${tag} fixture renders`);
        assert.equal(badge.className, tag === '红汤' ? 'soup-badge red' : 'soup-badge');
        const color = tag === '红汤' ? 'rgb(157, 67, 72)' : 'rgb(38, 34, 50)';
        assert.equal(badge.background, color);
        assert.equal(badge.border, color);
      }
      assert.equal(badges.filter(b => b.text === '进行中').length, 2);
      for (const badge of badges.filter(b => b.text === '进行中')) {
        assert.equal(badge.className, 'soup-badge playing');
        assert.equal(badge.background, 'rgba(134, 239, 172, 0.38)');
      }
      const finished = badges.find(b => b.text === '已结束');
      assert.equal(finished.className, 'soup-badge pale');
      assert.equal(finished.background, 'rgba(230, 168, 177, 0.14)');

      const rule = filters.getByRole('button', {name: '规则怪谈', exact: true});
      if (width < 600) await rule.tap();
      else {await rule.focus(); await page.keyboard.press('Space');}
      assert.equal(await rule.getAttribute('aria-pressed'), 'true');
      assert.deepEqual(await cards.locator('h2').allTextContents(), ['夜班守则']);
      assert.deepEqual(await measure(), before, 'selection does not shift the toolbar');
      await page.screenshot({path: path.join(output, `lobby-tags-selected-${width}.png`), fullPage: true});
      await rule.click();
      await cards.nth(2).waitFor();
      const red = filters.getByRole('button', {name: '红汤', exact: true});
      await red.click();
      assert.deepEqual(await cards.locator('h2').allTextContents(), ['雨中的电梯']);
      await red.click();
      const search = page.getByRole('searchbox', {name: '按汤名搜索房间'});
      await search.fill('门后');
      assert.deepEqual(await cards.locator('h2').allTextContents(), ['门后的脚步']);
      await search.fill('');
      await cards.nth(2).waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('turtle lobby tag browser checks passed');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
