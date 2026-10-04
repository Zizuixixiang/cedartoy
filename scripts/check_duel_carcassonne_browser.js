/* Real page + real engine through subprocesses, local intercepted assets only.
 * Run with flock /tmp/cedartoy-duel-new-games-heavy.lock. Never install deps. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browserExecutable=process.env.PLAYWRIGHT_EXECUTABLE_PATH||chromium.executablePath();
const root=path.resolve(__dirname,'..'),out=process.env.CARCASSONNE_TEST_TMP||path.join(root,'artifacts/carcassonne/browser');
const widths=(process.env.DUEL_BROWSER_WIDTHS||'360,430,1280').split(',').map(Number);
const counts=(process.env.DUEL_BROWSER_PLAYERS||'2,5').split(',').map(Number);
assert.ok(widths.every(w=>[360,430,1280].includes(w)));assert.ok(counts.every(n=>[2,5].includes(n)));
const screenshotNames=new Set((process.env.DUEL_BROWSER_SHOTS||'preview,late').split(','));
fs.mkdirSync(out,{recursive:true});const tmp=fs.mkdtempSync(path.join(root,'artifacts','cc-'));
process.env.TMPDIR=tmp;process.env.XDG_CACHE_HOME=path.join(tmp,'cache');
const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:path.join(root,'vendor/duel'),TMPDIR:tmp};
let counter=0;
function backend(input){
 if(input.action==='seed')env.DUEL_DB_PATH=path.join(tmp,`scenario-${++counter}.db`);
 const names=['in.json','out.json','error.log'].map(n=>path.join(tmp,n));fs.writeFileSync(names[0],JSON.stringify(input));
 const fds=[fs.openSync(names[0],'r'),fs.openSync(names[1],'w'),fs.openSync(names[2],'w')];let r;
 try{r=spawnSync(process.env.DUEL_TEST_PYTHON||'python3',[path.join(root,'vendor/duel/tests/carcassonne_ui_fixture.py')],{env,cwd:root,stdio:fds,timeout:30000});}finally{fds.forEach(fd=>fs.closeSync(fd));}
 assert.equal(r.status,0,fs.readFileSync(names[2],'utf8')||String(r.error));return JSON.parse(fs.readFileSync(names[1],'utf8'));
}
(async()=>{
 let browser;const measurements=[];
 try {
  browser=await chromium.launch({headless:true,executablePath:browserExecutable,args:['--no-sandbox']});
  for(const width of widths)for(const count of counts) {
   let fixture,posts=[],errors=[],failNext=false;
   const page=await browser.newPage({viewport:{width,height:950},hasTouch:width<500,isMobile:width<500});page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());if(url.hostname!=='carcassonne.test')return route.abort();
    if(url.pathname==='/'||url.pathname.startsWith('/static/')){
     const rel=url.pathname==='/'?'index.html':url.pathname.slice(8),file=path.join(root,'vendor/duel/app/static',rel);
     if(!file.startsWith(path.join(root,'vendor/duel/app/static/'))||!fs.existsSync(file))return route.fulfill({status:404,body:''});
     return route.fulfill({body:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
    }
    let data={ok:true,rooms:[],notifications:[],timeline:[]};
    if(url.pathname==='/api/whoami')data={...data,bound:true,human_player_id:'p0',human_name:'南杉',machines:[],games:[{game_type:'carcassonne',display_name:'卡卡颂',category:'tabletop',allowed_player_counts:[2,3,4,5],recommended_players:4,supports_npcs:true,supports_stakes:true,supports_multiplayer_stakes:true}],wallet:{balance:200}};
    else if(/^\/api\/rooms\/[A-Z0-9]+$/.test(url.pathname))data=fixture;
    else if(url.pathname.endsWith('/move')) {
     const body=route.request().postDataJSON();posts.push(body);
     if(failNext){failNext=false;return route.fulfill({status:409,json:{ok:false,message:'测试：过期 revision，请刷新'}});}
     data=backend({action:'move',room_id:fixture.room.room_id,move:body.move,revision:body.revision});if(data.ok)fixture=data;
    }
    return route.fulfill({status:data.ok===false?(data.status||400):200,json:data});
   });
   const key=k=>page.locator(`[data-cc-key="${k}"]`);
   const press=locator=>width<500?locator.tap():locator.click();
   const view=()=>page.evaluate(()=>{
    const world=document.querySelector('.cc-world'),m=new DOMMatrix(getComputedStyle(world).transform);
    return {x:m.e,y:m.f,zoom:m.a,scrollY,documentHeight:document.documentElement.scrollHeight,focus:document.activeElement?.dataset.ccKey||document.activeElement?.className};
   });
   const sameView=async(before,label)=>{
    const after=await view();
    for(const k of ['x','y','zoom','scrollY'])assert.ok(Math.abs(after[k]-before[k])<.02,`${label} preserves ${k}: ${before[k]} -> ${after[k]}`);
   };
   const open=async()=>{
    await page.goto(`http://carcassonne.test/?room=${fixture.room.room_id}`);await page.locator('.cc-viewport').waitFor();
    const dismiss=page.locator('#dismissWaitModeModalButton');if(await dismiss.isVisible())await dismiss.click();
    await page.waitForFunction(()=>!document.querySelector('#waitModeModal:not(.hidden)'));
   };
   const seed=async name=>{fixture=backend({action:'seed',fixture:name,count,invited:count===5});assert.equal(fixture.ok,true,fixture.message);await open();};
   const screenshot=async name=>{
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${name}/${width}/${count}: overflow`);
    assert.equal(await page.locator('.cc-map-hint').innerText(),width<500?'拖动地图 · 双指缩放':'拖动地图 · 滚轮缩放');
    if(!screenshotNames.has(name))return;
    const file=path.join(out,`${name}-${count}-${width}.png`);
    if(width<500){
     // Full-page capture resets touch media in this Chromium. Capture the game
     // at the real phone viewport size, without resizing its emulated surface.
     const scroll=await page.evaluate(()=>scrollY);
     try{
      await page.locator('.cc-game').evaluate(e=>e.scrollIntoView({block:'start',behavior:'instant'}));
      await page.screenshot({path:file});
     }finally{await page.evaluate(y=>scrollTo({top:y,behavior:'instant'}),scroll);}
    }else await page.screenshot({path:file,fullPage:true});
    assert.equal(await page.locator('.cc-map-hint').innerText(),width<500?'拖动地图 · 双指缩放':'拖动地图 · 滚轮缩放');
   };
   const checkControls=async()=>{
    const layout=await page.evaluate(()=>{
     const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height};};
     const row=document.querySelector('.cc-confirm');
     return {buttons:[...document.querySelectorAll('.cc-controls button')].map(rect),row:rect(row),
      submit:rect(row.querySelector('[data-cc-key="submit"]')),skip:rect(row.querySelector('[data-cc-key="none"]')),summary:rect(row.querySelector('.cc-meta'))};
    });
    assert.ok(layout.buttons.every(b=>b.height>=36&&b.height<=38),`compact controls/${width}: ${JSON.stringify(layout)}`);
    assert.ok(Math.abs(layout.submit.right-layout.row.right)<1,'CTA aligned to right edge');
    assert.ok(layout.skip.right<=layout.summary.left&&layout.summary.right<=layout.submit.left,'secondary actions precede CTA');
    assert.ok(Math.abs(layout.submit.top-layout.skip.top)<1,'CTA stays on the same row');
   };
   await seed('scoring');assert.equal(await page.locator('.room-participant:visible').count(),count,'one shared card per participant');
   assert.equal(await page.locator('.cc-tile').count(),1);assert.equal(await key('submit').count(),0);
   for(const removed of ['zoom-in','zoom-out','find'])assert.equal(await key(removed).count(),0);
   assert.equal(await page.locator('.cc-regions').count(),0);
   assert.deepEqual(await page.locator('.cc-controls button').evaluateAll(bs=>bs.map(b=>b.dataset.ccKey)),['rotate']);
   assert.equal(await key('latest').getAttribute('aria-label'),'回到最新板块');
   assert.equal(await key('latest').evaluate(e=>e.parentElement.className),'cc-viewport');
   assert.equal(await key('latest').evaluate(e=>{const r=e.getBoundingClientRect(),v=e.parentElement.getBoundingClientRect();return r.left>=v.left&&r.right<=v.right&&r.top>=v.top&&r.bottom<=v.bottom&&r.height>=44;}),true);
   assert.equal(await page.locator('.cc-map-hint').innerText(),width<500?'拖动地图 · 双指缩放':'拖动地图 · 滚轮缩放');
   assert.equal(await page.locator('.cc-map-hint').evaluate(e=>e.previousElementSibling.className),'cc-viewport');
   await screenshot('opening');
   // Rotate twice, choose a real negative-coordinate city closure, place a knight.
   await press(key('rotate'));await press(key('rotate'));
   await press(page.locator('[data-coordinate="0,-1"]'));
   assert.equal(posts.length,0,'preview is local');await checkControls();await screenshot('no-meeple');await press(key('region-c0'));
   assert.equal(await key('region-c0').getAttribute('aria-pressed'),'true');
   assert.equal(await key('submit').innerText(),'确认放置');
   await press(key('region-c0'));assert.equal(await key('none').getAttribute('aria-pressed'),'true');
   await press(key('region-c0'));await press(key('none'));assert.equal(await key('region-c0').getAttribute('aria-pressed'),'false');
   await press(key('region-c0'));
   await checkControls();
   await screenshot('preview');const rev=fixture.room.revision;
   await page.locator('.cc-viewport').scrollIntoViewIfNeeded();
   const beforeBox=await page.locator('.cc-viewport').boundingBox();
   const cx=beforeBox.x+beforeBox.width/2,cy=beforeBox.y+beforeBox.height/2;
   await page.mouse.move(cx,cy);await page.mouse.down();await page.mouse.move(cx+65,cy+35,{steps:5});await page.mouse.up();
   const initialZoom=(await view()).zoom;await page.mouse.wheel(0,-100);
   await page.waitForFunction(z=>new DOMMatrix(getComputedStyle(document.querySelector('.cc-world')).transform).a>z,initialZoom);
   await key('submit').scrollIntoViewIfNeeded();await key('submit').focus();
   const placedView=await view();
   if(width>=500)await page.keyboard.press('Enter');else await press(key('submit'));
   await page.waitForFunction(()=>document.querySelectorAll('.cc-tile').length===2);
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await sameView(placedView,'submit');
   if(width>=500)assert.equal(await page.evaluate(()=>document.activeElement.className),'cc-viewport','keyboard focus returns without scrolling');
   assert.equal(posts.length,1);assert.equal(posts[0].revision,rev);assert.equal(fixture.room.revision,rev+1);
   assert.equal(fixture.room.board_state.scores.p0,4);assert.equal(fixture.room.board_state.supply.p0,7);
   assert.equal(await page.locator('.cc-meeple').count(),0);assert.match(await page.locator('.cc-activity').innerText(),/城市 4分/);
   await screenshot('scored');
   fixture=backend({action:'state',room_id:fixture.room.room_id});await page.evaluate(()=>refreshRoom({quiet:true}));
   await sameView(placedView,'state refresh');assert.equal(await page.locator('.cc-tile').count(),2);
   assert.equal(fixture.room.board_state.scores.p0,4);assert.equal(await key('submit').count(),0);
   fixture=backend({action:'opponents',room_id:fixture.room.room_id});await page.evaluate(()=>refreshRoom({quiet:true}));
   await sameView(placedView,'opponent revision');
   assert.equal(await page.locator('.cc-draft').count(),0);assert.equal(await page.locator('.cc-regions').count(),0);
   assert.equal(await page.locator('.cc-current-tile svg g').first().getAttribute('transform'),'rotate(0 50 50)');
   await press(key('latest'));const latestView=await view(),latest=fixture.room.board_state.last_action;
   assert.equal(latestView.zoom,placedView.zoom);
   assert.ok(Math.abs(latestView.x+(latest.x+.5)*80*latestView.zoom)<.02);
   assert.ok(Math.abs(latestView.y+(latest.y+.5)*80*latestView.zoom)<.02);
   assert.ok(Math.abs(latestView.x-placedView.x)>.02||Math.abs(latestView.y-placedView.y)>.02,'only explicit latest recenters');
   await page.evaluate(()=>refreshRoom({quiet:true}));await sameView(latestView,'latest survives refresh');
   await page.locator('.cc-viewport').focus();await page.keyboard.press('Tab');
   assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('cc-spot')),true);
   await page.keyboard.press('Space');assert.equal(await key('none').getAttribute('aria-pressed'),'true');
   const before=fixture.room.revision;
   await page.evaluate(()=>scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'}));
   const bottomView=await view();
   await key('submit').click();await page.waitForFunction(()=>document.querySelector('.cc-current-copy strong')?.textContent.includes('等待'));
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await sameView(bottomView,'submit near page bottom');
   assert.equal(posts.at(-1).move.meeple,null);assert.equal(fixture.room.revision,before+1);
   // Switch rooms in the same loaded plugin, then return to each cached camera.
   const roomA=fixture,viewA=await view();
   fixture=backend({action:'seed',fixture:'scoring',count,invited:count===5});const roomB=fixture;
   const switchRoom=async data=>{
    fixture=data;await page.evaluate(id=>openRoom(id),data.room.room_id);
    const dismiss=page.locator('#dismissWaitModeModalButton');if(await dismiss.isVisible())await dismiss.click();
    await page.locator('.cc-viewport').waitFor();
   };
   await switchRoom(roomB);const viewB=await view(),defaultZoom=width<500?.85:1;
   assert.equal(viewB.zoom,defaultZoom);assert.equal(viewB.x,-40*defaultZoom);assert.equal(viewB.y,-40*defaultZoom);
   await page.locator('.cc-viewport').focus();await page.keyboard.press('+');await page.keyboard.press('ArrowLeft');const movedB=await view();
   await switchRoom(roomA);let restored=await view();for(const k of ['x','y','zoom'])assert.equal(restored[k],viewA[k],`room A restores ${k}`);
   assert.equal(await page.locator('.cc-draft').count(),0);
   await switchRoom(roomB);restored=await view();for(const k of ['x','y','zoom'])assert.equal(restored[k],movedB[k],`room B restores ${k}`);
   // Later map with 61 real tiles; exercise native touch/mouse events.
   await seed('late');assert.equal(await page.locator('.cc-tile').count(),61);
   assert.ok(await page.locator('.cc-meeple').count()>0);await screenshot('late');
   const world=()=>page.locator('.cc-world').evaluate(e=>{const m=new DOMMatrix(getComputedStyle(e).transform);return {x:m.e,z:m.a,y:m.f};});
   const near=(a,b)=>assert.ok(Math.abs(a-b)<.02,`${a} != ${b}`);
   const old=await world();await page.locator('.cc-viewport').scrollIntoViewIfNeeded();
   const box=await page.locator('.cc-viewport').boundingBox(),x=box.x+box.width/2,y=box.y+box.height/2;
   if(width<500){
    const session=await page.context().newCDPSession(page);
    const touch=async(type,points)=>{
     await session.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([id,x,y])=>({id,x,y}))});
     await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    await touch('touchStart',[[1,x,y]]);await touch('touchMove',[[1,x+80,y+50]]);await touch('touchEnd',[]);
    near((await world()).x,old.x+80);assert.equal(await page.locator('.cc-draft').count(),0);
    await key('latest').click();const base=await world(),scroll=await page.evaluate(()=>scrollY);
    await touch('touchStart',[[1,x-50,y],[2,x+50,y]]);
    await touch('touchMove',[[1,x-70,y+10],[2,x+90,y+10]]);
    let now=await world();near(now.z,base.z*1.6);near(-base.x/base.z,(10-now.x)/now.z);near(-base.y/base.z,(10-now.y)/now.z);
    assert.equal(await page.evaluate(()=>scrollY),scroll);
    // End the second finger explicitly; a smaller touchMove list does not release it.
    await touch('touchEnd',[[2,x+90,y+10]]);now=await world();
    await touch('touchMove',[[1,x-55,y+20]]);near((await world()).x,now.x+15);near((await world()).y,now.y+10);
    await touch('touchCancel',[]);assert.equal(await page.locator('.cc-draft').count(),0);
    // A fresh tap following a cancelled gesture still selects normally.
    await page.locator('.cc-viewport').focus();await page.keyboard.press('Tab');
    const focused=page.locator('.cc-spot:focus');await focused.tap();assert.equal(await page.locator('.cc-draft').count(),1);
    await session.detach();
   }else{
    await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+80,y+50,{steps:5});await page.mouse.up();
    near((await world()).x,old.x+80);assert.equal(await page.locator('.cc-draft').count(),0);
   }
   await key('latest').click();
   const base=await world(),scroll=await page.evaluate(()=>scrollY);
   await page.mouse.move(x+30,y+15);await page.mouse.wheel(100,0); // Horizontal wheel is contained too.
   await page.mouse.wheel(0,-100);
   await page.waitForFunction(z=>new DOMMatrix(getComputedStyle(document.querySelector('.cc-world')).transform).a>z,base.z);
   const zoomed=await world();near((30-base.x)/base.z,(30-zoomed.x)/zoomed.z);near((15-base.y)/base.z,(15-zoomed.y)/zoomed.z);
   assert.equal(await page.evaluate(()=>scrollY),scroll,'wheel must not scroll the page');
   await page.locator('.cc-viewport').focus();
   for(let i=0;i<12;i++)await page.keyboard.press('-');near((await world()).z,.6);
   const tileWidth=await page.locator('.cc-tile').first().evaluate(e=>e.getBoundingClientRect().width);assert.ok(tileWidth>=47.9);
   for(let i=0;i<12;i++)await page.keyboard.press('+');near((await world()).z,1.8);
   await key('latest').click();
   const centered=await page.locator('.cc-tile.is-latest').evaluate(e=>{const t=e.getBoundingClientRect(),v=e.closest('.cc-viewport').getBoundingClientRect();return {dx:t.x+t.width/2-v.x-v.width/2,dy:t.y+t.height/2-v.y-v.height/2};});
   near(centered.dx,0);near(centered.dy,0);assert.equal(await page.locator('.cc-new').count(),1);
   await page.locator('.cc-viewport').focus();const prior=await world();await page.keyboard.press('ArrowLeft');near((await world()).x,prior.x+80);
   // Native Tab/Enter and Tab/Space activation, including an offscreen target.
   await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('cc-spot')),true);
   assert.equal(await page.locator('.cc-spot:focus').evaluate(e=>getComputedStyle(e).outlineStyle),'solid');
   await page.keyboard.press('Enter');assert.equal(await page.locator('.cc-draft').count(),1);
   await page.keyboard.press('Tab');
   if(await page.evaluate(()=>document.activeElement.classList.contains('cc-spot')))await page.keyboard.press('Space');
   await key('none').focus();await page.keyboard.press('Space');assert.equal(await key('none').getAttribute('aria-pressed'),'true');
   const draft=await page.locator('.cc-spot.is-selected').getAttribute('data-coordinate');
   await page.mouse.move(x,y);await page.mouse.wheel(0,100);await key('latest').click();
   assert.equal(await page.locator('.cc-spot.is-selected').getAttribute('data-coordinate'),draft);
   assert.equal(await page.locator('.cc-new').count(),1);
   // Server conflict leaves a recoverable local draft and never reports success.
   failNext=true;const oldRev=fixture.room.revision;await key('submit').click();await page.locator('.cc-error').waitFor();
   assert.equal(fixture.room.revision,oldRev);assert.equal(await key('submit').isEnabled(),true);
   await key('submit').click();await page.waitForFunction(()=>document.querySelectorAll('.cc-tile').length===62);
   assert.equal(fixture.room.revision,oldRev+1);
   const geometry=await page.evaluate(()=>{
    const measure=selector=>{const e=document.querySelector(selector),s=getComputedStyle(e),r=e.getBoundingClientRect();return {width:r.width,height:r.height,font:s.fontFamily,fontSize:s.fontSize,lineHeight:s.lineHeight,padding:s.padding,border:s.border,shadow:s.boxShadow,gap:s.gap};};
    return {map:measure('.cc-viewport'),tile:measure('.cc-tile'),button:measure('.cc-locate'),card:measure('.room-participant'),chat:measure('#chatInput'),refresh:measure('#refreshButton')};
   });measurements.push({width,count,...geometry});
   if(count===5){await seed('terminal');assert.ok(await page.locator('.cc-result').isVisible());assert.equal(await key('submit').count(),0);await screenshot('terminal');}
   await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(()=>document.documentElement.style.fontSize='200%');
   if(width===360)await screenshot('enlarged');
   assert.deepEqual(errors,[]);await page.close();
   fs.writeFileSync(path.join(out,`measurements-${width}-${count}.json`),JSON.stringify(measurements.at(-1),null,2));
   console.log(JSON.stringify({ok:true,width,count}));
  }
  fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measurements,null,2));
  console.log(JSON.stringify({ok:true,widths,players:counts,scenarios:measurements.length,actions:['rotate','coordinate','region','none','confirm','pan','anchoredWheel','pinch','pinchToPan','cancelAndTap','zoomBounds','latest','keyboardEnterSpace','reload','conflictRetry','cameraAcrossRevisions','stablePageScroll','roomIsolation','keyboardSubmitFocus'],screenshots:out}));
 }finally{if(browser)await browser.close();fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
