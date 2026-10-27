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
 const errors=[],requests=[];
 try{
 if(!process.env.AUTOCHESS_BROWSER_SKIP_HOME){
 const homeContext=await browser.newContext();
 const home=await homeContext.newPage();
 await homeContext.addInitScript(token=>{if(location.hostname==='127.0.0.1')localStorage.setItem('cedartoy_token',token);},process.env.AUTOCHESS_BROWSER_TOKEN);
 home.on('pageerror',e=>errors.push(e.message));
 await home.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
   if(path.startsWith('/assets/') || path==='/autochess/api/stats')return route.continue();
   if(path==='/api/auth/me')return route.fulfill({json:{user:{id:101,username:'临时棋手',is_ai:false,avatar:{type:'emoji',value:'🙂'}},bindings:[]}});
   return route.fulfill({json:{}});
 });
 await home.goto(origin+'/');
 await home.locator('.game-card[data-game="autochess"]').hover();
 const entry=()=>home.locator('[data-autochess-stats]:visible');
 const counts=()=>home.locator('#autochessStatsContent strong');
 const waitCounts=()=>counts().first().waitFor();
 await entry().waitFor(); await entry().click(); await waitCounts();
 assert.deepEqual(await counts().allTextContents(),['0','0','0','0','0'],'empty real aggregate stays zero');
 for(const range of ['10m','1h','6h','12h','24h']){
   await home.locator(`[data-autochess-range="${range}"]`).click(); await waitCounts();
   assert.equal(await home.locator(`[data-autochess-range="${range}"]`).getAttribute('aria-pressed'),'true');
 }
 for(const width of [360,430,1440]){
   await home.setViewportSize({width,height:900});
   await home.screenshot({path:`${output}/home-stats-${width}.png`,fullPage:true});
   assert(await home.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'stats overflow '+width);
 }
 // Same real loopback API; intercept only failures/delivery timing, deliberately ignore abort
 // in held responses to exercise stale-result guards independently of network cancellation.
 await home.evaluate(()=>{
   const original=window.fetch;
   window.statsFixture={fail:false,hold:'',release:null,token:localStorage.getItem('cedartoy_token')};
   window.fetch=async(url,options={})=>{
     if(url!=='/autochess/api/stats')return original(url,options);
     const f=window.statsFixture,kind=JSON.parse(options.body).op==='access'?'access':'read';
     if(f.fail && kind==='read'){const status=f.fail;f.fail=false;return new Response('{}',{status});}
     if(f.hold===kind){
       f.hold='';let response=await original(url,{...options,signal:undefined});
       if(kind==='read')response=new Response(JSON.stringify({...await response.json(),active_humans:999}));
       return new Promise(resolve=>{f.release=()=>{f.release=null;resolve(response);};});
     }
     return original(url,options);
   };
 });
 await home.evaluate(()=>{statsFixture.fail=503;});
 await home.locator('[data-autochess-range="1h"]').click();
 await home.locator('#autochessStatsRetry').waitFor(); assert.equal(await counts().count(),0);
 await home.locator('#autochessStatsRetry').click(); await waitCounts();
 await home.evaluate(()=>{statsFixture.hold='read';});
 await home.locator('[data-autochess-range="10m"]').click();
 await home.waitForFunction(()=>!!statsFixture.release);
 await home.locator('[data-autochess-range="6h"]').click();await waitCounts();
 await home.evaluate(()=>statsFixture.release());
 await home.waitForTimeout(50);
 assert.deepEqual(await counts().allTextContents(),['0','0','0','0','0'],'older range must not overwrite newer data');
 assert.equal(await home.locator('[data-autochess-range="6h"]').getAttribute('aria-pressed'),'true');
 for(const transition of ['close','game','account']){
   await home.evaluate(()=>{statsFixture.hold='read';});
   await home.locator('[data-autochess-range="12h"]').click();
   await home.waitForFunction(()=>!!statsFixture.release);
   if(transition==='close')await home.locator('#autochessStatsClose').click();
   if(transition==='game')await home.evaluate(()=>selectGame('duel',false));
   if(transition==='account')await home.evaluate(()=>{localStorage.setItem('cedartoy_token','unauthorized-fixture');window.dispatchEvent(new StorageEvent('storage',{key:TOKEN_KEY}));});
   await home.evaluate(()=>statsFixture.release());
   await home.waitForTimeout(50);
   assert.equal(await counts().count(),0,transition+' clears counts');
   assert.equal(await home.locator('#autochessStatsModal').isVisible(),false);
   if(transition!=='close')assert.equal(await entry().count(),0,transition+' hides entry');
   await home.evaluate(()=>{localStorage.setItem('cedartoy_token',statsFixture.token);selectGame('autochess',false);});
   await entry().waitFor(); await entry().click(); await waitCounts();
 }
 await home.evaluate(()=>{statsFixture.fail=403;});
 await home.locator('[data-autochess-range="24h"]').click();
 await home.waitForFunction(()=>!document.querySelector('#autochessStatsModal').classList.contains('show'));
 assert.equal(await counts().count(),0); assert.equal(await entry().count(),0,'revoked grant hides entry');
 await home.evaluate(()=>refreshAutochessStatsAccess());
 await entry().waitFor();await entry().click();await waitCounts();
 // Close/Escape/backdrop, access race across game selection, and mobile drawer entry.
 await home.keyboard.press('Escape');
 await home.evaluate(()=>{statsFixture.hold='access';refreshAutochessStatsAccess();});
 await home.waitForFunction(()=>!!statsFixture.release);
 await home.evaluate(()=>{selectGame('duel',false);statsFixture.release();});
 await home.waitForTimeout(50); assert.equal(await entry().count(),0);
 await home.setViewportSize({width:360,height:900});
 await home.locator('.game-card[data-game="autochess"]').click();
 await entry().waitFor(); await entry().click(); await waitCounts();
 await home.locator('#autochessStatsModal').click({position:{x:2,y:2}});
 assert.equal(await counts().count(),0);
 await home.setViewportSize({width:1440,height:900});await home.evaluate(()=>closeDrawers());
 assert.equal(await home.locator('#enterButton').textContent(),'作者原版 ↗');
 assert.equal(await home.locator('#detailDesc a').getAttribute('href'),'https://github.com/Cecilia-coding/AutoChess-MCP');
 assert.match(await home.locator('#detailDesc').textContent(),/MIT/);
 assert.equal(await home.locator('#watchButton').textContent(),'进入棋室 →');
 // Abort navigation to the author site: verify URL and absence of credentials without contacting it.
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
 await context.addInitScript(token=>localStorage.setItem('cedartoy_token',token),process.env.AUTOCHESS_BROWSER_TOKEN);
 const page=await context.newPage();
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
 const anon=await browser.newPage();
 await anon.goto(origin+'/autochess/');await anon.locator('.pack-tome').first().waitFor();
 assert.equal(await anon.locator('.cedar-top').count(),0);
 assert.equal(await anon.locator('.lobby-top').count(),1);
 assert.equal(await anon.title(),'弈间');
 assert.equal(await anon.getByRole('link',{name:'前往登录'}).count(),1);
 assert(await anon.getByRole('button',{name:/✦ 辟立/}).isDisabled());
 await anon.setViewportSize({width:360,height:900});await anon.screenshot({path:`${output}/logged-out-360.png`,fullPage:true});await anon.close();
 await page.goto(origin+'/autochess/');await page.waitForFunction(()=>!document.querySelector('.lobby-primary')?.disabled);
 assert.equal(await page.title(),'弈间');
 assert.equal(await page.locator('.cedar-top').count(),0);
 assert.equal(await page.locator('.lobby-top').count(),1);assert.equal(await page.locator('.lobby-brand h1').textContent(),'弈间');
 assert.equal(await page.locator('.lobby-top').getByText('临时棋手').count(),0);
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
 // Invite a friend via the private server-issued invitation, using the visible lobby join form.
 const roomInfo=await page.evaluate(async()=>{
  const post=async body=>{const r=await fetch('/autochess/api/room',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify(body)});return r.json();};
  const cfg=await post({op:'config'});return post({op:'invite',roomId:cfg.rooms[0].roomId});
 });
 assert(roomInfo.invitation,'private room invitation');
 const friendContext=await browser.newContext();await friendContext.addInitScript(token=>localStorage.setItem('cedartoy_token',token),process.env.AUTOCHESS_BROWSER_FRIEND_TOKEN);
 const friend=await friendContext.newPage();await friend.goto(origin+'/autochess/');
 await friend.getByLabel('好友邀请').fill(roomInfo.invitation);
 await friend.getByRole('button',{name:'入座登盘',exact:true}).click();await friend.locator('canvas').first().waitFor({timeout:15000});
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
 for(const width of [360,430,1440]){
  await page.setViewportSize({width,height:1000});await page.waitForTimeout(600);
  await page.screenshot({path:`${output}/board-${width}.png`,fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'board overflow '+width);
 }
 // Choose original opening equipment with the visible UI. Seed a legal position via the
 // authenticated game API; pointer-based canvas dragging still needs a real-device check.
 await page.locator('.codex-choice-card').first().click();
 await page.evaluate(async()=>{
  const post=async(body)=>{const r=await fetch('/autochess/api/room',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('cedartoy_token')},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error);return v;};
  const cfg=await post({op:'config'});let v=await post({op:'state',roomId:cfg.rooms.find(r=>r.phase!=='waiting'&&r.phase!=='finished').roomId});
  if(!v.self.board.length)v=await post({op:'action',roomId:v.id,round:v.round,revision:v.self.revision,requestId:crypto.randomUUID(),action:{kind:'move',piece:v.self.bench[0].id,row:0,col:0}});
 });
 await page.getByRole('button',{name:'就绪开战',exact:true}).click();
 await page.getByRole('button',{name:'跳过战斗',exact:true}).waitFor({timeout:15000});
 await page.screenshot({path:`${output}/replay-desktop.png`,fullPage:true});
 // The replay may finish while software rendering captures the screenshot.
 await page.locator('.peach-shop').waitFor({timeout:30000});
 await page.screenshot({path:`${output}/shop-desktop.png`,fullPage:true});
 await page.reload();await page.locator('.cedar-saved').filter({hasText:/第 \d+ 轮/}).getByRole('button',{name:'回到棋盘',exact:true}).click();
 await page.waitForTimeout(2000);assert(await page.locator('canvas').count()>0,'original WebGL canvas');
 await context.setOffline(true);await page.waitForTimeout(2500);assert(await page.getByText(/网络中断/).count()>0);
 await context.setOffline(false);await page.waitForTimeout(3000);
 await page.getByLabel('房间菜单',{exact:true}).click();
 await page.getByRole('button',{name:'返回棋室（保留席位）',exact:true}).click();
 await page.getByRole('button',{name:'观看',exact:true}).first().click();await page.waitForTimeout(2000);
 assert.equal(await page.getByRole('button',{name:'就绪开战',exact:true}).count(),0);
 await page.screenshot({path:`${output}/own-spectator-desktop.png`,fullPage:true});
 const forbidden=requests.filter(r=>!r.url.startsWith(origin+'/autochess/')&&!r.url.startsWith('data:')&&!r.url.startsWith('blob:'));
 assert.deepEqual(forbidden,[],'no original/external endpoints');assert.deepEqual(errors,[],'browser errors');
 fs.writeFileSync(`${output}/browser-result.json`,JSON.stringify({ok:true,completedAt:new Date().toISOString(),widths:[360,430,1440],homepage:!process.env.AUTOCHESS_BROWSER_SKIP_HOME,scenarios:['anonymous','original-lobby','five-bound-AIs-eight-seats','invited-friend','solo-NPC','skin-in-3D','slot-switch','account-switch-late-response','AI-wallet-readonly-403','AI-spectator','wheel','reconnect','battle-replay'],requestCount:requests.length,errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
