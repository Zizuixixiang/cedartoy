/* Actual app + registry + plugin, isolated real engine requests, no external network. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const out=process.env.BOMB_PLANE_TEST_TMP||path.join(root,'artifacts/bomb_plane/browser');
fs.mkdirSync(out,{recursive:true});
const tmp=fs.mkdtempSync(path.join(root,'artifacts','bp-'));
const widths=(process.env.DUEL_BROWSER_WIDTHS||'360,430,1280').split(',').map(Number);
const roomKinds=(process.env.DUEL_BROWSER_ROOMS||'ordinary,invite').split(',');
const focused=process.env.DUEL_BROWSER_FOCUSED==='1';
assert.ok(widths.every(w=>[360,430,1280].includes(w)));
assert.ok(roomKinds.every(k=>['ordinary','invite'].includes(k)));
const screenshotNames=new Set((process.env.DUEL_BROWSER_SHOTS||'empty,preview-north,preview-east,preview-south,preview-west,one-plane,two-planes,invalid,duplicate-head,overlap,head-on-body,random,cleared,manual-three,locked,attack,shot-miss,shot-head,shot-hit,own-under-fire,terminal,enlarged,enlarged-setup,waiting-own,incoming-e5,incoming-e4,attack-separated,head-feedback,turn-e6-attack,turn-e6-manual-own,incoming-miss,latest-hit-h5,outgoing-g3,queued-outgoing,queued-incoming').split(','));
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browserExecutable=process.env.PLAYWRIGHT_EXECUTABLE_PATH||chromium.executablePath();
process.env.TMPDIR=tmp;process.env.XDG_CACHE_HOME=path.join(tmp,'cache');
const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:path.join(root,'vendor/duel'),TMPDIR:tmp};
let scenario=0;
function backend(input){
 if(input.action==='seed')env.DUEL_DB_PATH=path.join(tmp,`${++scenario}.db`);
 const files=['request.json','response.json','error.log'].map(f=>path.join(tmp,f));
 fs.writeFileSync(files[0],JSON.stringify(input));
 const fds=[fs.openSync(files[0],'r'),fs.openSync(files[1],'w'),fs.openSync(files[2],'w')];let res;
 try{res=spawnSync(process.env.DUEL_TEST_PYTHON||'python3',[path.join(root,'vendor/duel/tests/bomb_plane_ui_fixture.py')],{stdio:fds,env,timeout:15000});}finally{fds.forEach(fd=>fs.closeSync(fd));}
 assert.equal(res.status,0,fs.readFileSync(files[2],'utf8'));return JSON.parse(fs.readFileSync(files[1],'utf8'));
}
(async()=>{
 let browser;const measurements=[];
 try {
  browser=await chromium.launch({headless:true,executablePath:browserExecutable,args:['--no-sandbox']});
  for(const width of widths)for(const kind of roomKinds){
   const invited=kind==='invite';
   let fixture=backend({action:'seed',invited}),viewer='p0';const posts=[],errors=[];
   const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<500});
   await page.addInitScript(()=>{
    const native=window.setTimeout.bind(window),clear=window.clearTimeout.bind(window);
    window.bpDelays=[];
    window.setTimeout=(fn,ms,...args)=>{
     if(ms!==2000)return native(fn,ms,...args);
     const entry={start:performance.now(),fired:null,canceled:false};window.bpDelays.push(entry);
     entry.id=native(()=>{entry.fired=performance.now();fn(...args);},ms);return entry.id;
    };
    window.clearTimeout=id=>{const entry=window.bpDelays.find(e=>e.id===id);if(entry)entry.canceled=true;clear(id);};
   });
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url()),p=url.pathname;
    if(url.hostname!=='bomb-plane.test')return route.abort();
    if(p==='/'||p.startsWith('/static/')){
     const rel=p==='/'?'index.html':p.slice(8),file=path.join(root,'vendor/duel/app/static',rel);
     if(!file.startsWith(path.join(root,'vendor/duel/app/static/'))||!fs.existsSync(file))return route.fulfill({status:404,body:''});
     return route.fulfill({body:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
    }
    let data={ok:true,notifications:[],rooms:[],timeline:[]};
    if(p==='/api/whoami')data={...data,bound:true,human_player_id:viewer,human_name:viewer==='p0'?'玩家零':'玩家一',machines:[],games:fixture.games,wallet:{balance:100}};
    else if(/^\/api\/rooms\/[A-Z0-9]+$/.test(p))data=fixture;
    else if(p.endsWith('/move')){
     const body=route.request().postDataJSON();posts.push(body);
     data=backend({action:'move',room_id:fixture.room.room_id,viewer,move:body.move,revision:body.revision});if(data.ok)fixture=data;
    }
    return route.fulfill({status:data.ok===false?(data.status||400):200,json:data});
   });
   const key=k=>page.locator(`[data-bp-key="${k}"]`);
   const activate=async k=>width<500?key(k).tap():key(k).click();
   const open=async()=>{
    await page.goto(`http://bomb-plane.test/?room=${fixture.room.room_id}`);await page.locator('.bp-grid').waitFor();
    await page.waitForFunction(()=>getComputedStyle(document.querySelector('.bp-grid')).display==='grid');
    const dismiss=page.locator('#dismissWaitModeModalButton');if(await dismiss.isVisible())await dismiss.click();
    await page.waitForFunction(()=>!document.querySelector('#waitModeModal:not(.hidden)'));
   };
   const shot=async name=>{
    await page.evaluate(()=>window.scrollTo(0,0));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${name}/${width} overflow`);
    if(!screenshotNames.has(name))return;
    await page.screenshot({path:path.join(out,`${invited?'invite':'ordinary'}-${width}-${name}.png`),fullPage:true});
    // A focused crop keeps the board and controls readable alongside the full page.
    await page.locator('.board-zone').screenshot({path:path.join(out,`${kind}-${width}-${name}-board.png`)});
   };
   const move=async keyName=>{
    const rev=fixture.room.revision;await activate(keyName);
    await page.waitForFunction(()=>!document.querySelector('.bp-status')?.textContent.includes('undefined'));
    // Network fixture writes are synchronous, but allow the host render to settle.
    await page.waitForTimeout(120);
    assert.equal(fixture.room.revision,rev+1,`${keyName} applied`);assert.equal(posts.at(-1).revision,rev);
   };
   const measureGeometry=()=>page.evaluate(()=>{
    const props=s=>{const e=document.querySelector(s),c=getComputedStyle(e),r=e.getBoundingClientRect();return {x:r.x,width:r.width,height:r.height,font:c.fontFamily,size:c.fontSize,border:c.border,shadow:c.boxShadow,padding:c.padding};};
    return {board:props('.bp-game'),controls:props('.bp-controls'),cell:props('.bp-cell'),button:props('.bp-button'),chat:props('#chatInput'),refresh:props('#refreshButton'),roster:document.querySelectorAll('.participant-badge').length};
   });
   if(!focused){
   await open();await shot('empty');
   const setupGeometry=await page.evaluate(()=>{
    const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
    return {controls:rect('.bp-controls'),rotate:rect('[data-bp-key="rotate"]'),place:rect('[data-bp-key="place"]'),
      shuffle:rect('[data-bp-key="shuffle"]'),undo:rect('[data-bp-key="undo"]'),clear:rect('[data-bp-key="clear"]')};
   });
   assert.ok(setupGeometry.controls.height<120,'compact setup controls');
   assert.equal(setupGeometry.rotate.y,setupGeometry.place.y);assert.equal(setupGeometry.shuffle.y,setupGeometry.undo.y);assert.equal(setupGeometry.undo.y,setupGeometry.clear.y);
   assert.ok(setupGeometry.rotate.height>=44);assert.ok(setupGeometry.shuffle.height>=40);
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');assert.equal(await key('attack').isDisabled(),true);
   assert.match(await page.locator('.bp-status').textContent(),/已放置 0\/3 架（可重叠）/);
   assert.equal(await page.locator('.bp-steps, .bp-directions, #bp-shuffle-help').count(),0);
   assert.equal(await page.locator('.bp-status').count(),1);assert.equal(await page.locator('#gameMessage').textContent(),'');
   assert.doesNotMatch(await page.locator('.bp-game').textContent()+await page.locator('.bp-controls').textContent(),/①|②|③|共放3架|仅生成布阵|先点棋盘|确认放置/);
   await page.emulateMedia({reducedMotion:'reduce'});
   await page.evaluate(()=>{const sizes=[...document.querySelectorAll('.bp-game *, .bp-controls *')].map(e=>[e,parseFloat(getComputedStyle(e).fontSize)]);sizes.forEach(([e,size])=>{e.style.fontSize=`${size*2}px`;});});
   await shot('enlarged-setup');
   await open();
   viewer='p1';fixture=backend({action:'state',room_id:fixture.room.room_id,viewer});await open();
   assert.equal(await page.locator('.bp-cell:enabled').count(),100);await activate('C1');await move('place');
   assert.equal(fixture.room.current_player_id,'p0');await move('undo');
   viewer='p0';fixture=backend({action:'state',room_id:fixture.room.room_id,viewer});await open();
   const localPosts=posts.length;
   await activate('E5');
   await shot('preview-north');
   for(const [direction,name] of [['右','east'],['下','south'],['左','west'],['上','north']]){
    await key('rotate').focus();await page.keyboard.press('Space');
    assert.equal(await key('rotate').getAttribute('aria-label'),`旋转，当前朝${direction}`);
    assert.equal(await page.locator('.bp-preview').count(),10);await shot(`preview-${name}`);
   }
   await activate('rotate');
   assert.equal(await page.locator('.bp-preview').count(),10);assert.equal(posts.length,localPosts);
   await shot('preview-east');await move('place');
   assert.deepEqual(fixture.room.private_state.planes,[{head:'E5',direction:'E'}]);
   fixture=backend({action:'state',room_id:fixture.room.room_id});await open();
   assert.equal(await page.locator('.bp-plane').count(),10);assert.equal(await page.locator('.bp-fleet li').count(),1);await shot('one-plane');
   await activate('E5');assert.match(await page.locator('.bp-error').textContent(),/机头不能重合/);assert.equal(await key('place').isDisabled(),true);await shot('duplicate-head');
   await activate('D5');assert.equal(await key('place').isDisabled(),false);assert.equal(await page.locator('.bp-preview').count(),10);await shot('head-on-body');await move('place');
   assert.equal(await page.locator('.bp-fleet li').count(),2);await shot('overlap');
   await activate('E4');await move('place');assert.equal(await key('E5').textContent(),'→');assert.match(await key('E5').getAttribute('aria-label'),/飞机1机头朝右/);
   assert.equal(await page.locator('.bp-fleet li').count(),3);assert.ok(await page.locator('.bp-plane').count()<30);
   await move('undo');await move('undo');
   await move('undo');assert.equal(fixture.room.private_state.planes.length,0);
   await activate('A1');assert.equal(await key('place').isDisabled(),true);assert.match(await page.locator('.bp-error').textContent(),/飞机出界/);await shot('invalid');
   await move('shuffle');assert.equal(fixture.room.private_state.planes.length,3);assert.equal(fixture.room.board_state.ready.p0,false);
   assert.ok(await page.locator('.bp-plane').count()<=30);assert.equal(new Set(fixture.room.private_state.planes.map(p=>p.head)).size,3);assert.equal(await page.locator('.bp-fleet li').count(),3);assert.equal(await key('ready').isDisabled(),false);await shot('random');
   await move('shuffle');assert.equal(fixture.room.board_state.ready.p0,false);
   await move('clear');assert.equal(await page.locator('.bp-plane').count(),0);await shot('cleared');
   for(const [i,cell] of ['C1','H1','C6'].entries()){await activate(cell);await move('place');assert.equal(await page.locator('.bp-fleet li').count(),i+1);if(i===1)await shot('two-planes');}
   await shot('manual-three');await move('ready');assert.equal(fixture.room.board_state.ready.p0,true);
   assert.equal(await key('shuffle').count(),0);assert.equal(await key('attack').isDisabled(),true);await shot('locked');
   // Other participant sees none of our planes and has its own empty board.
   const roomId=fixture.room.room_id;viewer='p1';fixture=backend({action:'state',room_id:roomId,viewer});await open();
   assert.equal(await page.locator('.bp-plane').count(),0);assert.equal(fixture.room.private_state.planes.length,0);await shot('other-private');
   viewer='p0';fixture=backend({action:'opponent',room_id:roomId});await open();
   assert.equal(await key('attack').getAttribute('aria-pressed'),'true');assert.equal(await page.locator('.bp-plane').count(),0);
   assert.equal(await key('place').count(),0);assert.equal(await key('rotate').count(),0);await shot('attack');
   for(const [cell,result] of [['J10','miss'],['C1','head'],['C2','hit']]){
    await activate(cell);await move('attack-confirm');assert.equal(fixture.room.board_state.shots.p0.at(-1).result,result);
    await shot(`shot-${result}`);fixture=backend({action:'opponent',room_id:roomId});await open();
   }
   assert.equal(await key('C1').isDisabled(),true);assert.equal(await page.locator('.bp-plane').count(),0);
   await activate('own');assert.equal(await page.locator('.bp-plane').count(),30);assert.equal(await key('attack-confirm').count(),0);await shot('own-under-fire');
   // Keyboard selection and reduced motion use the same click-free interaction path.
   await activate('attack');await key('E5').focus();await page.keyboard.press('Space');assert.equal(await key('attack-confirm').textContent(),'确认攻击 E5');
   const geometry=await measureGeometry();measurements.push({width,invited,setup:setupGeometry,...geometry});assert.ok(geometry.cell.width>=24);assert.ok(geometry.button.height>=40);
   assert.ok(Math.abs(geometry.board.width-geometry.controls.width)<=1,'board and controls have matching width');
   assert.ok(Math.abs(geometry.board.x-geometry.controls.x)<=1,'board and controls align');
   fixture=backend({action:'finish',room_id:roomId});await open();assert.equal(await page.locator('.bp-plane').count(),30);await shot('terminal');
   await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(()=>{const sizes=[...document.querySelectorAll('.bp-game *, .bp-controls *')].map(e=>[e,parseFloat(getComputedStyle(e).fontSize)]);sizes.forEach(([e,size])=>{e.style.fontSize=`${size*2}px`;});});await shot('enlarged');
   }
   // A new real fixture, then refresh through renderGame without page reloads:
   // exercise the same revision-reset mechanism used by polling and move replies.
   fixture=backend({action:'seed',invited});await open();
   const incomingRoom=fixture.room.room_id;
   const render=async()=>page.evaluate(f=>renderGame(f.room,f.message,f.timeline),fixture);
   const engineMove=async(pid,movePayload)=>{
    const result=backend({action:'move',room_id:incomingRoom,viewer:pid,revision:fixture.room.revision,move:movePayload});assert.equal(result.ok,true,result.message);
    fixture=backend({action:'state',room_id:incomingRoom,viewer:'p0'});await render();
   };
   await engineMove('p0',{action:'set_layout',planes:[{head:'E3',direction:'N'},{head:'H2',direction:'N'},{head:'C6',direction:'N'}]});
   assert.equal(await page.locator('.bp-plane-head').count(),3);
   await engineMove('p0',{action:'ready'});fixture=backend({action:'opponent',room_id:incomingRoom});await render();
   assert.equal(await key('attack').getAttribute('aria-pressed'),'true');
   assert.equal(await page.locator('.bp-plane-head').count(),0);
   if(focused){
    const geometry=await measureGeometry();measurements.push({width,invited,...geometry});
    assert.ok(geometry.cell.width>=24);assert.ok(geometry.button.height>=40);
    assert.ok(Math.abs(geometry.board.width-geometry.controls.width)<=1);
    assert.ok(Math.abs(geometry.board.x-geometry.controls.x)<=1);
   }
   await activate('G3');await move('attack-confirm');
   assert.equal(fixture.room.board_state.shots.p0.at(-1).result,'miss');assert.equal(await key('attack').getAttribute('aria-pressed'),'true');
   assert.equal(await page.locator('.bp-latest-outgoing').getAttribute('data-cell'),'G3');assert.equal(await key('G3').textContent(),'空');
   await shot('outgoing-g3');const outgoingCount=await page.evaluate(()=>bpDelays.length);
   await render();await render();assert.equal(await page.evaluate(()=>bpDelays.length),outgoingCount);
   await page.waitForFunction(()=>document.querySelector('[data-bp-key="own"]').getAttribute('aria-pressed')==='true');
   const outgoingDuration=await page.evaluate(()=>bpDelays.at(-1).fired-bpDelays.at(-1).start);
   assert.ok(outgoingDuration>=1900);measurements.at(-1).outgoingDelayMs=outgoingDuration;
   assert.match(await page.locator('.bp-status').textContent(),/等待对方攻击/);await shot('waiting-own');
   const gridY=await page.locator('.bp-grid').evaluate(e=>e.getBoundingClientRect().y);
   await activate('attack');await engineMove('p1',{action:'attack',cell:'E5'});
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');
   assert.equal(await page.locator('.bp-incoming').textContent(),'对方刚攻击 E5 · 伤');
   await activate('own');await page.waitForTimeout(2100);await render();await render();await key('own').focus();await page.evaluate(()=>renderBoard([]));
   assert.equal(await page.evaluate(()=>bpDelays.at(-1).canceled && bpDelays.at(-1).fired===null),true);
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');assert.equal(await key('E5').textContent(),'伤');
   await page.evaluate(f=>renderGame({...f.room,revision:f.room.revision+1},'',[]),fixture);await render();
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');
   assert.equal(await page.locator('.bp-latest-incoming').getAttribute('data-cell'),'E5');
   assert.equal(await page.locator('.bp-grid').evaluate(e=>e.getBoundingClientRect().y),gridY,'incoming text reserves its row');
   await shot('incoming-e5');await activate('attack');await render();await render();
   assert.equal(await key('attack').getAttribute('aria-pressed'),'true');assert.equal(await key('E5').textContent(),'');assert.equal(await key('G3').textContent(),'空');
   assert.equal(await page.locator('.bp-plane-head, .bp-latest-incoming').count(),0);await shot('attack-separated');
   await activate('J10');await move('attack-confirm');assert.equal(await key('attack').getAttribute('aria-pressed'),'true');
   await activate('attack');await page.waitForTimeout(2100);await render();await render();
   assert.equal(await page.evaluate(()=>bpDelays.at(-1).fired),null,'active attack tab cancels outgoing dwell');
   await page.evaluate(f=>renderGame({...f.room,revision:f.room.revision+1},'',[]),fixture);await render();
   assert.equal(await key('attack').getAttribute('aria-pressed'),'true');await activate('own');
   await engineMove('p1',{action:'attack',cell:'E4'});
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');await activate('attack');await page.waitForTimeout(2100);
   assert.equal(await key('attack').getAttribute('aria-pressed'),'true');assert.equal(await page.evaluate(()=>bpDelays.at(-1).fired),null);
   await activate('own');await render();
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');assert.equal(await key('E4').textContent(),'伤');assert.equal(await key('E5').textContent(),'伤');
   assert.equal(await page.locator('.bp-incoming').textContent(),'对方刚攻击 E4 · 伤');
   assert.equal(await page.locator('.bp-latest-incoming').count(),1);assert.equal(await page.locator('.bp-latest-incoming').getAttribute('data-cell'),'E4');
   const colors=await page.evaluate(()=>{
    const styles=k=>getComputedStyle(document.querySelector(`[data-bp-key="${k}"]`));
    return {head:styles('E3').backgroundColor,body:styles('E6').backgroundColor,hit:styles('E4').backgroundColor,latestOutline:styles('E4').outlineWidth,latestOffset:styles('E4').outlineOffset,oldOutlineStyle:styles('E5').outlineStyle,hintSize:getComputedStyle(document.querySelector('.bp-incoming')).fontSize};
   });
   assert.equal(colors.head,'rgb(255, 199, 181)');assert.equal(colors.body,'rgb(229, 221, 240)');assert.equal(colors.hit,'rgb(255, 176, 0)');assert.equal(colors.latestOutline,'4px');assert.equal(colors.latestOffset,'0px');assert.equal(colors.oldOutlineStyle,'none');assert.equal(colors.hintSize,'12px');
   await shot('incoming-e4');
   // Prepare authoritative projections first: Python startup/SQLite latency is
   // unrelated to the fast-delivery scenario and must not consume its 2 seconds.
   const sent=backend({action:'move',room_id:incomingRoom,viewer:'p0',revision:fixture.room.revision,move:{action:'attack',cell:'I10'}});
   assert.equal(sent.ok,true,sent.message);
   const reply=backend({action:'move',room_id:incomingRoom,viewer:'p1',revision:sent.room.revision,move:{action:'attack',cell:'E6'}});
   assert.equal(reply.ok,true,reply.message);
   const received=backend({action:'state',room_id:incomingRoom,viewer:'p0'});
   fixture=sent;await render();const serialStart=await page.evaluate(()=>bpDelays.length-1);
   fixture=received;await render();
   assert.equal(await key('attack').getAttribute('aria-pressed'),'true','fast reply must finish outgoing first');
   assert.equal(await page.locator('.bp-latest-outgoing').getAttribute('data-cell'),'I10');
   assert.equal(await page.evaluate(()=>bpDelays.length),serialStart+1,'reply must not start a competing timer');
   await shot('queued-outgoing');await render();
   await page.waitForFunction(()=>document.querySelector('[data-bp-key="own"]').getAttribute('aria-pressed')==='true');
   assert.equal(await page.locator('.bp-latest-incoming').getAttribute('data-cell'),'E6');
   await shot('queued-incoming');
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');assert.equal(await page.locator('.bp-incoming').textContent(),'对方刚攻击 E6 · 伤');
   const delayCount=await page.evaluate(()=>bpDelays.length);await page.waitForTimeout(1000);await render();await render();
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');assert.equal(await page.evaluate(()=>bpDelays.length),delayCount);
   await page.waitForFunction(()=>document.querySelector('[data-bp-key="attack"]').getAttribute('aria-pressed')==='true');
   const duration=await page.evaluate(()=>bpDelays.at(-1).fired-bpDelays.at(-1).start);assert.ok(duration>=1900,`incoming shown for ${duration}ms`);
   measurements.at(-1).incomingDelayMs=duration;
   const serial=await page.evaluate(i=>bpDelays.slice(i).map(e=>({start:e.start,fired:e.fired})),serialStart);
   assert.equal(serial.length,2);assert.ok(serial[0].fired-serial[0].start>=1900);
   assert.ok(serial[1].start>=serial[0].fired,'incoming starts after outgoing finishes');measurements.at(-1).serialDwell=serial;
   await shot('turn-e6-attack');await activate('own');await render();await render();
   await page.waitForTimeout(2100);assert.equal(await page.evaluate(()=>bpDelays.length),delayCount);
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');assert.equal(await page.locator('.bp-latest-incoming').getAttribute('data-cell'),'E6');await shot('turn-e6-manual-own');
   await activate('attack');await activate('H10');await move('attack-confirm');await activate('own');await page.waitForTimeout(2100);await render();
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');assert.equal(await page.evaluate(()=>bpDelays.at(-1).fired),null,'own tab cancels outgoing');
   await engineMove('p1',{action:'attack',cell:'E3'});
   assert.equal(await key('own').getAttribute('aria-pressed'),'true');await activate('own');
   assert.equal(await key('E3').textContent(),'头');assert.equal(await key('E3').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(213, 43, 32)');await shot('head-feedback');
   await activate('attack');await activate('G10');await move('attack-confirm');await engineMove('p1',{action:'attack',cell:'J10'});await activate('own');
   assert.equal(await key('J10').textContent(),'空');assert.equal(await key('J10').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(8, 123, 158)');await shot('incoming-miss');
   await activate('attack');await activate('F10');await move('attack-confirm');await engineMove('p1',{action:'attack',cell:'H5'});await activate('own');
   assert.equal(await key('H5').textContent(),'伤');assert.equal(await page.locator('.bp-latest-incoming').getAttribute('data-cell'),'H5');
   const resultLayer=await page.evaluate(()=>{
    const read=cell=>{const e=document.querySelector(`[data-bp-key="${cell}"]`),s=getComputedStyle(e),r=e.getBoundingClientRect();return {cell,fill:s.backgroundColor,color:s.color,size:s.fontSize,weight:s.fontWeight,border:s.borderTopWidth,outline:s.outlineWidth,outlineStyle:s.outlineStyle,width:r.width,height:r.height};};
    return {results:['E3','E4','E5','E6','J10','H5'].map(read),body:read('H4')};
   });
   for(const result of resultLayer.results){
    assert.equal(result.size,'15px');assert.ok(Number(result.weight)>=800);assert.equal(result.border,'2px');
    assert.ok(Math.abs(result.height-resultLayer.body.height)<=1,'result styling preserves cell height');
    assert.ok(Math.abs(result.width-resultLayer.body.width)<=1,'result styling preserves cell width');
   }
   assert.equal(resultLayer.body.fill,'rgb(229, 221, 240)');assert.equal(resultLayer.body.size,'12px');
   assert.equal(resultLayer.results.at(-1).fill,'rgb(255, 176, 0)');assert.equal(resultLayer.results.at(-1).outline,'4px');
   assert.equal(resultLayer.results.find(r=>r.cell==='E5').outlineStyle,'none');
   measurements.at(-1).resultLayer=resultLayer;await shot('latest-hit-h5');
   assert.deepEqual(errors,[]);await page.close();
   fs.writeFileSync(path.join(out,`measurements-${width}-${kind}.json`),JSON.stringify(measurements.at(-1),null,2));
   console.log(JSON.stringify({ok:true,width,kind}));
  }
  fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measurements,null,2));
  console.log(JSON.stringify({ok:true,widths,roomKinds,scenarios:measurements.length,screenshots:out,measurements}));
 }finally{if(browser)await browser.close();fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});
