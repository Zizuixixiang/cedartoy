/* Focused radio test: real auth/session, synthetic game payload, no real player data. */
const assert = require('node:assert/strict');

async function checkRadioClick(page, mobile) {
  const radio = page.locator('.traillink').filter({hasText:'RADIO-HTTPS电台'});
  await radio.waitFor({state:'attached'});
  const unsafe = page.locator('.trailitem').filter({hasText:'TEST-UNSAFE-'});
  assert.equal(await unsafe.count(),4);
  assert.equal(await unsafe.locator('a').count(),0);
  assert.equal(await radio.getAttribute('target'),'_blank');
  assert.equal(await radio.getAttribute('rel'),'noopener noreferrer');
  const href = await radio.getAttribute('href'), before = page.url();
  const destination = new URL(href, before);
  assert.equal(destination.origin, new URL(before).origin);
  assert.equal(destination.pathname, '/nowhere/radio');
  const upstream = destination.searchParams.get('url');
  if(process.env.NOWHERE_SCREENSHOTS){
    const fs=require('node:fs'),path=require('node:path');fs.mkdirSync(process.env.NOWHERE_SCREENSHOTS,{recursive:true});
    await page.screenshot({path:path.join(process.env.NOWHERE_SCREENSHOTS,`${page.viewportSize().width}-map.png`)});
  }
  await page.locator('#trailopen').click();
  await page.waitForFunction(()=>Math.abs(document.getElementById('trail').getBoundingClientRect().left)<0.1);
  let popups = 0;
  const countPopup = ()=>popups++;
  page.on('popup',countPopup);
  if (!mobile) {
    const [popup] = await Promise.all([page.waitForEvent('popup'),radio.click()]);
    await popup.waitForURL(destination.href);
    assert.equal(popups,1);
    await popup.close();
    assert.equal(page.url(),before);
  } else {
    const streamResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/nowhere/radio/stream');
    await radio.click();
    await page.waitForURL(u=>u.pathname==='/nowhere/radio');
    const url=new URL(page.url()), original=new URL(before);
    assert.equal(url.origin,original.origin);
    assert.equal(url.searchParams.get('player'),original.searchParams.get('player'));
    assert.equal(url.searchParams.get('url'),upstream);
    const audio=page.locator('audio');
    assert.equal(await audio.count(),1);
    const source=new URL(await audio.getAttribute('src'),page.url());
    assert.equal(source.origin,original.origin);
    assert.equal(source.pathname,'/nowhere/radio/stream');
    assert.equal(source.searchParams.get('player'),original.searchParams.get('player'));
    const streamUrl=new URL(upstream);streamUrl.hash='';
    assert.equal(source.searchParams.get('url'),streamUrl.href);
    assert.equal((await streamResponse).status(),200);
    await page.waitForFunction(()=>document.querySelector('audio').readyState>=2);
    for(const attr of ['controls','autoplay','playsinline'])assert.notEqual(await audio.getAttribute(attr),null);
    assert.equal(await page.locator('script').count(),0);
    if(process.env.NOWHERE_SCREENSHOTS) {
      const fs=require('node:fs'),path=require('node:path');fs.mkdirSync(process.env.NOWHERE_SCREENSHOTS,{recursive:true});
      await page.screenshot({path:path.join(process.env.NOWHERE_SCREENSHOTS,`${page.viewportSize().width}-radio.png`)});
    }
    await page.getByRole('link',{name:'返回旅程',exact:true}).click();
    await page.waitForURL(before);
    await radio.waitFor({state:'attached'});
    const status=await page.evaluate(()=>fetch('/state').then(r=>r.status));
    assert.equal(status,200);
    await page.locator('#trailopen').click();
    await radio.waitFor({state:'visible'});
    assert.equal(popups,0);
  }
  assert.equal(await radio.getAttribute('href'),href);
  page.off('popup',countPopup);
  return {mobile,popups,sameOrigin:true,returnedToJourney:true,unsafeLinks:0};
}

