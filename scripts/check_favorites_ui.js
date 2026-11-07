'use strict';
// Fixtures only. Default: DOM behavior. --browser: rendered 360/430/1280 checks.
// PLAYWRIGHT_MODULE and PLAYWRIGHT_EXECUTABLE_PATH may select an existing installation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 30));

function fixture() {
  const stores = {one: new Set(), two: new Set()};
  const state = {stores, writes: 0, fail: false, hold: null};
  state.request = async (url, options = {}) => {
    const auth = (options.headers?.Authorization || '').replace('Bearer ', '');
    const user = {id: auth === 'two' ? 2 : 1, username: auth === 'two' ? '第二个人类' : '收藏测试', is_ai: false};
    let status = 200, body = {};
    if (url === '/api/auth/me') body = {user, bindings: []};
    else if (url === '/api/auth/favorites') {
      if (!stores[auth]) return {status: 401, body: {error: '未登录'}};
      if (options.method === 'POST') {
        state.writes++;
        const args = JSON.parse(options.body);
        if (state.hold) await new Promise(resolve => { state.release = resolve; });
        if (state.fail) return {status: 503, body: {error: '收藏未保存，请重试'}};
        if (args.action === 'favorite_add') stores[auth].add(args.game);
        else stores[auth].delete(args.game);
      }
      body = {self: {username: user.username, games: [...stores[auth]].map(id => ({id, name: id}))}};
    } else if (url === '/api/announcements') body = {announcements: [], unread_count: 0};
    else if (url === '/api/auth/avatar') body = {supported: false};
    else if (url === '/api/games/stats') body = {};
    return {status, body};
  };
  return state;
}

async function domChecks() {
  const {JSDOM, VirtualConsole} = require('jsdom');
  const state = fixture();
  for (const width of [360, 430, 1280]) {
    const errors = [];
    const dom = new JSDOM(html, {
      url: 'http://favorites.test/', runScripts: 'dangerously', pretendToBeVisual: true,
      virtualConsole: new VirtualConsole().on('jsdomError', e => errors.push(e.message)),
      beforeParse(w) {
        w.matchMedia = query => ({matches: query.includes('max-width: 599px') && width < 600,
          addEventListener() {}, removeEventListener() {}});
        w.localStorage.setItem('cedartoy_token', 'one');
        w.fetch = async (url, options) => {
          const {status, body} = await state.request(url, options);
          return {status, ok: status === 200, json: async () => body};
        };
      }
    });
    try {
      const w = dom.window, d = w.document;
      const star = id => d.querySelector(`[data-favorite="${id}"]`);
      const ids = () => [...d.querySelectorAll('.game-card[data-game]:not(.hidden)')].map(e => e.dataset.game);
      await tick();
      const original = ids();
      assert.equal(d.querySelectorAll('button button').length, 0);
      // Spy only the existing game entry functions; the DOM, fetch and handlers remain real.
      w.eval('window.openedGames = []; enterGameById = id => window.openedGames.push(id); selectGame = id => window.openedGames.push(id);');
      star('duel').focus(); star('duel').click(); await tick();
      assert.equal(star('duel').getAttribute('aria-pressed'), 'true');
      assert.equal(ids()[0], 'duel');
      assert.deepEqual(ids().slice(1), original.filter(id => id !== 'duel'));
      assert.equal(d.activeElement.dataset.favorite, 'duel');
      assert.equal(w.openedGames.length, 0);
      d.querySelector('[data-game="duel"] .card-open').click();
      assert.equal(w.openedGames.length, 1);
      d.querySelector('#searchInput').value = '双弈';
      d.querySelector('#searchInput').dispatchEvent(new w.Event('input'));
      assert.deepEqual(ids(), ['duel']);
      d.querySelector('#searchInput').value = '';
      d.querySelector('[data-filter="test"]').click();
      const tests = ids();
      star('mbti').click(); await tick();
      assert.equal(ids()[0], 'mbti');
      assert.deepEqual(ids().slice(1), tests.filter(id => id !== 'mbti'));
      star('attribute').click(); await tick();
      assert(state.stores.one.has('bdsmtest'));
      star('attribute').click(); await tick();
      star('mbti').click(); await tick();
      assert.deepEqual(ids(), tests);
      d.querySelector('[data-filter="mini"]').click();
      await w.loadMe(); await tick();
      assert.equal(ids()[0], 'duel');
      state.fail = true;
      star('duel').click(); await tick();
      assert.equal(star('duel').getAttribute('aria-pressed'), 'true');
      assert.match(d.querySelector('#pixelToast').textContent, /未保存/);
      state.fail = false;
      state.hold = true;
      star('soup').click(); await tick();
      const writes = state.writes;
      star('eco').click(); await tick();
      assert.equal(state.writes, writes, 'pending writes disable other stars');
      // Old response may commit for account one, but must never render for account two.
      w.localStorage.setItem('cedartoy_token', 'two');
      w.dispatchEvent(new w.StorageEvent('storage', {key: 'cedartoy_token'}));
      await tick(); state.release(); state.hold = null; await tick();
      assert.equal(d.querySelectorAll('.favorite-button[aria-pressed="true"]').length, 0);
      w.logout();
      const anonymousWrites = state.writes;
      star('duel').click(); await tick();
      assert.equal(state.writes, anonymousWrites);
      assert(d.querySelector('#loginModal').classList.contains('show'));
      assert.equal(d.querySelectorAll('.favorite-button[aria-pressed="true"]').length, 0);
      assert.deepEqual(errors, []);
      state.stores.one.clear();
      console.log(`Favorites DOM ${width}: ordering, filters, persistence reload, ownership, failures, stale replies, login passed`);
    } finally { dom.window.close(); }
  }
}

