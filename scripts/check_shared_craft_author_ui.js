/* Isolated homepage acceptance: no production requests, credentials or user data.
 * node scripts/check_shared_craft_author_ui.js
 * PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chrome ... --browser
 * Screenshots default to /tmp/shared-craft-author-ui. Fixtures model the existing
 * authorized, shared-only, ten-item backend contract; they do not test live grants. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
// JSDOM's separate eval calls do not share top-level let bindings. Expose only
// the fixture's identity setter inside the real script's lexical environment.
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8').replace(
  'let me = null;',
  'let me = null; window.__authorSession = id => { me = {user:{id,username:"验收作者"},bindings:[]}; };'
);
const widths = [360, 390, 430, 1280];

function installFixture() {
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1sAAAAASUVORK5CYII=';
  const f = window.authorFixture = {
    requests: [], created: [], revoked: [], holds: [], hold: '', allowed: true,
    listStatus: 200, imageStatus: 200, imageType: 'image/png', count: 12,
  };
  localStorage.setItem('cedartoy_token', 'author-A');
  const create = URL.createObjectURL?.bind(URL), revoke = URL.revokeObjectURL?.bind(URL);
  URL.createObjectURL = blob => {
    const url = create ? create(blob) : `blob:fixture-${f.created.length}`;
    f.created.push(url); return url;
  };
  URL.revokeObjectURL = url => { f.revoked.push(url); if (revoke) revoke(url); };
  window.fetch = async (url, options = {}) => {
    url = String(url);
    const request = {url, options}; f.requests.push(request);
    let status = 200, data = {};
    const authorized = f.allowed && options.headers?.Authorization !== 'Bearer expired';
    if (url === '/api/auth/me') data = {user:{id:101,username:'验收作者'},bindings:[]};
    if (url === '/api/auth/login') data = {token:'author-B',user:{id:202,username:'另一作者'}};
    if (url === '/api/announcements') data = {announcements:[],unread_count:0};
    if (url === '/shared-craft/author?access=1') data = {allowed:authorized};
    if (url.startsWith('/shared-craft/author?page=')) {
      status = authorized ? f.listStatus : 403;
      const page = Number(new URL(url, location.href).searchParams.get('page'));
      const items = Array.from({length:f.count}, (_, i) => ({
        id: `private-submission-${i}`, title:'共同画作', note:`太好玩了！\n来自主动分享者 ${i} <script>原样附言</script>`,
        created_at:1792400000 + i,
      })).slice((page-1)*10, page*10);
      data = {items, total:f.count, page, pages:Math.max(1, Math.ceil(f.count/10)), has_prev:page>1, has_next:page*10<f.count};
    }
    const isImage = url.startsWith('/shared-craft/author/');
    if (isImage) status = authorized ? f.imageStatus : 403;
    if (url === '/api/candle/feedback?access=1') data = {allowed:true};
    if (url.startsWith('/api/candle/feedback?page=')) data = {
      items:[{created_at:1792400000,chosen:2,comment:'烛的原有影评'},{created_at:1792400000,chosen:0,comment:''}],
      page:1,pages:1,total:2,has_prev:false,has_next:false,
    };
    const bytes = Uint8Array.from(atob(png), c => c.charCodeAt(0));
    const response = {ok:status===200,status,json:async()=>data,blob:async()=>new Blob([bytes], {type:f.imageType})};
    const kind = isImage ? 'image' : url.includes('author?page=') ? 'list' : url.includes('author?access=') ? 'access' : '';
    if (f.hold === kind && kind) {
      f.hold = '';
      // Deliberately ignore abort to verify stale-response guards as well as signals.
      return new Promise(resolve => f.holds.push({request, release:()=>resolve(response)}));
    }
    if (isImage && f.hold === 'blob') {
      f.hold = '';
      response.blob = () => new Promise(resolve => f.holds.push({request, release:()=>resolve(new Blob([bytes],{type:'image/png'}))}));
    }
    return response;
  };
}

async function checks() {
  const f = window.authorFixture, $ = id => document.getElementById(id);
  const ok = (condition, message) => { if (!condition) throw new Error(message); };
  const tick = async () => { for (let i=0;i<5;i++) await new Promise(r=>setTimeout(r,0)); };
  const images = () => f.requests.filter(r=>r.url.startsWith('/shared-craft/author/'));
  const rows = () => [...$('craftAuthorContent').querySelectorAll('details')];
  const open = async () => { window.openCraftAuthor(); await tick(); };
  const toggle = async (row, value) => { row.open = value; await tick(); };
  const clean = () => {
    ok(!$('craftAuthorContent').children.length, 'old author DOM must be empty');
    ok(f.created.every(url=>f.revoked.includes(url)), 'all image URLs must be revoked');
  };
  const session = async (token='author-A', id=101) => {
    localStorage.setItem('cedartoy_token', token);
    window.__authorSession(id);
    window.selectGame('shared_craft', false); await tick();
  };
  await tick(); await session(); await open();
  ok(rows().length===10, 'first page has ten rows');
  ok(rows().every(r=>!r.open && r.firstElementChild.matches('summary.candle-feedback-line')), 'Candle details start collapsed');
  ok(!images().length && !$('craftAuthorContent').querySelector('img'), 'no initial image or PNG fetch');
  ok(rows()[0].querySelector('time[datetime]') && rows()[0].querySelector('.candle-feedback-view').textContent==='查看', 'time and view marker');
  ok(!$('craftAuthorContent').textContent.includes('private-submission'), 'no internal IDs');
  await toggle(rows()[0], true);
  ok(images().length===1 && images()[0].url.endsWith('/private-submission-0'), 'only expanded image fetched');
  ok(images()[0].options.headers.Authorization==='Bearer author-A' && images()[0].options.cache==='no-store', 'authenticated uncached image');
  ok(rows()[0].querySelector('img') && rows()[0].textContent.includes('太好玩了！\n来自主动分享者 0 <script>原样附言</script>'), 'image and exact note');
  ok(!rows()[0].querySelector('script'), 'note is text, not HTML');
  await toggle(rows()[0], false); await toggle(rows()[0], true);
  ok(images().length===1, 'collapse/reopen reuses loaded image');
  await toggle(rows()[1], true);
  ok(images().length===2 && rows()[1].querySelector('img'), 'independent second image');
  const oldPageSignal = images()[0].options.signal;
  $('craftAuthorNext').click(); await tick();
  ok(oldPageSignal.aborted && f.created.every(url=>f.revoked.includes(url)), 'page aborts and revokes');
  ok(rows().length===2 && rows().every(r=>!r.open) && images().length===2, 'next page collapsed');
  $('craftAuthorPrev').click(); await tick(); ok(rows().length===10, 'previous page');

  f.imageStatus=500; await toggle(rows()[0],true);
  ok(rows()[0].textContent.includes('收起后展开重试') && !rows()[0].querySelector('img'), 'image failure is recoverable');
  f.imageStatus=200; await toggle(rows()[0],false); await toggle(rows()[0],true);
  ok(rows()[0].querySelector('img'), 'image retry succeeds');
  f.imageType='text/html'; await toggle(rows()[1],true);
  ok(!rows()[1].querySelector('img') && rows()[1].textContent.includes('重试'), 'non-PNG rejected'); f.imageType='image/png';

  // Pending image responses and blob reads must never repopulate a discarded page.
  for (const kind of ['image','blob']) {
    await open(); f.hold=kind; await toggle(rows()[0],true);
    const held=f.holds.pop(); ok(held, `${kind} request held`);
    $('craftAuthorNext').click(); await tick();
    ok(held.request.options.signal.aborted, `${kind} aborted by page`);
    const count=f.created.length; held.release(); await tick();
    ok(f.created.length===count && !document.querySelector('#craftAuthorContent img'), `${kind} cannot write stale image`);
  }
  // Late list responses, including after new requests for the same identity.
  window.closeModals(); f.hold='list'; await open(); const heldList=f.holds.pop();
  await open(); heldList.release(); await tick();
  ok(heldList.request.options.signal.aborted && rows().length===10, 'stale list cannot duplicate rows');
  f.listStatus=500; await open(); ok(!$('craftAuthorRetry').hidden, 'list retry shown');
  f.listStatus=200; $('craftAuthorRetry').click(); await tick(); ok(rows().length===10, 'list retry works');

  for (const action of ['close','escape','backdrop','closeModals','game','logout','login','storage']) {
    f.allowed=true; await session(); await open();
    ok(rows().length===10, `${action} setup: ${$('craftAuthorMsg').textContent}`);
    await toggle(rows()[0],true);
    f.hold='image'; await toggle(rows()[1],true); const held=f.holds.pop();
    if(action==='close') $('craftAuthorClose').click();
    if(action==='escape') $('craftAuthorModal').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    if(action==='backdrop') $('craftAuthorModal').click();
    if(action==='closeModals') window.closeModals();
    if(action==='game') window.selectGame('candle',false);
    if(action==='logout') window.logout();
    if(action==='login') {
      window.openModal('loginModal');
      $('loginUser').value='另一作者'; $('loginPass').value='fixture-only'; await window.login();
    }
    if(action==='storage') {
      localStorage.setItem('cedartoy_token','author-B');
      window.dispatchEvent(new StorageEvent('storage',{key:'cedartoy_token'}));
    }
    await tick(); clean(); ok(held.request.options.signal.aborted, `${action} aborts pending image`);
    ok(!$('craftAuthorModal').classList.contains('show'), `${action} hides dialog`);
    held.release(); await tick(); clean();
  }
  f.allowed=true; await session('author-B',202); await open(); await toggle(rows()[0],true);
  ok(images().at(-1).options.headers.Authorization==='Bearer author-B', 'new account loads with own credentials');
  // A grant/session can expire after list load: image 401/403 clears even loaded images and notes.
  for(const status of [401,403]) {
    f.imageStatus=200; await session(); await open(); await toggle(rows()[0],true);
    f.imageStatus=status; await toggle(rows()[1],true); clean();
    ok($('craftAuthorMsg').textContent.includes('权限') && $('craftAuthorRetry').hidden, 'permission failure is not a normal retry');
    ok([...document.querySelectorAll('[data-craft-author-entry]')].every(e=>e.style.display==='none'), 'entry removed on denied image');
  }
  f.imageStatus=200;
  for(const status of [401,403]) {
    f.listStatus=status; await session(); await open(); clean();
    ok($('craftAuthorMsg').textContent.includes('权限'), 'denied list clears');
  }
  f.listStatus=200; window.closeModals(); f.allowed=false; await session();
  let count=f.requests.length; await open(); ok(f.requests.length===count, 'unauthorized cannot request list'); clean();
  f.allowed=true; await session('expired'); count=f.requests.length; await open();
  ok(f.requests.length===count, 'expired account cannot request list'); clean();
  // Access-request race across two identities.
  f.hold='access'; await session(); const heldAccess=f.holds.pop();
  f.allowed=false; await session('author-B',202); heldAccess.release(); await tick();
  count=f.requests.length; await open(); ok(f.requests.length===count && heldAccess.request.options.signal.aborted, 'late access cannot regrant');
  f.allowed=true; await session(); f.count=0; await open();
  ok($('craftAuthorMsg').textContent.includes('暂无') && rows().length===0, 'empty list');
  // The original Candle renderer and summary interaction continue to work.
  window.closeModals(); window.selectGame('candle',false); await tick(); window.openCandleFeedback(); await tick();
  const candle=$('candleFeedbackContent');
  ok(candle.querySelectorAll('.candle-feedback-row').length===2, 'Candle rows intact');
  const detail=candle.querySelector('details'); ok(detail && !detail.open, 'Candle starts collapsed');
  await toggle(detail,true); ok(detail.textContent.includes('烛的原有影评'), 'Candle comment intact');
  await toggle(detail,false); window.closeModals(); ok(!candle.children.length, 'Candle close intact');
  return 'folding, lazy PNG, exact notes, pagination, retry, abort/late races, account/login/logout, close paths, 401/403, empty and Candle';
}

async function domChecks() {
  for(const width of widths) {
    const dom=new JSDOM(html,{url:'https://author-ui.test/',runScripts:'outside-only',pretendToBeVisual:true});
    const w=dom.window;
    w.matchMedia=q=>({matches:q.includes('599px')&&width<600,addEventListener(){},removeEventListener(){}});
    w.scrollTo=()=>{}; w.HTMLElement.prototype.scrollIntoView=()=>{};
    w.eval(`(${installFixture})();`);
    for(const script of w.document.querySelectorAll('script:not([src])')) w.eval(script.textContent);
    try { console.log(`PASS DOM ${width}px: ${await w.eval(`(${checks})()` )}`); }
    finally { w.close(); }
  }
}

async function browserChecks() {
  const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined,args:['--no-sandbox']});
  const output=process.env.AUTHOR_UI_SCREENSHOTS || '/tmp/shared-craft-author-ui';
  fs.mkdirSync(output,{recursive:true});
  try {
    for(const width of widths) {
      const page=await browser.newPage({viewport:{width,height:820},deviceScaleFactor:1});
      const errors=[]; page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*', route=>route.request().url()==='https://author-ui.test/' ? route.fulfill({contentType:'text/html',body:html}) : route.fulfill({status:204,body:''}));
      await page.addInitScript(installFixture);
      await page.goto('https://author-ui.test/');
      await page.evaluate(async()=>{ authorFixture.count=1; selectGame('shared_craft',false); await refreshCraftAuthorAccess(); openCraftAuthor(); });
      await page.waitForSelector('#craftAuthorContent summary');
      const geometry=async()=>page.evaluate(()=>{
        const box=document.querySelector('#craftAuthorModal .modal-box'), content=document.getElementById('craftAuthorContent');
        const b=box.getBoundingClientRect(), c=content.getBoundingClientRect();
        return {height:b.height,left:b.left,right:b.right,content:c.height,scroll:content.scrollHeight,overflow:getComputedStyle(content).overflowY,
          font:getComputedStyle(content.querySelector('summary')).fontSize,rows:content.querySelectorAll('details').length,
          pngRequests:authorFixture.requests.filter(r=>r.url.startsWith('/shared-craft/author/')).length};
      });
      const collapsed=await geometry();
      assert(collapsed.height<300 && collapsed.left>=16 && collapsed.right<=width-16,JSON.stringify(collapsed));
      assert.equal(collapsed.pngRequests,0); assert.equal(collapsed.font,'11px');
      await page.screenshot({path:path.join(output,`${width}-collapsed.png`)});
      // Keyboard opens the actual summary and triggers exactly one authenticated fetch.
      await page.locator('#craftAuthorContent summary').focus(); await page.keyboard.press('Enter');
      await page.waitForSelector('#craftAuthorContent img');
      await page.locator('#craftAuthorContent img').evaluate(async img=>{
        // A test-only canvas image gives the tiny transport fixture realistic dimensions.
        const canvas=document.createElement('canvas'); canvas.width=900; canvas.height=600;
        const ctx=canvas.getContext('2d'); ctx.fillStyle='#e6dff0'; ctx.fillRect(0,0,900,600);
        ctx.fillStyle='#9b8db5'; ctx.fillRect(230,110,440,380); img.src=canvas.toDataURL('image/png'); await img.decode();
      });
      const size=await page.locator('#craftAuthorContent img').evaluate(img=>({w:img.clientWidth,h:img.clientHeight}));
      assert(size.h<=230 && Math.abs(size.w/size.h-1.5)<0.02,JSON.stringify(size));
      assert.equal((await geometry()).pngRequests,1);
      await page.screenshot({path:path.join(output,`${width}-expanded.png`)});
      await page.keyboard.press('Enter');
      await page.waitForFunction(()=>!document.querySelector('#craftAuthorContent details').open);
      assert.equal((await geometry()).height,collapsed.height);
      // Summary participates in the original dialog focus loop.
      await page.keyboard.press('Tab'); assert.equal(await page.evaluate(()=>document.activeElement.id),'craftAuthorClose');
      await page.keyboard.press('Shift+Tab'); assert.equal(await page.evaluate(()=>document.activeElement.tagName),'SUMMARY');
      await page.evaluate(async()=>{authorFixture.count=12; await loadCraftAuthor(1);});
      const full=await geometry(); assert(full.height<=680 && full.rows===10 && full.overflow==='auto');
      await page.screenshot({path:path.join(output,`${width}-ten-rows.png`)});
      await page.locator('#craftAuthorNext').click(); await page.waitForFunction(()=>document.querySelectorAll('#craftAuthorContent details').length===2);
      await page.evaluate(()=>{closeModals(); authorFixture.requests.length=0;});
      // The exhaustive account/race tests run in domChecks() with fresh fixtures.
      // Avoid re-running them in the already-mutated screenshot fixture.
      assert.deepEqual(errors,[]);
      console.log(`PASS Chromium ${width}px: folding/layout/lazy image preview; one row ${collapsed.height}px, ten rows ${full.height}px`);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log(`Screenshots: ${output}`);
}
(async()=>{ if (!process.argv.includes('--browser-only')) await domChecks();
if(process.argv.includes('--browser') || process.argv.includes('--browser-only')) await browserChecks();
else console.log('DOM checks only; use --browser for actual layout and screenshots.');
})().catch(error=>{console.error(error);process.exitCode=1;});
