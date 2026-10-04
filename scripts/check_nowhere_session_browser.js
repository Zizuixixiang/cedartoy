const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const cfg=JSON.parse(fs.readFileSync(process.env.NOWHERE_BROWSER_CONFIG,'utf8'));
const output=process.env.NOWHERE_SCREENSHOTS;
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--no-proxy-server','--host-resolver-rules=MAP nowhere.test 127.0.0.1']});
 try {
  const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width:360,height:900},hasTouch:true});
  await page.route('**/*',r=>new URL(r.request().url()).hostname==='nowhere.test'?r.continue():r.abort());
  if(process.env.NOWHERE_SESSION_BASELINE){
   await page.goto(cfg.httpOrigin+'/');
   await page.evaluate(token=>localStorage.setItem('cedartoy_token',token),cfg.token);
   await page.reload();await page.waitForFunction(()=>Boolean(me?.user));
   await page.locator('#searchInput').fill('乌有乡');await page.locator('[data-game="nowhere"]').tap();
   await page.locator('#drawerWatchButton').click();await page.locator('[data-nowhere-save]').first().click();
   await page.waitForURL(url=>url.pathname==='/nowhere/'&&!url.searchParams.has('token'));
   assert((await page.locator('body').innerText()).includes('未登录'));
   assert.equal((await page.context().cookies()).filter(c=>c.name==='nowhere_token').length,0);
   await page.screenshot({path:path.join(output,'360-http-before.png')});
   fs.writeFileSync(path.join(output,'baseline.json'),JSON.stringify({host:'nowhere.test',scheme:'http',secureCookieRetained:false,rawJson401:true}));
   console.log('REPRODUCED: ordinary HTTP hostname loses Secure cookie after 303');
   return;
  }
  await page.close();
  const reports=[];
  async function session(width) {
   const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width,height:900},hasTouch:width<600});
   page.setDefaultTimeout(15000);
   const errors=[],requests=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{const u=new URL(r.url());requests.push({path:u.pathname,scheme:u.protocol,queryToken:u.searchParams.has('token'),auth:!!r.headers().authorization,navigation:r.isNavigationRequest()});});
   await page.route('**/*',r=>new URL(r.request().url()).hostname==='nowhere.test'?r.continue():r.abort());
   const shot=name=>page.screenshot({animations:'disabled',path:path.join(output,`${width}-${name}.png`)});
   async function seed(origin,token) {
    await page.goto(origin+'/');
    await page.evaluate(token=>localStorage.setItem('cedartoy_token',token),token);
    await page.reload();await page.waitForFunction(()=>Boolean(me?.user));
   }
   async function enter() {
    await page.locator('#searchInput').fill('乌有乡');
    const card=page.locator('[data-game="nowhere"]');
    if(width<600)await card.tap();else await card.hover();
    await page.locator(width<600?'#drawerWatchButton':'#watchButton').click();
   }
   async function map() {
    await page.waitForFunction(()=>typeof CARDS!=='undefined'&&CARDS.length>0&&document.querySelector('#now .text')?.textContent.trim().length>0);
    assert.equal(new URL(page.url()).protocol,'https:');
    assert.equal(new URL(page.url()).searchParams.get('player'),'101:2');
    assert(!new URL(page.url()).searchParams.has('token'));
   }
   return {page,errors,requests,shot,seed,enter,map};
  }
  for(const width of [360,428,1280]) {
   const {page,errors,requests,shot,seed,enter,map}=await session(width);
   // Existing logins on both origins. Only the HTTPS token is used after upgrade.
   await seed(cfg.origin,cfg.token);await seed(cfg.httpOrigin,cfg.token);
   await enter();await page.locator('[data-nowhere-save]').first().waitFor();
   assert.equal(new URL(page.url()).origin,cfg.origin);
   await shot('secure-picker');
   await page.locator('[data-nowhere-save]').filter({hasText:'槽2'}).click();await map();
   await page.reload();await map();
   assert.equal(await page.locator('#platform-credit').count(),0);
   assert.equal(await page.locator('body').innerText().then(t=>/关于乌有乡|返回 4399|CedarToy|第三方数据许可/.test(t)),false);
   assert.equal(await page.locator('#platform-status').isVisible(),false);
   assert.equal(await page.locator('#wallbtn').evaluate(n=>getComputedStyle(n).writingMode),'vertical-rl');
   await shot('map');
   await page.locator('#zoomrange').evaluate(n=>{n.value='2';n.dispatchEvent(new Event('input'));});
   await page.locator('#trailopen').click();
   await page.waitForFunction(()=>Math.abs(document.querySelector('#trail').getBoundingClientRect().left)<.1);
   await page.locator('.trailitem').first().waitFor();
   await shot('trail');
   await page.locator('#trailback').click();
   const cookies=await page.context().cookies();
   const cookie=cookies.find(c=>c.name==='nowhere_token');
   assert(cookie?.secure&&cookie.httpOnly&&cookie.sameSite==='Strict');
   await page.locator('#wallbtn').click();
   if(!await page.locator('.mini').count())await page.locator('#tabReplied').click();
   await page.locator('.mini').first().click();
   await page.waitForFunction(()=>[...document.querySelectorAll('#flipcard img')].some(i=>i.complete&&i.naturalWidth>0));
   await page.locator('#replyin').fill('HTTPS-REPLY-'+width);
   await page.getByRole('button',{name:'寄回',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('#replyin')?.value==='');
   await shot('postcard');
   const checks=await page.evaluate(async({id,image,width})=>{
    const paths=['/state','/messages','/postcards','/static/'+image,'/postcard/'+id+'/reply','/postcard/'+id];
    const codes=[];
    for(const [i,p] of paths.entries()) {
     const opts=i>=4?{method:i===4?'POST':'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:'forbidden',confirm:true})}:{};
     codes.push((await fetch('/nowhere'+p+'?player=202',opts)).status);
    }
    const response=await fetch('/nowhere/postcards?player=101:2');
    if(!response.headers.get('content-type')?.includes('application/json')) return {codes,status:response.status,path:new URL(response.url).pathname,type:response.headers.get('content-type')};
    const cards=await response.json();
    return {codes,reply:JSON.stringify(cards).includes('HTTPS-REPLY-'+width),image:(await fetch('/nowhere/static/'+image+'?player=101:2')).status};
   },{id:cfg.cardId,image:cfg.image,width});
   assert(checks.codes.every(c=>c===403));assert(checks.reply);assert.equal(checks.image,200);
   // Lost cookie/direct entry recovers from the ordinary platform login once.
   await page.context().clearCookies();
   await page.goto(cfg.origin+'/nowhere/?player=101:2');
   try { await map(); } catch(e) {
    console.log('RECOVERY',await page.evaluate(()=>({path:location.pathname,body:document.body.innerText.slice(0,180),tokenPresent:!!localStorage.getItem('cedartoy_token'),cards:typeof CARDS==='undefined'?null:CARDS.length})),errors);
    await shot('recovery-failed');throw e;
   }
   assert((await page.context().cookies()).some(c=>c.name==='nowhere_token'));
   const dataRequests=requests.filter(r=>r.path.startsWith('/nowhere/')||r.path==='/api/nowhere/saves');
   assert(dataRequests.every(r=>r.scheme==='https:'&&!r.queryToken));
   assert(dataRequests.filter(r=>r.navigation).every(r=>!r.auth));
   assert(dataRequests.some(r=>r.path.endsWith(cfg.image)&&!r.auth));
   const mobile=await page.evaluate(()=>matchMedia('(pointer: coarse)').matches&&/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent));
   const radio=await require('./check_nowhere_radio_click').checkRadioClick(page,mobile);
   assert.deepEqual(errors,[]);
   reports.push({width,httpsEntry:true,refresh:true,cookieRecovery:true,originalUI:true,trail:true,image:true,reply:true,radio,foreignCodes:checks.codes,errors});
   await page.close();
  }
  // A stale slot selection must not fall back to a different, older cookie.
  {
   const {page,errors,seed,enter,map}=await session(360);
   await seed(cfg.origin,cfg.token);await enter();
   await page.locator('[data-nowhere-save]').first().click();await map();
   await seed(cfg.origin,cfg.otherToken);
   await page.evaluate(()=>watchNowhere(101,2));
   assert.equal(new URL(page.url()).pathname,'/');
   await page.getByText('无法打开这个旅程',{exact:false}).waitFor();
   await page.evaluate(t=>localStorage.setItem('cedartoy_token',t),cfg.expiredToken);
   await page.evaluate(()=>watchNowhere(101,2));
   await page.locator('#loginModal.show').waitFor();
   assert.equal(new URL(page.url()).pathname,'/');
   assert.deepEqual(errors,[]);await page.close();
   reports.push({rejectedCurrentLogin:'no navigation using an older cookie'});
  }
  // HTTP-only login cannot leak across localStorage origins or auto-loop.
  {
   const {page,errors,requests,shot,seed,enter}=await session(360);
   await seed(cfg.httpOrigin,cfg.token);await enter();
   await page.locator('#loginModal.show').waitFor();
   assert.equal(new URL(page.url()).origin,cfg.origin);
   assert.equal(await page.evaluate(()=>localStorage.getItem('cedartoy_token')),null);
   assert.equal(await page.locator('#gameDrawer.show').count(),0);
   await shot('https-login');
   assert(!requests.some(r=>r.scheme==='http:'&&(r.path.startsWith('/nowhere/')||r.path==='/api/nowhere/saves')));
   assert.deepEqual(errors,[]);await page.close();
   reports.push({httpOnlyLogin:'HTTPS login prompt; no credential transfer'});
  }
  // Missing and expired sessions render real HTML, with no MCP token advice.
  for(const mode of ['missing','expired','http-direct','other-account']) {
   const {page,errors,shot}=await session(360);
   const origin=mode==='http-direct'?cfg.httpOrigin:cfg.origin;
   await page.goto(origin+'/');
   if(mode==='expired') {
    await page.evaluate(t=>localStorage.setItem('cedartoy_token',t),cfg.expiredToken);
    await page.context().addCookies([{name:'nowhere_token',value:cfg.expiredToken,domain:'nowhere.test',path:'/nowhere/',secure:true,httpOnly:true,sameSite:'Strict'}]);
   }
   if(mode==='other-account')await page.evaluate(t=>localStorage.setItem('cedartoy_token',t),cfg.otherToken);
   const response=await page.goto(origin+'/nowhere/?player=101:2');
   assert(response.headers()['content-type'].includes('text/html'));
   await page.locator('#access-home').waitFor();
   if(mode==='other-account') await page.getByText('当前账号无法查看这个旅程',{exact:false}).waitFor();
   assert(!(await page.locator('body').innerText()).includes('MCP'));
   if(mode==='http-direct') {
    assert.equal(response.status(),426);
    assert((await page.locator('#access-secure').getAttribute('href')).startsWith('https://'));
   }
   await shot(mode);
   await page.locator('#access-home').click();
   await page.waitForURL(url=>url.pathname==='/');
   assert.equal(new URL(page.url()).protocol,'https:');
   assert.deepEqual(errors,[]);await page.close();
   reports.push({mode,friendlyDocument:true});
  }
  // Expiry while the map is already open must also leave the user a usable page.
  {
   const {page,errors,seed,enter,map,shot}=await session(428);
   await seed(cfg.origin,cfg.token);await enter();
   await page.locator('[data-nowhere-save]').first().click();await map();
   await page.context().clearCookies();
   await page.evaluate(t=>localStorage.setItem('cedartoy_token',t),cfg.expiredToken);
   await page.waitForSelector('#access-home');
   await shot('expired-open-map');
   assert(!(await page.locator('body').innerText()).includes('MCP'));
   assert.deepEqual(errors,[]);await page.close();
   reports.push({expiryDuringPolling:'friendly recovery page'});
  }
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(reports,null,2));
  console.log(JSON.stringify(reports));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
