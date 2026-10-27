// Only use disposable accounts and a loopback fixture. Never a production token.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const output=process.env.AUTOCHESS_BROWSER_OUTPUT||'artifacts/autochess-stage2';
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 fs.rmSync(`${output}/browser-result.json`,{force:true});
 const origin=process.env.AUTOCHESS_BROWSER_ORIGIN;
 assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin));
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader']});
 const errors=[],requests=[],roomMetrics=[];
 try{
 if(!process.env.AUTOCHESS_BROWSER_SKIP_HOME){
 const homeContext=await browser.newContext();
 const home=await homeContext.newPage();
 await homeContext.addInitScript(token=>{if(location.hostname==='127.0.0.1')localStorage.setItem('cedartoy_token',token);},process.env.AUTOCHESS_BROWSER_TOKEN);
 home.on('pageerror',e=>errors.push(e.message));
 await home.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
   if(path.startsWith('/assets/'))return route.continue();
   if(path==='/api/auth/me')return route.fulfill({json:{user:{id:101,username:'临时棋手',is_ai:false,avatar:{type:'emoji',value:'🙂'}},bindings:[]}});
   return route.fulfill({json:{}});
 });
 await home.goto(origin+'/');
 await home.locator('.game-card[data-game="autochess"]').hover();
 assert.equal(await home.locator('#autochessStatsModal, [data-autochess-stats]').count(),0,'Legacy stats popup was removed from the home game card');
 assert.equal(await home.locator('#enterButton').textContent(),'作者原版 ↗');
 assert.equal(await home.locator('#detailDesc a').getAttribute('href'),'https://github.com/Cecilia-coding/AutoChess-MCP');
 assert.match(await home.locator('#detailDesc').textContent(),/MIT/);
 assert.equal(await home.locator('#watchButton').textContent(),'进入棋室 →');
 let sourceRequest;
 await home.route('https://play.ceciliaceci.me/**',route=>{sourceRequest=route.request();return route.abort();});
 await home.locator('#enterButton').click();
 await home.waitForTimeout(100);
 assert.equal(sourceRequest?.url(),'https://play.ceciliaceci.me/');
 assert.equal(sourceRequest.headers().authorization,undefined);
 await home.goto(origin+'/');await home.locator('.game-card[data-game="autochess"]').hover();
 await home.locator('#watchButton').click();await home.waitForURL(origin+'/autochess/');
 await homeContext.close();
 }
 const context=await browser.newContext();
 // Simulate mobile WebViews lacking randomUUID but with secure getRandomValues.
 await context.addInitScript(() => {Object.defineProperty(window.crypto, "randomUUID", {value:undefined, configurable:true});});
 await context.addInitScript(token=>localStorage.setItem('cedartoy_token',token),process.env.AUTOCHESS_BROWSER_TOKEN);
 const page=await context.newPage();page.setDefaultTimeout(15000);
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push({url:r.url(),method:r.method()}));
 const nav=name=>page.locator('.lobby-tabs').getByRole('button',{name:new RegExp(name)});
 const create=()=>page.getByRole('button',{name:/✦ (辟立对弈棋局|开启弈决) ✦/});
 const screenshots=async(name,fullPage=false)=>{
  for(const width of [360,430,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(()=>window.scrollTo(0,0));
   await page.screenshot({path:`${output}/${name}-${width}.png`,fullPage});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),name+' overflow '+width);
  }
 };
 const landscapeSizes=[[740,345],[844,390],[960,430],[1024,480],[1536,690]];
 const landscapeChecks=async(phase)=>{
  const cdp=await context.newCDPSession(page);
  for(const [width,height] of landscapeSizes){
   await cdp.send('Emulation.setSafeAreaInsetsOverride',{insets:width===844?{top:0,left:24,right:24,bottom:16}:{top:0,left:0,right:0,bottom:0}});
   console.log('LANDSCAPE',phase,width,height);await page.setViewportSize({width,height});await page.waitForTimeout(350);
   await page.screenshot({path:`${output}/landscape-${phase}-base-${width}.png`});
   // Native full-screen is exercised, with Chromium viewport emulation retained.
   await page.locator('.peach-fullscreen').click();await page.waitForFunction(()=>!!document.fullscreenElement);

   const fullscreenHits=await page.evaluate(()=>['.peach-fullscreen','.room-panel-toggle','.room-float>summary','.peach-inventory summary','.peach-shop-toggle'].map(selector=>{const el=document.querySelector(selector),r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {selector,ok:el===hit||el.contains(hit)};}));
   assert(fullscreenHits.every(h=>h.ok),'fullscreen utility hits');roomMetrics.push({kind:'fullscreen',phase,width,height,hits:fullscreenHits});
   if(width===740||width===1536)await page.screenshot({path:`${output}/fullscreen-${phase}-${width}.png`});
   await page.locator('.peach-fullscreen').click();await page.waitForFunction(()=>!document.fullscreenElement);
   await page.getByLabel('放大棋盘',{exact:true}).click();await page.getByLabel('重置视角',{exact:true}).click();
   const measure=async()=>page.evaluate(()=>{
    const selectors=['.peach-camera-overlay','.peach-enemy-bench','.room-panel-toggle','.peach-inventory','.peach-dock','.room-versus-hud','.room-float>summary'];
    const boxes=selectors.map(selector=>{const e=document.querySelector(selector),r=e.getBoundingClientRect();return {selector,x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};});
    const targets=['.peach-fullscreen','.room-panel-toggle','.peach-enemy-bench summary','.peach-inventory summary','.room-float>summary','.peach-shop-toggle','.peach-trash'];
    return {boxes,hits:targets.map(selector=>{const e=document.querySelector(selector),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {selector,ok:e===hit||e.contains(hit)};}),overflow:document.documentElement.scrollWidth-innerWidth};
   });
   const metric=await measure();roomMetrics.push({kind:'landscape',phase,width,height,safeArea:width===844?{left:24,right:24,bottom:16}:null,...metric});
   if(width===844){assert(metric.boxes.every(b=>b.x>=24&&b.right<=width-24));assert(metric.boxes.find(b=>b.selector==='.peach-dock').bottom<=height-16);}
   assert(metric.overflow<=1);assert(metric.hits.every(hit=>hit.ok),JSON.stringify(metric.hits));
   for(let i=0;i<metric.boxes.length;i++)for(let j=i+1;j<metric.boxes.length;j++){
    const a=metric.boxes[i],b=metric.boxes[j];assert(!(a.x<b.right&&a.right>b.x&&a.y<b.bottom&&a.bottom>b.y),`overlap ${width} ${a.selector} ${b.selector}`);
   }
   await page.getByLabel('房间菜单',{exact:true}).click();await page.getByRole('button',{name:'返回棋室（保留席位）',exact:true}).waitFor();
   const open=await measure();assert(open.hits.every(hit=>hit.ok),'menu does not cover utility controls');
   await page.screenshot({path:`${output}/landscape-${phase}-menu-${width}.png`});
   await page.getByRole('button',{name:'收起房间菜单',exact:true}).click();
   await page.locator('.peach-inventory summary').click();await page.screenshot({path:`${output}/landscape-${phase}-inventory-${width}.png`});
   const inventory=await measure();assert(inventory.hits.every(hit=>hit.ok),'expanded inventory controls remain reachable');
   await page.locator('.peach-enemy-bench summary').click();assert(!await page.locator('.peach-inventory').evaluate(el=>el.open),'utilities collapse each other');
   await page.screenshot({path:`${output}/landscape-${phase}-enemy-${width}.png`});
   await page.locator('.room-panel-toggle').click();assert(!await page.locator('.peach-enemy-bench').evaluate(el=>el.open));
   await page.screenshot({path:`${output}/landscape-${phase}-info-${width}.png`});await page.locator('.room-panel-toggle').click();
  }
  await cdp.send('Emulation.setSafeAreaInsetsOverride',{insets:{top:0,left:0,right:0,bottom:0}});await cdp.detach();
 };
 const anon=await browser.newPage();
 await anon.goto(origin+'/autochess/');await anon.locator('.pack-tome').first().waitFor();
 assert.equal(await anon.locator('.cedar-top').count(),0);
 assert.equal(await anon.locator('.lobby-top').count(),1);
 assert.equal(await anon.title(),'弈间');
 assert.equal(await anon.getByRole('link',{name:'前往登录'}).count(),1);
 assert(await anon.getByRole('button',{name:/✦ 辟立/}).isDisabled());
 await anon.setViewportSize({width:360,height:900});await anon.screenshot({path:`${output}/logged-out-360.png`,fullPage:true});await anon.close();
 await page.goto(origin+'/autochess/');await page.waitForFunction(()=>!document.querySelector('.lobby-primary')?.disabled);
 assert.equal(await page.evaluate(()=>typeof crypto.randomUUID), 'undefined');
 assert.equal(await page.evaluate(()=>typeof crypto.getRandomValues), 'function');
 assert.equal(await page.title(),'弈间');
 assert.equal(await page.locator('.cedar-top').count(),0);
 assert.equal(await page.locator('.lobby-top').count(),1);assert.equal(await page.locator('.lobby-brand h1').textContent(),'弈间');
 assert.equal(await page.locator('.lobby-top').getByText('临时棋手').count(),0);
 // Game-only management: actual six-range API, isolated UI, failures and account races.
 await nav('我的').click();await page.getByRole('button',{name:'弈间 · 游玩统计',exact:true}).click();
 await page.locator('.yijian-management tbody tr').first().waitFor();
 assert.equal(await page.locator('.yijian-management tbody tr').count(),2);
 assert.deepEqual(await page.getByLabel('统计范围').locator('option').evaluateAll(els=>els.map(e=>e.value)),['1h','24h','7d','14d','30d','all']);
 for(const range of ['1h','24h','7d','14d','30d','all']){await page.getByLabel('统计范围').selectOption(range);await page.locator('.yijian-management tbody tr').first().waitFor();}
 for(const width of [360,390,430,1440]){await page.setViewportSize({width,height:900});await page.screenshot({path:`${output}/game-stats-${width}.png`});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
 assert.equal(await page.locator('.yijian-management a[href="/admin"]').count(),0);
 await page.route('**/autochess/api/stats',route=>route.fulfill({status:503,json:{error:'临时故障，请重试'}}),{times:1});
 await page.getByLabel('统计范围').selectOption('1h');await page.getByRole('button',{name:'重试',exact:true}).click();await page.locator('.yijian-management tbody tr').first().waitFor();
 let releaseStats;
 await page.route('**/autochess/api/stats',async route=>{const response=await route.fetch();await new Promise(resolve=>{releaseStats=()=>route.fulfill({response}).then(resolve).catch(resolve);});},{times:1});
 await page.getByLabel('统计范围').selectOption('24h');
 while(!releaseStats)await page.waitForTimeout(20);
 await page.evaluate(token=>{localStorage.setItem('cedartoy_token',token);window.dispatchEvent(new Event('storage'));},process.env.AUTOCHESS_BROWSER_FRIEND_TOKEN);
 await create().waitFor();await releaseStats();
 assert.equal(await page.locator('.yijian-management').count(),0);
 await nav('我的').click();assert.equal(await page.getByRole('button',{name:'弈间 · 游玩统计',exact:true}).count(),0);
 const forbiddenStats=await page.evaluate(async()=>{const r=await fetch('/autochess/api/stats',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify({view:'breakdown',range:'all'})});return r.status;});assert.equal(forbiddenStats,403);
 await page.evaluate(token=>{localStorage.setItem('cedartoy_token',token);window.dispatchEvent(new Event('storage'));},process.env.AUTOCHESS_BROWSER_TOKEN);await create().waitFor();

 await nav('我的').click();await page.getByRole('button',{name:'弈间 · 游玩统计',exact:true}).click();await page.locator('.yijian-management tbody tr').first().waitFor();
 let releaseClosed;
 await page.route('**/autochess/api/stats',async route=>{const response=await route.fetch();await new Promise(resolve=>{releaseClosed=()=>route.fulfill({response}).then(resolve).catch(resolve);});},{times:1});
 await page.getByLabel('统计范围').selectOption('all');while(!releaseClosed)await page.waitForTimeout(20);
 await page.getByRole('button',{name:'返回棋室',exact:true}).click();await releaseClosed();assert.equal(await page.locator('.yijian-management').count(),0);
 await page.unrouteAll({behavior:'wait'}); // Finish held responses before injecting a new permission failure.
 await page.route('**/autochess/api/stats',route=>route.fulfill({status:403,json:{error:'未获本游戏统计查看授权'}}),{times:1});
 await page.getByRole('button',{name:'弈间 · 游玩统计',exact:true}).click();await page.getByRole('alert').filter({hasText:'未获本游戏统计查看授权'}).waitFor();
 assert.equal(await page.locator('.yijian-management').count(),0);assert.equal(await page.getByRole('button',{name:'弈间 · 游玩统计',exact:true}).count(),0);
 await page.reload();await create().waitFor();
 // Admin.html is a static style reference only: no credentials or admin API request.
 const reference=await browser.newPage();
 await reference.setContent(fs.readFileSync('admin.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
 await reference.evaluate(()=>{
  const panel=document.querySelector('#duelDashboard');panel.querySelectorAll('.dashboard-content').forEach(el=>{el.innerHTML='<div class="game-overview"><div class="game-overview-row game-overview-header"><span>棋谱</span><span>多人对弈</span><span>弈决</span><span>合计</span></div><div class="game-overview-row"><span>参考样式</span><span>0</span><span>0</span><span>0</span></div></div>';});
  const shell=document.createElement('div');shell.className='shell';shell.append(panel);document.body.replaceChildren(shell);
 });
 for(const width of [360,390,430,1440]){await reference.setViewportSize({width,height:900});await reference.bringToFront();await reference.waitForTimeout(150);await reference.screenshot({path:`${output}/admin-style-reference-${width}.png`});}
 await reference.close();
 console.log('PASS game-only stats / six ranges / failure retry / account race');
 assert.equal(await page.locator('.lobby-tabs button').count(),4);
 assert.equal(await page.locator('input[type=checkbox]').count(),0);
 assert.equal(await page.getByLabel('存档槽',{exact:true}).count(),0);
 await screenshots('entry',true);console.log('PASS original lobby / anonymous / responsive');
 const metrics=await page.evaluate(()=>Object.fromEntries(['.lobby','.lobby-top','.lobby-brand h1','.lobby-sub','.lobby-tabs button','.chamber-start h2','.pack-tome'].map(selector=>{const el=document.querySelector(selector),s=getComputedStyle(el);return [selector,{font:s.font,fontSize:s.fontSize,lineHeight:s.lineHeight,padding:s.padding,background:s.background,border:s.border,borderRadius:s.borderRadius}];})));
 fs.writeFileSync(`${output}/lobby-metrics.json`,JSON.stringify(metrics,null,2));
 await page.locator('.cedar-table-settings summary').click();
 assert.equal(await page.locator('.cedar-table-settings button').count(),5);
 await screenshots('five-machines',true);
 await page.locator('.cedar-table-settings button').first().click();
 assert.equal(await page.locator('.cedar-table-settings button').first().getAttribute('aria-pressed'),'true');
 await page.locator('.cedar-table-settings button').first().click();
 await page.locator('.cedar-table-settings summary').click();
 // The same original cosmetic cards and wheel live under the original header/navigation.
 await nav('外观').click();await page.locator('.lobby-card').first().waitFor();
 await screenshots('economy-shop');
 const rose=page.locator('.lobby-card').filter({hasText:'玫瑰古堡'});
 await rose.getByRole('button',{name:'奉纳购置',exact:true}).click();
 await rose.getByRole('button',{name:'✦ 已佩戴入局',exact:true}).waitFor();
 await nav('我的').click();await page.locator('#wardrobe h2').waitFor();
 assert.equal(await page.getByText(/胜 \/.*局/).count(),0);
 await screenshots('my-wallet');
 await nav('对对碰').click();await page.getByRole('button',{name:'领试玩券，开一盘',exact:true}).click();
 await page.getByRole('button',{name:'补一格',exact:true}).waitFor();
 await page.getByRole('button',{name:'买 10 张 · 7 铜钱',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.lobby-coin-pill.human .coin-val')?.textContent==='25');
 await screenshots('economy-wheel');
 for(const width of [360,430,1440]){await page.setViewportSize({width,height:900});await page.locator('.wheel-play-area').scrollIntoViewIfNeeded();await page.screenshot({path:`${output}/wheel-board-${width}.png`});}
 await page.getByRole('button',{name:'补一格',exact:true}).click();
 await page.getByRole('button',{name:'补一格',exact:true}).waitFor();
 await page.getByRole('link',{name:'我的装扮',exact:true}).click();
 // Independent slot/AI wallets and explicit read-only navigation.
 await page.getByLabel('存档槽',{exact:true}).selectOption('2');
 await page.locator('.lobby-profile').filter({hasText:'0 铜钱 · 0 机芯'}).waitFor();
 await page.getByLabel('存档槽',{exact:true}).selectOption('1');
 await page.getByLabel('棋局归属',{exact:true}).selectOption('202');
 await page.locator('.lobby-profile').filter({hasText:'临时小机'}).filter({hasText:'0 铜钱 · 0 机芯'}).waitFor();
 assert.equal(await nav('对对碰').count(),0);
 assert.equal(await page.getByRole('button',{name:'确认赠送'}).count(),0);
 await screenshots('ai-readonly');
 await nav('外观').click();await page.locator('.lobby-card').first().waitFor();
 assert.equal(await page.locator('.lobby-card button:not([disabled])').count(),0);
 const denied=await page.evaluate(async()=>{const r=await fetch('/autochess/api/economy',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify({ai_id:'202',slot:1,op:'buy',cosmeticId:'rose-court',requestId:'browser-denied'})});return r.status;});assert.equal(denied,403);
 await nav('开始').click();assert.equal(await create().count(),0);assert.equal(await page.getByRole('heading',{name:'自家机的棋局'}).count(),1);
 // Hold a real profile response, then deliver it only after the account changed.
 await page.evaluate(()=>{
  const original=window.fetch;window.walletRelease=null;let held=false;
  window.fetch=async(url,options={})=>{
   if(!held&&url==='/autochess/api/economy'&&JSON.parse(options.body).op==='profile'){
    held=true;const response=await original(url,{...options,signal:undefined});
    return new Promise(resolve=>{window.walletRelease=()=>resolve(response);});
   }
   return original(url,options);
  };
 });
 await nav('我的').click();await page.waitForFunction(()=>!!window.walletRelease);
 await page.evaluate(token=>{localStorage.setItem('cedartoy_token',token);window.dispatchEvent(new Event('storage'));},process.env.AUTOCHESS_BROWSER_FRIEND_TOKEN);
 await create().waitFor();await page.evaluate(()=>window.walletRelease());assert.equal(await page.locator('.lobby-profile').count(),0);
 await nav('我的').click();await page.locator('.lobby-profile').filter({hasText:'临时朋友'}).filter({hasText:'0 铜钱 · 0 机芯'}).waitFor();
 await page.evaluate(token=>{localStorage.setItem('cedartoy_token',token);window.dispatchEvent(new Event('storage'));},process.env.AUTOCHESS_BROWSER_TOKEN);
 await create().waitFor();await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:/弈决.*1v1/}).click();await create().click();
 await page.getByRole('button',{name:'补路人机，直接开局',exact:true}).waitFor({timeout:15000});
 await page.locator('.peach-arena-shell[data-skin="rose-court"]').waitFor();
 await page.screenshot({path:`${output}/waiting-desktop.png`,fullPage:true});
 assert.equal(await page.locator('.room-offer-overlay,.room-draft').count(),0,'waiting has no actionable offers');
 assert(await page.locator('.peach-shop-toggle').isDisabled());
 assert(await page.locator('.peach-trash').evaluate(el=>el.classList.contains('disabled')));
 for(const width of [360,390,430,1440]){
  await page.setViewportSize({width,height:width===1440?1000:844});await page.waitForTimeout(300);
  await page.screenshot({path:`${output}/waiting-${width}.png`});
 }
 await landscapeChecks('waiting');
 // Invite a friend via the private server-issued invitation, using the visible lobby join form.
 const roomInfo=await page.evaluate(async()=>{
  const post=async body=>{const r=await fetch('/autochess/api/room',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify(body)});return r.json();};
  const cfg=await post({op:'config'});return post({op:'invite',roomId:cfg.rooms[0].roomId});
 });
 assert(roomInfo.invitation,'private room invitation');
 const friendContext=await browser.newContext();await friendContext.addInitScript(() => {Object.defineProperty(window.crypto, "randomUUID", {value:undefined, configurable:true});});await friendContext.addInitScript(token=>localStorage.setItem('cedartoy_token',token),process.env.AUTOCHESS_BROWSER_FRIEND_TOKEN);
 const friend=await friendContext.newPage();await friend.goto(origin+'/autochess/');
 await friend.getByLabel('好友邀请').fill(roomInfo.invitation);
 await friend.getByRole('button',{name:'入座登盘',exact:true}).click();await friend.locator('canvas').first().waitFor({timeout:15000});
 assert.equal(await friend.locator('.room-offer-overlay').count(),0);
 assert.equal(await friend.getByRole('button',{name:'补路人机，直接开局',exact:true}).count(),0);
 await friend.getByText('等待房主开局',{exact:true}).waitFor();
 await page.getByRole('button',{name:'开始棋局',exact:true}).waitFor();
 await friend.setViewportSize({width:390,height:844});await friend.screenshot({path:`${output}/invited-guest-390.png`});
 // Re-enter after a real reload while still waiting; the invitation is not needed again.
 await friend.reload();await friend.getByRole('button',{name:'回到棋盘',exact:true}).click();
 await friend.getByText('等待房主开局',{exact:true}).waitFor();
 await friend.getByLabel('房间菜单',{exact:true}).click();await friend.getByRole('button',{name:'返回棋室（保留席位）',exact:true}).click();
 await friend.getByRole('button',{name:'回到棋盘',exact:true}).waitFor();await friendContext.close();
 console.log('PASS shop / wheel / isolation / read-only / invite');
 // Eight-seat private game with five bound AIs: inspect the actual room, then its read-only AI view.
 await page.getByLabel('房间菜单',{exact:true}).click();await page.getByRole('button',{name:'返回棋室（保留席位）',exact:true}).click();
 await screenshots('existing-rooms');
 await page.getByRole('button',{name:/多人对弈.*轮流/}).click();
 await page.getByRole('button',{name:/夜航船.*40 枚/}).click();
 await page.locator('.cedar-table-settings summary').click();
 for(const button of await page.locator('.cedar-table-settings button').all())await button.click();
 await create().click();await page.getByRole('button',{name:'补路人机，直接开局',exact:true}).waitFor({timeout:15000});
 const household=await page.evaluate(async()=>{const r=await fetch('/autochess/api/room',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify({op:'config'})});return (await r.json()).rooms.find(r=>r.capacity===8);});
 assert.equal(household.seats,6);assert.equal(household.packName,'夜航船');
 await page.getByLabel('房间菜单',{exact:true}).click();await page.getByRole('button',{name:'返回棋室（保留席位）',exact:true}).click();
 await nav('我的').click();await page.getByLabel('棋局归属',{exact:true}).selectOption('202');
 await nav('开始').click();await page.getByRole('button',{name:'观看',exact:true}).click();await page.locator('canvas').first().waitFor();
 assert.equal(await page.getByRole('button',{name:'补路人机，直接开局',exact:true}).count(),0);
 await page.getByRole('button',{name:'返回棋室',exact:true}).click();
 await nav('我的').click();await page.getByLabel('棋局归属',{exact:true}).selectOption('');
 await nav('开始').click();await page.getByRole('button',{name:/弈决.*1v1/}).click();await create().click();
 await page.getByRole('button',{name:'补路人机，直接开局',exact:true}).waitFor({timeout:15000});
 console.log('PASS five AIs / eight-seat / readonly spectator');
 await page.getByRole('button',{name:'补路人机，直接开局',exact:true}).click();
 await page.waitForTimeout(3000);
 for(const width of [360,390,430,1440]){
  await page.setViewportSize({width,height:width===1440?1000:844});await page.waitForTimeout(500);
  await page.screenshot({path:`${output}/board-${width}.png`,fullPage:true});
  const metrics=await page.evaluate(()=>{
   const box=s=>{const el=document.querySelector(s),r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right};};
   return {kind:'offer',fonts:{name:getComputedStyle(document.querySelector('.versus-name')).fontSize,cardTitle:getComputedStyle(document.querySelector('.codex-choice-card strong')).fontSize,description:getComputedStyle(document.querySelector('.codex-choice-card p')).fontSize},width:innerWidth,overflow:document.documentElement.scrollWidth-innerWidth,offer:box('.room-offer-overlay'),dock:box('.peach-dock'),hud:box('.room-versus-hud'),menu:box('.room-float'),camera:box('.peach-camera-overlay'),card:box('.codex-choice-card'),name:box('.versus-name'),columns:getComputedStyle(document.querySelector('.codex-offer-grid')).gridTemplateColumns.split(' ').length};
  });roomMetrics.push(metrics);
  assert(metrics.overflow<=1,'board overflow '+width);
  assert(metrics.offer.bottom<=metrics.dock.y-4,'offer clears bottom actions '+width);
  if(width<768){
   assert.equal(metrics.columns,1);assert(metrics.card.width>280);
   assert(metrics.hud.right<metrics.menu.x,'HUD clears menu');
   assert(metrics.camera.y>=metrics.hud.bottom,'camera below HUD');
   assert(metrics.name.height<22,'nickname stays single-line');
  }else assert.equal(metrics.columns,3);
 }
 await page.setViewportSize({width:360,height:640});
 await page.locator('.codex-choice-card').last().scrollIntoViewIfNeeded();
 await page.screenshot({path:`${output}/offer-short-360.png`});
 assert(await page.locator('.codex-choice-card').last().isVisible());
 await page.setViewportSize({width:390,height:844});
 // Choose original opening equipment with the visible UI. Seed a legal position via the
 // authenticated game API; pointer-based canvas dragging still needs a real-device check.
 await page.locator('.codex-choice-card').first().click();
 await page.locator('.peach-inventory > summary').click();
 await page.locator('.peach-inventory[open] button').first().waitFor();
 for(const width of [360,390,430,1440]){
  await page.setViewportSize({width,height:width===1440?1000:844});await page.waitForTimeout(300);
  if(!await page.locator('.peach-inventory').evaluate(el=>el.open))await page.locator('.peach-inventory > summary').click();
  const inventory=await page.locator('.peach-inventory').boundingBox();
  assert(inventory.width>180);
  const dock=await page.locator('.peach-dock').boundingBox();assert(inventory.y+inventory.height<dock.y,'inventory clears dock');
  roomMetrics.push({kind:'inventory',width,rect:inventory,buttonFont:await page.locator('.peach-inventory button').first().evaluate(el=>getComputedStyle(el).fontSize)});
  await page.screenshot({path:`${output}/inventory-${width}.png`});
 }

 for(const [width,height] of landscapeSizes){
  await page.setViewportSize({width,height});await page.waitForTimeout(350);
  if(!await page.locator('.peach-inventory').evaluate(el=>el.open))await page.locator('.peach-inventory > summary').click();
  const item=page.locator('.peach-inventory[open] button').first();await item.waitFor({state:'visible'});await item.scrollIntoViewIfNeeded();
  const m=await item.evaluate(el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {width:r.width,height:r.height,font:getComputedStyle(el).fontSize,hit:el===hit||el.contains(hit)};});
  assert(m.width>=140&&m.height>=44&&m.hit,'readable landscape equipment '+JSON.stringify(m));roomMetrics.push({kind:'landscape-equipment',width,height,...m});
  await page.screenshot({path:`${output}/landscape-equipment-${width}.png`});
 }

 await page.evaluate(async()=>{
  const post=async(body)=>{const r=await fetch('/autochess/api/room',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error);return v;};
  const cfg=await post({op:'config'});let v=await post({op:'state',roomId:cfg.rooms.find(r=>r.phase!=='waiting'&&r.phase!=='finished').roomId});
  if(!v.self.board.length)v=await post({op:'action',roomId:v.id,round:v.round,revision:v.self.revision,requestId:Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,"0")).join(""),action:{kind:'move',piece:v.self.bench[0].id,row:0,col:0}});
 });
 // Select the real rendered piece via canvas pointer coordinates, then equip from
 // its backpack. No synthetic React state or test endpoint is used.
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(1800);
 const unit=await page.locator('.peach-unit-labels .label:not(.red)').first().boundingBox();assert(unit);
 for(const offset of [16,28,40,52,64]){
  await page.mouse.click(unit.x+unit.width/2,unit.y+offset);await page.waitForTimeout(150);
  if(await page.getByRole('button',{name:'选中棋子',exact:true}).count())break;
 }
 await page.getByRole('button',{name:'选中棋子',exact:true}).click();
 await page.getByRole('button',{name:'关闭棋子详情',exact:true}).click();
 if(!await page.locator('.peach-inventory').evaluate(el=>el.open))await page.locator('.peach-inventory > summary').click();
 await page.locator('.peach-inventory button').first().click();
 await page.locator('.peach-inventory summary').filter({hasText:'装备背包 · 0'}).waitFor();
 await page.screenshot({path:`${output}/equipped-390.png`});
 await landscapeChecks('prepare');
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'就绪开战',exact:true}).click();
 await page.getByRole('button',{name:'跳过战斗',exact:true}).waitFor({timeout:15000});
 await page.screenshot({path:`${output}/replay-390.png`,fullPage:true});
 // The replay may finish while software rendering captures the screenshot.
 await page.locator('.peach-shop').waitFor({timeout:30000});
 for(const width of [360,390,430,1440]){
  await page.setViewportSize({width,height:width===1440?1000:844});await page.waitForTimeout(300);
  await page.screenshot({path:`${output}/shop-${width}.png`});
  if(width<768){
   const shop=await page.locator('.peach-popover:has(.peach-shop)').boundingBox(),dock=await page.locator('.peach-dock').boundingBox();
   assert(shop.y+shop.height<dock.y,'shop clears bottom actions');
   for(const b of await page.locator('.peach-shop header button').all()){
    const r=await b.boundingBox();assert(r.width>=80&&r.height>=44,'readable shop actions');
   }
  }
 }

 for(const [width,height] of landscapeSizes){
  await page.setViewportSize({width,height});await page.waitForTimeout(300);
  if(!await page.locator('.peach-shop').count())await page.locator('.peach-shop-toggle').click();
  const layout=await page.evaluate(()=>{
   const rect=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};};
   const hits=[...document.querySelectorAll('.peach-shop header button,.peach-camera-overlay button,.peach-dock button')].map(el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {text:el.textContent,ok:el===hit||el.contains(hit)};});
   return {shop:rect('.peach-popover:has(.peach-shop)'),dock:rect('.peach-dock'),hits};
  });
  assert(layout.shop.right<layout.dock.x,'shop clears landscape action rail');assert(layout.hits.every(h=>h.ok),JSON.stringify(layout.hits));roomMetrics.push({kind:'landscape-shop',width,height,...layout});
  await page.screenshot({path:`${output}/landscape-shop-${width}.png`});
 }
 // Buy a real market piece and sell it through the original canvas inspector in landscape.
 await page.setViewportSize({width:740,height:345});
 const stateSnapshot=()=>page.evaluate(async()=>{
  const post=async body=>{const r=await fetch('/autochess/api/room',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify(body)});if(!r.ok)throw Error(await r.text());return r.json();};
  const cfg=await post({op:'config'});return post({op:'state',roomId:cfg.rooms.find(r=>r.phase==='prepare').roomId});
 });
 const beforeBuy=await stateSnapshot();await page.locator('.peach-shop-cards button:not([disabled])').first().click();
 await page.waitForTimeout(1300);const afterBuy=await stateSnapshot();assert(afterBuy.self.bench.length>beforeBuy.self.bench.length);
 await page.getByRole('button',{name:'收起模型市场',exact:true}).click();
 if(await page.getByRole('button',{name:'暂回棋盘',exact:true}).count())await page.getByRole('button',{name:'暂回棋盘',exact:true}).click();
 await page.locator('.peach-unit-labels .label:not(.red)').first().waitFor();
 const benchLabel=await page.locator('.peach-unit-labels .label:not(.red)').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width};}).sort((a,b)=>b.y-a.y)[0]);assert(benchLabel);
 for(const offset of [16,28,40,52]){await page.mouse.click(benchLabel.x+benchLabel.width/2,benchLabel.y+offset);await page.waitForTimeout(150);if(await page.getByRole('button',{name:'出售',exact:true}).count())break;}
 await page.getByRole('button',{name:'出售',exact:true}).click();await page.waitForTimeout(1300);
 const afterSale=await stateSnapshot();assert.equal(afterSale.self.bench.length,afterBuy.self.bench.length-1);assert(afterSale.self.gold>afterBuy.self.gold);
 console.log('PASS landscape market purchase / canvas inspector sale');
 await page.setViewportSize({width:1440,height:1000});
 await page.reload();await page.locator('.cedar-saved').filter({hasText:/第 \d+ 轮/}).getByRole('button',{name:'回到棋盘',exact:true}).click();
 await page.waitForTimeout(2000);assert(await page.locator('canvas').count()>0,'original WebGL canvas');
 await context.setOffline(true);await page.waitForTimeout(2500);assert(await page.getByText(/网络中断/).count()>0);
 await context.setOffline(false);await page.waitForTimeout(3000);
 await page.getByLabel('房间菜单',{exact:true}).click();
 await page.getByRole('button',{name:'返回棋室（保留席位）',exact:true}).click();
 await page.getByRole('button',{name:'观看',exact:true}).first().click();await page.waitForTimeout(2000);
 assert.equal(await page.getByRole('button',{name:'就绪开战',exact:true}).count(),0);
 await page.screenshot({path:`${output}/own-spectator-desktop.png`,fullPage:true});
 await page.setViewportSize({width:740,height:345});
 if(await page.getByRole('button',{name:'跳过战斗',exact:true}).count())await page.getByRole('button',{name:'跳过战斗',exact:true}).click();
 assert.equal(await page.locator('.room-float').count(),0);assert(await page.locator('.peach-shop-toggle').isDisabled());
 await page.locator('.peach-fullscreen').click();await page.waitForFunction(()=>!!document.fullscreenElement);await page.locator('.peach-fullscreen').click();await page.waitForFunction(()=>!document.fullscreenElement);
 const watchHit=await page.getByRole('button',{name:'返回棋室',exact:true}).evaluate(el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return el===hit||el.contains(hit);});assert(watchHit);
 await page.screenshot({path:`${output}/own-spectator-740.png`});
 await page.setViewportSize({width:1440,height:1000});

 // Actual full household creation goes straight to preparation, without NPCs.
 await page.getByRole('button',{name:'返回棋室',exact:true}).click();
 await page.getByRole('button',{name:/弈决.*1v1/}).click();await page.locator('.cedar-table-settings summary').click();
 await page.locator('.cedar-table-settings button').first().click();await create().click();
 await page.getByText('第 1 轮 · 备战排布',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'补路人机，直接开局',exact:true}).count(),0);
 await page.getByLabel('房间菜单',{exact:true}).click();page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'认输结束',exact:true}).click();
 await page.getByRole('button',{name:'看看棋盘',exact:true}).click();
 await page.getByText('棋局已结束',{exact:true}).waitFor();assert.equal(await page.getByText('棋局待启',{exact:true}).count(),0);
 await page.getByRole('button',{name:'查看本局结算',exact:true}).waitFor();
 for(const [width,height] of [[740,345],[1536,690]]){
  await page.setViewportSize({width,height});const r=await page.locator('.room-result-reopen').boundingBox();assert(r.height>=44&&r.height<=60&&r.y<100);
  await page.getByRole('button',{name:'查看本局结算',exact:true}).click();await page.getByRole('button',{name:'看看棋盘',exact:true}).click();
  await page.screenshot({path:`${output}/finished-${width}.png`});
 }
 if(!await page.locator('.room-float').evaluate(el=>el.open))await page.getByLabel('房间菜单',{exact:true}).click();
 await page.getByRole('button',{name:'返回棋室（保留席位）',exact:true}).click();
 await nav('我的').click();await page.getByRole('button',{name:'弈间 · 游玩统计',exact:true}).click();await page.locator('.yijian-management tbody tr').first().waitFor();
 assert(Number(await page.locator('.yijian-management tfoot td').last().textContent())>=2);
 for(const width of [360,390,430,1440]){await page.setViewportSize({width,height:900});await page.screenshot({path:`${output}/game-stats-started-${width}.png`});}

 const forbidden=requests.filter(r=>!r.url.startsWith(origin+'/autochess/')&&!r.url.startsWith('data:')&&!r.url.startsWith('blob:'));
 assert.deepEqual(forbidden,[],'no original/external endpoints');assert.deepEqual(errors,[],'browser errors');
 fs.writeFileSync(`${output}/browser-result.json`,JSON.stringify({ok:true,completedAt:new Date().toISOString(),widths:[360,390,430,1440],landscapeViewports:landscapeSizes,roomMetrics,homepage:!process.env.AUTOCHESS_BROWSER_SKIP_HOME,scenarios:['game-only-management','six-range-grouped','stats-late-close','stats-revoked','full-duel-autostart','landscape-fullscreen','landscape-safearea','ended-stage-label','anonymous','original-lobby','five-bound-AIs-eight-seats','invited-friend','waiting-reconnect','waiting-offers-hidden','long-nicknames','single-column-offers','backpack-equip','shop-mobile','solo-NPC','skin-in-3D','slot-switch','account-switch-late-response','AI-wallet-readonly-403','AI-spectator','wheel','reconnect','battle-replay'],requestCount:requests.length,errors},null,2));
 }catch(error){
  for(const context of browser.contexts())for(const page of context.pages())if(page.url().includes('/autochess/')){await page.screenshot({path:`${output}/failure.png`}).catch(()=>{});console.error('Visible UI at failure:',await page.locator('body').innerText().catch(()=>''));}
  throw error;
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