async function checkHistoryRadio(page,mobile,origin) {
  const entry=origin+'/nowhere/?player=101';
  await page.goto(entry);
  await page.locator('#traillist .trailitem').first().waitFor({state:'attached'});
  const check=await page.evaluate(async()=>{
    const state=await fetch('/state').then(r=>r.json());
    const history=await fetch('/history').then(r=>r.json());
    const extraLinks=()=>document.querySelectorAll('#nowstats a, .trailhead a').length;
    renderNow(state);
    const extras=extraLinks();
    renderNow({...state,radio:null});
    const noRadioExtras=extraLinks();
    renderNow(state);
    const unsafe=['','/relative','javascript:alert(1)','data:audio/mp3,x','file:///tmp/x','ftp://x/stream'];
    return {extras,noRadioExtras,actions:history.footprints.filter(f=>!f.legacy),
            badLinks:unsafe.map(url=>safeHttpUrl(url)),
            header:document.querySelector('.trailhead').textContent.trim().replace(/\s+/g,' ')};
  });
  assert.equal(check.extras,0);assert.equal(check.noRadioExtras,0);
  assert.equal(check.header,'← 返回地图旅行足迹');
  assert(check.badLinks.every(value=>!value));
  assert.equal(check.actions.length,5);
  const href='https://icecast.radiofrance.fr/fip-midfi.mp3';
  assert(check.actions.every(f=>f.stream_url===href&&f.station.name==='FIP'));
  const links=page.locator('#traillist .trailitem a.traillink');
  assert((await links.count())>=5);
  for(const link of await links.all()) {
    assert.equal(await link.innerText(),'打开 FIP电台流 ↗');
    const destination=new URL(await link.getAttribute('href'),origin);
    assert.equal(destination.origin,origin);
    assert.equal(destination.pathname,'/nowhere/radio');
    assert.equal(destination.searchParams.get('url'),href);
    assert.equal(await link.getAttribute('target'),'_blank');
    assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
    assert(await link.evaluate(a=>a.parentElement.classList.contains('trailtext')&&a.previousElementSibling.tagName==='BR'));
  }
  await page.locator('#trailopen').click();
  await page.waitForFunction(()=>Math.abs(document.getElementById('trail').getBoundingClientRect().left)<0.1);
  if(process.env.NOWHERE_SCREENSHOTS){
    const path=require('node:path');
    await page.screenshot({path:path.join(process.env.NOWHERE_SCREENSHOTS,`${page.viewportSize().width}-trail-actions.png`)});
  }
  const link=links.first();
  if(process.env.NOWHERE_SCREENSHOTS){
    const path=require('node:path');
    for(let i=0;i<await links.count();i++){
      const actionLink=links.nth(i);
      // Upstream replaces trail nodes on each poll; scroll in one JS turn.
      await actionLink.evaluate(a=>a.scrollIntoView({block:'center'}));
      assert(await actionLink.isVisible());
      await page.screenshot({path:path.join(process.env.NOWHERE_SCREENSHOTS,`${page.viewportSize().width}-trail-action-${i+1}.png`)});
    }
  }
  let popups=0;const onPopup=()=>popups++;page.on('popup',onPopup);
  if(mobile){
    const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/nowhere/radio/stream');
    await link.click();await page.waitForURL(u=>u.pathname==='/nowhere/radio');
    const location=new URL(page.url());
    assert.equal(location.origin,origin);assert.equal(location.searchParams.get('player'),'101');
    assert.equal(location.searchParams.get('url'),href);
    assert.equal((await response).status(),200);
    await page.waitForFunction(()=>document.querySelector('audio').readyState>=2);
    assert.equal(new URL(await page.locator('audio').getAttribute('src'),origin).pathname,'/nowhere/radio/stream');
    await page.getByRole('link',{name:'返回旅程',exact:true}).click();await page.waitForURL(entry);
    await links.first().waitFor({state:'attached'});assert.equal(popups,0);
  }else{
    const [popup]=await Promise.all([page.waitForEvent('popup'),link.click()]);
    await popup.waitForURL(u=>u.origin===origin&&u.pathname==='/nowhere/radio'&&u.searchParams.get('url')===href);await popup.close();assert.equal(page.url(),entry);assert.equal(popups,1);
    await page.locator('#trailback').click();
  }
  assert.equal(await page.locator('#trail.open').count(),0);
  assert.equal(await page.locator('#nowstats a, .trailhead a').count(),0);
  page.off('popup',onPopup);
  return {historyEnrichedActions:check.actions.length,originalTrailMarkup:true,mapReturn:true,popups};
}