async function browserChecks() {
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const out = process.env.FAVORITES_SCREENSHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'cedartoy-favorites-'));
  fs.mkdirSync(out, {recursive: true});
  let browser;
  try {
    browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined,
      args: ['--no-sandbox']});
    const state = fixture();
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => localStorage.setItem('cedartoy_token', 'one'));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://favorites.test') return route.abort();
        if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (url.pathname.startsWith('/assets/icons/')) {
          const file = path.join(__dirname, '..', url.pathname);
          if (fs.existsSync(file)) return route.fulfill({path: file});
        }
        const request = route.request();
        const response = await state.request(url.pathname, {method: request.method(), body: request.postData(),
          headers: {Authorization: request.headers().authorization}});
        return route.fulfill({status: response.status, json: response.body});
      });
      await page.goto('http://favorites.test/');
      await page.waitForFunction(() => favoritesReady);
      const star = page.locator('[data-favorite="duel"]');
      if (width < 600) await star.tap(); else { await star.focus(); await page.keyboard.press('Space'); }
      await page.waitForFunction(() => !favoritesBusy && isFavorite({id: 'duel'}));
      assert.equal(await page.locator('.game-card[data-game]:visible').first().getAttribute('data-game'), 'duel');
      assert.equal(await page.locator('#gameDrawer.show').count(), 0);
      await page.reload(); await page.waitForFunction(() => favoritesReady);
      assert.equal(await star.getAttribute('aria-pressed'), 'true');
      assert(await star.evaluate(el => {
        const r = el.getBoundingClientRect(), level = el.previousElementSibling.getBoundingClientRect();
        const style = getComputedStyle(el), target = getComputedStyle(el, '::before');
        const x = r.right - parseFloat(target.right) - 44, y = r.y + r.height / 2 - 22;
        return Math.abs(r.y + r.height / 2 - level.y - level.height / 2) < 1
          && parseFloat(style.fontSize) <= 14 && style.borderWidth === '0px'
          && style.boxShadow === 'none' && style.backgroundColor === 'rgba(0, 0, 0, 0)'
          && target.width === '44px' && target.height === '44px'
          && [[2, 2], [42, 2], [2, 42], [42, 42], [22, 22]].every(([dx, dy]) =>
            document.elementFromPoint(x + dx, y + dy) === el);
      }), 'Small unframed star shares the LV row and its transparent 44px target receives hits');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path: path.join(out, `${width}-mini.png`), fullPage: false});
      await page.locator('[data-filter="test"]').click();
      await page.locator('[data-favorite="mbti"]').click();
      await page.waitForFunction(() => !favoritesBusy && isFavorite({id: 'mbti'}));
      await page.screenshot({path: path.join(out, `${width}-test.png`), fullPage: false});
      await page.locator('[data-favorite="mbti"]').click();
      await page.waitForFunction(() => !favoritesBusy);
      await page.locator('[data-filter="mini"]').click();
      await star.click(); await page.waitForFunction(() => !favoritesBusy);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(`Rendered checks passed. Screenshots: ${out}`);
  } finally {
    if (browser) await browser.close();
    // Explicit output is for manual visual review; automatic scratch output is disposable.
    if (!process.env.FAVORITES_SCREENSHOTS) fs.rmSync(out, {recursive: true, force: true});
  }
}
(process.argv.includes('--browser') ? browserChecks() : domChecks()).catch(error => {console.error(error); process.exitCode = 1;});
