/* Homepage-only fixtures: no production APIs, credentials, or vendor writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const domOnly = process.argv.includes('--dom-only');
const chromium = domOnly ? null : require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium;
const html = fs.readFileSync(process.env.SHARED_CRAFT_HOME_HTML || 'index.html', 'utf8');
const captureOnly = process.argv.includes('--capture-only');
const output = process.env.SHARED_CRAFT_HOME_SCREENSHOTS || '/tmp/shared-craft-home-review';
const repo = 'https://github.com/SuLee3927/shared-craft-room';
fs.mkdirSync(output, {recursive:true});
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);

async function checkDom() {
  const {JSDOM} = require('jsdom');
  const report = [];
  for (const width of [360,430,1280]) {
    const dom = new JSDOM(html, {url:'https://craft-home.test/',runScripts:'outside-only',pretendToBeVisual:true});
    const w = dom.window, d = w.document, requests = [];
    let allowed = false, failSession = false, failAccess = false, delayedAccess;
    w.matchMedia = query => ({matches:query.includes('599px') && width<600,addEventListener(){},removeEventListener(){}});
    w.scrollTo = () => {};
    w.HTMLElement.prototype.scrollIntoView = () => {};
    w.__navigations = [];
    w.fetch = async (url, options = {}) => {
      requests.push({url,options});
      let data = {}, ok = true;
      if (url === '/api/announcements') data = {announcements:[],unread_count:0};
      if (url === '/shared-craft/author?access=1') {
        if (delayedAccess) return delayedAccess;
        data = {allowed}; ok = !failAccess;
      }
      if (url.startsWith('/shared-craft/author?page=')) data = {items:[],total:0,page:1,pages:1};
      if (url === '/shared-craft/session') { data = {error:'请先绑定小机'}; ok = !failSession; }
      return {ok,status:ok?200:403,json:async()=>data};
    };
    // JSDOM has no navigation engine. Capture only these two navigation sinks;
    // all original rendering, event handlers, permission checks and session fetches run.
    for (const script of d.querySelectorAll('script:not([src])')) w.eval(script.textContent
      .replace('window.location.href = game.url;', 'window.__navigations.push(game.url);')
      .replace('location.assign("/shared-craft/crafts/");', 'window.__navigations.push("/shared-craft/crafts/");') + `
        window.__craftSession = loggedIn => {
          if (loggedIn) {
            localStorage.setItem(TOKEN_KEY, "fixture-member");
            me = {user:{id:101},bindings:[]};
          } else { localStorage.removeItem(TOKEN_KEY); me = null; }
          renderDetail();
        };
      `);
    const tick = () => new Promise(resolve=>setImmediate(resolve));
    await tick();
    const select = () => w.eval('selectGame("shared_craft", true)');
    const entries = () => {
      for (const id of ['enterButton','guideEnterButton','drawerEnter']) assert.equal(d.getElementById(id).textContent,'GitHub →');
      for (const id of ['watchButton','guideWatchButton','drawerWatchButton']) {
        assert.equal(d.getElementById(id).textContent,'进入手作间 →');
        assert.notEqual(d.getElementById(id).style.display,'none');
      }
      assert.equal(d.getElementById('drawerMetricLabel').textContent,'玩法数');
      assert.equal(d.getElementById('drawerMetric').textContent,'2');
    };
    const authorVisible = () => [...d.querySelectorAll('[data-craft-author-entry]')].some(el=>el.style.display!=='none');
    select(); entries(); assert.equal(authorVisible(),false);
    assert.equal(d.querySelector('[data-game="shared_craft"] .card-enter').textContent,'GitHub →');
    d.querySelector('[data-game="shared_craft"]').click();
    if (width<600) assert(d.getElementById('gameDrawer').classList.contains('show'));
    else assert.equal(w.__navigations.pop(),repo);
    for (const id of ['enterButton','guideEnterButton','drawerEnter']) {
      d.getElementById(id).click(); assert.equal(w.__navigations.pop(),repo);
    }
    d.getElementById('drawerWatchButton').click(); await tick();
    assert(d.getElementById('loginModal').classList.contains('show'));
    assert(!d.getElementById('gameDrawer').classList.contains('show'));
    assert(!requests.some(r=>r.url==='/shared-craft/session'));
    w.closeModals(); w.__craftSession(true);
    select(); await tick(); entries(); assert.equal(authorVisible(),false);
    for (const id of ['watchButton','guideWatchButton','drawerWatchButton']) {
      d.getElementById(id).click(); await tick();
      assert.equal(w.__navigations.pop(),'/shared-craft/crafts/');
      const request = requests.filter(r=>r.url==='/shared-craft/session').at(-1);
      assert.equal(request.options.method,'POST');
      assert.equal(request.options.headers.Authorization,'Bearer fixture-member');
    }
    failSession = true;
    d.getElementById('drawerWatchButton').click(); await tick();
    assert.equal(w.__navigations.length,0);
    assert.equal(d.getElementById('pixelToast').textContent,'请先绑定小机');
    failSession = false; allowed = true;
    await w.eval('refreshCraftAuthorAccess()');
    assert(authorVisible()); entries();
    d.querySelector('[data-craft-author]').click(); await tick();
    assert(d.getElementById('craftAuthorModal').classList.contains('show'));
    assert.equal(d.getElementById('craftAuthorMsg').textContent,'暂无主动分享的作品。');
    d.getElementById('craftAuthorClose').click();
    d.getElementById('drawerWatchButton').click(); await tick();
    assert.equal(w.__navigations.pop(),'/shared-craft/crafts/');
    d.getElementById('drawerEnter').click(); assert.equal(w.__navigations.pop(),repo);
    for (const id of ['garden_cat','camping_plaza','duel','workkk']) {
      w.eval(`selectGame("${id}", true)`);
      assert.equal(authorVisible(),false);
      assert.equal(d.getElementById('enterButton').textContent,'GitHub →');
      assert.notEqual(d.getElementById('watchButton').textContent,'查看已分享作品');
    }
    select(); await tick();
    failAccess = true; await w.eval('refreshCraftAuthorAccess()'); assert.equal(authorVisible(),false); entries();
    failAccess = false;
    let resolveAccess;
    delayedAccess = new Promise(resolve=>{resolveAccess=resolve;});
    const pending = w.eval('refreshCraftAuthorAccess()');
    w.__craftSession(false);
    resolveAccess({ok:true,json:async()=>({allowed:true})});
    await pending; await tick();
    assert.equal(authorVisible(),false); entries();
    report.push({width,mode:'DOM only; no layout or screenshots',dualEntries:true,metric:'玩法数 2',
      sessionAndLogin:true,authorAccessAndStaleResponse:true});
    w.close();
  }
  fs.writeFileSync(path.join(output,'dom-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
}

(async () => {
  if (domOnly) return checkDom();
  const browser = await chromium.launch({
    headless:true, executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH, args:['--no-sandbox'],
  });
  const report = [];
  try {
    for (const width of [360,430,1280]) {
      const page = await browser.newPage({viewport:{width,height:900},hasTouch:width<600});
      const errors = [], sessions = [];
      let allowed = false, sessionError = false, accessError = false;
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.href === repo) return route.fulfill({body:'Captured repository navigation'});
        if (url.hostname !== 'craft-home.test') return route.abort();
        if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:html});
        if (url.pathname === '/shared-craft/session') {
          sessions.push({method:req.method(),authorization:req.headers().authorization});
          return route.fulfill(sessionError ? {status:403,json:{error:'请先绑定小机'}} : {json:{ok:true}});
        }
        if (url.pathname === '/shared-craft/crafts/') return route.fulfill({body:'Captured original UI destination'});
        if (url.pathname === '/shared-craft/author') {
          if (url.searchParams.has('access')) return route.fulfill(accessError
            ? {status:503,json:{error:'unavailable'}} : {json:{allowed}});
          return route.fulfill({json:{items:[],page:1,pages:1,total:0,has_prev:false,has_next:false}});
        }
        if (url.pathname === '/api/announcements') return route.fulfill({json:{announcements:[],unread_count:0}});
        if (url.pathname.startsWith('/assets/')) {
          const file = path.resolve('.' + url.pathname);
          if (file.startsWith(path.resolve('assets') + path.sep) && fs.existsSync(file)) return route.fulfill({path:file});
        }
        return route.fulfill({json:{}});
      });
      const touch = locator => width<600 ? locator.tap() : locator.click();
      async function home(role = 'guest') {
        await page.goto('https://craft-home.test/');
        await page.waitForFunction(() => typeof previewGame === 'function' && announcementState.loaded);
        await page.evaluate(role => {
          if (role === 'guest') { localStorage.removeItem(TOKEN_KEY); me = null; }
          else {
            localStorage.setItem(TOKEN_KEY, 'fixture-' + role);
            me = {user:{id:role === 'author' ? 102 : 101,username:'验收账号'},bindings:[]};
          }
        }, role);
        await page.locator('#searchInput').fill('共同手作间');
        if (width<600) await touch(page.locator('[data-game="shared_craft"]'));
        else await page.locator('[data-game="shared_craft"]').hover();
      }
      const shot = name => page.screenshot({path:path.join(output,`${width}-${name}.png`)});
      const primary = width<600 ? '#drawerEnter' : '#enterButton';
      const secondary = width<600 ? '#drawerWatchButton' : '#watchButton';
      async function entries() {
        for (const id of ['enterButton','guideEnterButton','drawerEnter']) {
          assert.equal(await page.locator('#'+id).textContent(), 'GitHub →');
        }
        for (const id of ['watchButton','guideWatchButton','drawerWatchButton']) {
          assert.equal(await page.locator('#'+id).textContent(), '进入手作间 →');
          assert.notEqual(await page.locator('#'+id).evaluate(el=>el.style.display), 'none');
        }
        assert(await page.locator(primary).isVisible());
        assert(await page.locator(secondary).isVisible());
        assert.equal(await page.locator('#drawerMetricLabel').textContent(), '玩法数');
        assert.equal(await page.locator('#drawerMetric').textContent(), '2');
        assert.equal(await page.locator('#detailMetric').textContent(), '2');
        assert.equal(await page.locator('#guideTab').isVisible(), false, 'do not invent a guide page');
        assert.equal(await page.locator('[data-drawer-tab="guide"]').isVisible(), false);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        for (const selector of [primary,secondary]) {
          const rect = await page.locator(selector).boundingBox();
          assert(rect.x>=0 && rect.x+rect.width<=width+1 && rect.height>=38);
        }
      }

      await home();
      await shot('guest');
      if (captureOnly) { await page.close(); continue; }
      await entries();
      assert.equal(await page.locator('[data-craft-author]:visible').count(), 0);
      assert.equal(await page.locator('[data-game="shared_craft"] .card-enter').textContent(), 'GitHub →');
      await touch(page.locator(secondary));
      await page.locator('#loginModal.show').waitFor();
      assert.equal(await page.locator('#gameDrawer.show').count(), 0);
      await page.locator('#loginModal input').first().click();
      assert(await page.locator('#loginModal input').first().evaluate(el=>el===document.activeElement));
      assert.equal(sessions.length, 0);
      await shot('login');

      // Every existing primary control, including the normally hidden guide control,
      // navigates to the same repository without creating a craft session.
      for (const id of ['enterButton','guideEnterButton','drawerEnter']) {
        await home();
        await page.locator('#'+id).dispatchEvent('click');
        await page.waitForURL(repo);
      }
      if (width>=600) {
        await home();
        await page.locator('[data-game="shared_craft"]').click();
        await page.waitForURL(repo);
      }
      assert.equal(sessions.length, 0);

      await home('member');
      await page.evaluate(() => refreshCraftAuthorAccess());
      await entries();
      assert.equal(await page.locator('[data-craft-author]:visible').count(), 0);
      sessionError = true;
      await touch(page.locator(secondary));
      await page.waitForFunction(() => document.querySelector('#pixelToast').textContent === '请先绑定小机');
      assert.equal(new URL(page.url()).pathname, '/');
      sessionError = false;
      for (const id of ['watchButton','guideWatchButton','drawerWatchButton']) {
        await home('member');
        const before = sessions.length;
        await page.locator('#'+id).dispatchEvent('click');
        await page.waitForURL('https://craft-home.test/shared-craft/crafts/');
        assert.equal(sessions.length, before+1);
        assert.deepEqual(sessions.at(-1), {method:'POST',authorization:'Bearer fixture-member'});
      }

      allowed = true;
      await home('author');
      await page.locator('[data-craft-author]:visible').waitFor();
      await entries();
      assert.equal(await page.locator('[data-craft-author]:visible').count(), 1);
      await shot('author');
      const author = page.locator('[data-craft-author]:visible');
      await author.focus();
      await page.keyboard.press('Enter');
      await page.locator('#craftAuthorModal.show').waitFor();
      await page.waitForFunction(() => document.querySelector('#craftAuthorMsg').textContent === '暂无主动分享的作品。');
      await shot('author-modal');
      await page.locator('#craftAuthorClose').click();
      assert(await author.evaluate(el=>el===document.activeElement), 'close restores focus');
      await entries();
      await touch(page.locator(secondary));
      await page.waitForURL('https://craft-home.test/shared-craft/crafts/');
      assert.equal(sessions.at(-1).authorization, 'Bearer fixture-author');
      await home('author');
      await page.locator('[data-craft-author]:visible').waitFor();
      await touch(page.locator(primary));
      await page.waitForURL(repo);

      // Permission controls disappear on game/account switches and failed access checks.
      await home('author');
      await page.locator('[data-craft-author]:visible').waitFor();
      for (const id of ['garden_cat','camping_plaza','duel','workkk']) {
        await page.evaluate(id => selectGame(id, true), id);
        assert.equal(await page.locator('[data-craft-author]:visible').count(), 0);
        assert.equal(await page.locator(primary).textContent(), 'GitHub →');
        assert.notEqual(await page.locator(secondary).textContent(), '查看已分享作品');
      }
      await page.evaluate(() => selectGame('shared_craft', true));
      await page.locator('[data-craft-author]:visible').waitFor();
      await page.evaluate(() => { localStorage.removeItem(TOKEN_KEY); me = null; renderDetail(); });
      assert.equal(await page.locator('[data-craft-author]:visible').count(), 0);
      await entries();
      accessError = true;
      await home('member');
      await page.evaluate(() => refreshCraftAuthorAccess());
      assert.equal(await page.locator('[data-craft-author]:visible').count(), 0);
      await entries();
      assert.deepEqual(errors, []);
      report.push({width,dualEntries:true,metric:'玩法数 2',guestLogin:true,sessionError:true,
        authorControls:true,accountAndGameSwitch:true,consoleErrors:errors});
      await page.close();
    }
    fs.writeFileSync(path.join(output,'report.json'), JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
