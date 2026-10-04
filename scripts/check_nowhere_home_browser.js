/* Real homepage -> bound machine/slot -> original UI, on isolated acceptance HTTP. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const config = JSON.parse(fs.readFileSync(process.env.NOWHERE_BROWSER_CONFIG, 'utf8'));
const output = path.join(process.env.NOWHERE_SCREENSHOTS, 'homepage');
fs.mkdirSync(output, {recursive:true});
const urls = {
  nowhere:'https://github.com/yuyixuanfu/nowhere',
  ciyuwu:'https://github.com/yuyixuanfu/ci-yu-wu',
  market:'https://github.com/yuyixuanfu/shangzhuochifan',
};

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const report = [];
  try {
    async function session(width, token) {
      const page = await browser.newPage({viewport:{width,height:900},hasTouch:width<600});
      page.setDefaultTimeout(15000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      // No production request: local HTTP is real; external navigation is captured.
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin === config.origin) return route.continue();
        if (Object.values(urls).includes(url.href))
          return route.fulfill({contentType:'text/html',body:'<title>Captured GitHub navigation</title>'});
        return route.abort();
      });
      await page.addInitScript(({origin,token}) => {
        if (location.origin === origin && token) localStorage.setItem('cedartoy_token',token);
      }, {origin:config.origin,token});
      async function home() {
        await page.goto(config.origin+'/');
        await page.waitForFunction(loggedIn => loggedIn ? Boolean(me?.user) : Boolean(document.querySelector('#loginOpen')), Boolean(token));
      }
      async function card(id) {
        await page.locator('#searchInput').fill(id === 'ciyuwu' ? '词与物' : id === 'market' ? '买菜' : '乌有乡');
        const target = page.locator(`[data-game="${id}"]`);
        assert.equal(await target.locator('.card-enter').textContent(), 'GitHub →');
        if (width<600) {
          await target.tap();
          await page.locator('#gameDrawer.show').waitFor();
        } else {
          // Desktop cards retain direct GitHub navigation; their focus/hover
          // exposes the existing detail panel with the separate journey entry.
          await target.click();
          await page.waitForURL(urls[id]);
          await home();
          await page.locator('#searchInput').fill(id === 'ciyuwu' ? '词与物' : id === 'market' ? '买菜' : '乌有乡');
          await page.locator(`[data-game="${id}"]`).hover();
        }
      }
      const screenshot = name => page.screenshot({animations:'disabled',path:path.join(output,`${width}-${name}.png`)});
      const watch = () => page.locator(width<600 ? '#drawerWatchButton' : '#watchButton').click();
      return {page,errors,home,card,screenshot,watch};
    }

    for (const width of [360,428,1280]) {
      const {page,errors,home,card,screenshot,watch} = await session(width,config.token);
      await home();
      const targets = await page.evaluate(() => homepageGames.filter(g=>g.url?.startsWith('https://github.com/')&&g.id!=='duel').map(g=>({id:g.id,url:g.url,label:g.ctaLabel})));
      assert(targets.length>=22);
      assert(targets.every(g=>g.label==='GitHub →'));
      await page.locator('#searchInput').fill('乌有乡');
      await screenshot('card');
      // Empty -> populated -> empty must neither throw nor retain stale rows.
      for (const id of ['nowhere','ciyuwu','market']) {
        await card(id);
        assert.equal(await page.locator('#enterButton').textContent(),'GitHub →');
        assert.equal(await page.locator('#guideEnterButton').textContent(),'GitHub →');
        assert.equal(await page.locator('#drawerEnter').textContent(),'GitHub →');
        assert.notEqual(await page.locator('#enterButton').evaluate(el=>getComputedStyle(el).textTransform),'uppercase');
        assert.equal(await page.locator('.stat-bars').evaluate(el=>getComputedStyle(el).display==='none'), id==='nowhere');
        if (id!=='nowhere') {
          assert(await page.locator('#statOneLabel').textContent());
          assert(await page.locator('#statTwoLabel').textContent());
        }
        const detail = await page.locator(width<600 ? '#drawerLogs' : '#detailDesc').innerText();
        assert(detail.includes('青少年小鼠狂饮乙醇（小红书号 94326164228）'));
        if (width===360 || id==='nowhere') await screenshot(id+'-detail');
        await page.locator(width<600 ? '#drawerEnter' : '#enterButton').click();
        await page.waitForURL(urls[id]);
        await home();
      }
      // One-stat is also valid, and a subsequent normal game restores both rows.
      await page.evaluate(() => {
        const game = games.find(g=>g.id==='nowhere');
        const original = game.stats;
        game.stats = [['single-stat fixture','25%']];
        previewGame('nowhere');
        if (document.querySelector('#statTwoLabel').closest('.stat-item').style.display!=='none') throw Error('missing row not hidden');
        game.stats = original;
        previewGame('ciyuwu');
        if (document.querySelector('#statTwoLabel').closest('.stat-item').style.display==='none') throw Error('row not restored');
      });
      await card('nowhere');
      await watch();
      await page.locator('[data-nowhere-save]').first().waitFor();
      const options = await page.locator('[data-nowhere-save]').allTextContents();
      assert.equal(options.length,2);
      assert(options.some(s=>s.includes('验收旅者甲 · 槽1')));
      assert(options.some(s=>s.includes('验收旅者甲 · 槽2')));
      assert(options.every(s=>!s.includes('验收旅者乙')&&!s.includes('undefined')));
      await screenshot('picker');
      await page.locator('[data-nowhere-save]').filter({hasText:'槽2'}).click();
      await page.waitForURL(url => url.pathname==='/nowhere/'&&!url.searchParams.has('token'));
      assert.equal(new URL(page.url()).searchParams.get('player'),'101:2');
      await page.waitForFunction(()=>document.querySelector('#now .text')?.textContent.trim().length>0);
      await page.waitForFunction(()=>document.querySelector('#platform-status')?.hidden!==false);
      const state = await page.evaluate(async()=>{const r=await fetch('/nowhere/state?player=202');return r.status;});
      assert.equal(state,403,'cookie session must still reject another bound account');
      const links = await page.locator('a[href*="github.com"]').allTextContents();
      assert(links.length && links.every(label=>label.trim()==='GitHub'));
      await screenshot('map');
      await page.locator('#wallbtn').click();
      await page.locator('#tabReplied').click();
      await page.locator('.mini').first().click();
      await page.locator('#replyin').waitFor();
      assert((await page.locator('body').innerText()).includes('PRIVATE-CARD-101-2'));
      await screenshot('postcard');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);
      assert.deepEqual(errors,[]);
      report.push({width,mode:'live-homepage-real-auth-saves-engine',githubTargets:targets.length,slots:2,errors});
      await page.close();
    }

    for (const [name,token,message] of [
      ['login',null,'请先登录'],['unbound',config.unboundToken,'还没有绑定小机'],['empty',config.emptyToken,'还没有乌有乡旅程'],
    ]) {
      const {page,errors,home,card,screenshot,watch} = await session(360,token);
      await home();await card('nowhere');await watch();
      await page.getByText(message,{exact:false}).first().waitFor({state:'visible'});
      if (name==='login') {
        await page.locator('#loginModal.show').waitFor();
        assert.equal(await page.locator('#gameDrawer.show').count(),0,'detail drawer must not cover login');
        // Hit testing, not just DOM visibility: the actual login form is usable.
        await page.locator('#loginModal input').first().click();
        assert(await page.locator('#loginModal input').first().evaluate(el=>el===document.activeElement));
      }
      if (name==='empty') assert.equal(await page.locator('[data-nowhere-save]').count(),0);
      await screenshot(name);
      assert.deepEqual(errors,[]);
      report.push({state:name,explicitMessage:true,errors});
      await page.close();
    }
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exit(1)});