module.exports = {checkRadioClick};

if (require.main === module) (async()=>{
  const fs=require('node:fs');
  const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
  const cfg=JSON.parse(fs.readFileSync(process.env.NOWHERE_BROWSER_CONFIG,'utf8'));
  const browser=await chromium.launch({headless:true,args:['--no-sandbox','--no-proxy-server','--host-resolver-rules=MAP nowhere.test 127.0.0.1']});
  const results=[];
  try {
    for(const test of [
      {name:'desktop-mouse',width:1280,hasTouch:false,mobile:false},
      {name:'desktop-touch',width:360,hasTouch:true,mobile:false},
      {name:'ios-touch',width:428,hasTouch:true,mobile:true,userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1'},
      {name:'android-touch',width:360,hasTouch:true,mobile:true,userAgent:'Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36'}
    ]) {
      console.log('Radio browser context:',test.name);
      const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:test.width,height:900},hasTouch:test.hasTouch,...(test.userAgent?{userAgent:test.userAgent}:{})});
      const mediaRequests=[];
      await context.route('**/*',route=>{
        const u=new URL(route.request().url());
        if(u.hostname==='nowhere.test')return route.continue();
        if(['radio.example.test','icecast.radiofrance.fr'].includes(u.hostname)){
          mediaRequests.push(route.request().resourceType());
          return route.fulfill({contentType:'audio/mpeg',body:Buffer.from([0xff,0xe3,0x18,0xc4])});
        }
        return route.abort();
      });
      // Establish the existing Secure cookie through the real authenticated
      // document response; subsequent navigations carry no Bearer header.
      const entry=cfg.origin+'/nowhere/?player=101%3A2';
      const page=await context.newPage(),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      page.setDefaultTimeout(10000);
      await page.goto(cfg.origin+'/nowhere/platform.js');
      const login=await page.evaluate(async({entry,token})=>(await fetch(entry,{headers:{Authorization:'Bearer '+token},credentials:'same-origin'})).status,{entry,token:cfg.token});
      assert.equal(login,200);
      const before=await context.cookies();
      assert(before.some(c=>c.name==='nowhere_token'&&c.secure&&c.httpOnly));
      await page.evaluate(token=>localStorage.setItem('cedartoy_token',token),cfg.token);
      const pickerRequests=[];
      page.on('request',r=>{const p=new URL(r.url()).pathname;if(p.endsWith('/saves'))pickerRequests.push(p);});
      await page.goto(cfg.origin+'/');
      await page.waitForFunction(()=>Boolean(me?.user));
      await page.locator('#searchInput').fill('乌有乡');
      const card=page.locator('[data-game="nowhere"]');
      if(test.width<600)await card.click();else await card.hover();
      await page.locator(test.width<600?'#drawerWatchButton':'#watchButton').click();
      const option=page.locator('[data-nowhere-save]').filter({hasText:'槽2'});
      await option.waitFor();
      assert.deepEqual(pickerRequests,['/api/nowhere/saves']);
      await option.click();
      await page.waitForURL(u=>u.pathname==='/nowhere/'&&u.searchParams.get('player')==='101:2');
      assert.equal(await page.locator('.trailitem').filter({hasText:'TEST-NO-STREAM-'}).count(),3);
      assert.equal(await page.locator('.trailitem').filter({hasText:'TEST-NO-STREAM-'}).locator('a').count(),0);
      results.push({name:test.name,pickerEndpoint:'/api/nowhere/saves',...await checkRadioClick(page,test.mobile)});
      results.push({name:test.name,...await checkHistoryRadio(page,test.mobile,cfg.origin)});
      assert.deepEqual(await context.cookies(),before,'radio must not rotate credentials');
      assert.deepEqual(mediaRequests,[],'all devices must use the platform proxy');
      const denied=await page.evaluate(()=>fetch('/nowhere/radio?player=202&url=https://radio.example.test/fip.mp3').then(r=>r.status));
      assert.equal(denied,403);
      assert.deepEqual(errors,[]);
      await context.close();
      console.log('Radio browser context passed:',test.name);
    }
  } finally {await browser.close();}
  console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exitCode=1;});
