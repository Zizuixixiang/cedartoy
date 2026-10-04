/* Render/interaction acceptance. No server or production network: intercept all
 * requests and drive the actual engine in an explicitly disposable SQLite DB.
 * Requires an already installed Playwright + Chromium; never installs anything.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname,'..');
const out = process.env.RUMMIKUB_TEST_TMP || path.join(root,'artifacts/rummikub');
fs.mkdirSync(out,{recursive:true});
const tmp = fs.mkdtempSync(path.join(out,'browser-'));
process.env.TMPDIR = tmp;
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const widths=(process.env.RUMMIKUB_TEST_WIDTHS || '360,430,1280').split(',').map(Number);
assert.ok(widths.length && widths.every(width=>[360,430,1280].includes(width)),'supported viewport widths');
const env={...process.env,DUEL_DB_PATH:path.join(tmp,'browser.db'),PYTHONPATH:path.join(root,'vendor/duel'),PYTHONPYCACHEPREFIX:path.join(tmp,'pycache')};
let fixtureNumber=0;
function backend(input) {
  // Each scenario gets a fresh database, so unrelated active-room limits do not
  // accumulate across viewport repetitions. Moves/reloads retain this same DB.
  if(input.action==='seed')env.DUEL_DB_PATH=path.join(tmp,`fixture-${++fixtureNumber}.db`);
  const paths=['request.json','response.json','error.log'].map(n=>path.join(tmp,n));
  fs.writeFileSync(paths[0],JSON.stringify(input));
  const fds=[fs.openSync(paths[0],'r'),fs.openSync(paths[1],'w'),fs.openSync(paths[2],'w')];
  let r;
  try { r=spawnSync(process.env.DUEL_TEST_PYTHON || 'python3',[path.join(root,'vendor/duel/tests/rummikub_ui_fixture.py')],
    {env,stdio:fds,cwd:root,timeout:15000}); } finally { fds.forEach(fd=>fs.closeSync(fd)); }
  assert.equal(r.status,0,fs.readFileSync(paths[2],'utf8') || String(r.error||''));
  return JSON.parse(fs.readFileSync(paths[1],'utf8'));
}
(async()=>{
 let browser;
 const measurements=[];
 try {
  for(const width of widths) for(const count of [2,4]) {
    // Release Chromium resources between the independent viewport/seat cases.
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    let fixture, viewer='p0', posts=[];
    const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<500});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url()), pathname=url.pathname;
      if(url.hostname!=='rummikub.test') return route.abort();
      if(pathname==='/' || pathname.startsWith('/static/')) {
        const rel=pathname==='/' ? 'index.html' : pathname.slice('/static/'.length);
        const file=path.join(root,'vendor/duel/app/static',rel);
        if(!file.startsWith(path.join(root,'vendor/duel/app/static/')) || !fs.existsSync(file)) return route.fulfill({status:404,body:''});
        return route.fulfill({body:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
      }
      let data={ok:true,notifications:[],rooms:[],timeline:[]};
      if(pathname==='/api/whoami') data={...data,bound:true,human_player_id:viewer,human_name:viewer==='p0'?'玩家0':'玩家1',machines:[],
        games:[{game_type:'rummikub',display_name:'拉密',category:'tabletop',allowed_player_counts:[2,3,4],recommended_players:4,supports_npcs:true,supports_stakes:false}],wallet:{balance:0}};
      else if(pathname.match(/^\/api\/rooms\/[A-Z0-9]+$/)) data=fixture;
      else if(pathname.endsWith('/move')) {
        const body=route.request().postDataJSON(); posts.push(body);
        data=backend({action:'move',room_id:fixture.room.room_id,move:body.move,revision:body.revision});
        if(data.ok) fixture=data;
      }
      return route.fulfill({status:data.ok===false?(data.status||400):200,json:data});
    });
    const key=k=>page.locator(`[data-rk-focus="${k}"]`);
    const tapGroup=async(index)=>{
      const group=page.locator(`[data-group="${index}"]`);await group.scrollIntoViewIfNeeded();
      const r=await group.boundingBox();
      // The group center is normally a tile: rack-selection context must make
      // the entire surface a target, not accidentally select that public tile.
      if(width<500) await page.touchscreen.tap(r.x+r.width/2,r.y+r.height/2);
      else await page.mouse.click(r.x+r.width/2,r.y+r.height/2);
    };
    const pick=async(ids,where='.rk-hand')=>{for(const id of ids)await page.locator(`${where} [data-tile-id="${id}"]`).click();};
    const open=async()=>{
      await page.goto(`http://rummikub.test/?room=${fixture.room.room_id}`);
      await page.locator('.rk-hand .rk-tile').first().waitFor();
      const dismiss=page.locator('#dismissWaitModeModalButton');
      if(await dismiss.isVisible())await dismiss.click();
      await page.waitForFunction(()=>!document.querySelector('#waitModeModal:not(.hidden)'));
    };
    const seed=async name=>{viewer='p0';fixture=backend({action:'seed',fixture:name,count,invited:count===4});assert.equal(fixture.ok,true,fixture.message);await open();};
    const screenshot=async name=>{
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${name}/${count}/${width} overflow`);
      assert.equal(await page.locator('.rk-table').evaluate(n=>n.scrollWidth>n.clientWidth),false,`${name} table has no horizontal overflow`);
      await page.screenshot({path:path.join(out,`${name}-${count}-${width}.png`),fullPage:true});
    };
    const rackGeometry=()=>page.locator('.rk-hand').evaluate(n=>{
      const tiles=[...n.querySelectorAll('.rk-tile')].map(t=>t.getBoundingClientRect());
      return {count:tiles.length,rows:new Set(tiles.map(t=>Math.round(t.top))).size,width:tiles[0].width,height:tiles[0].height,
        contentHeight:n.scrollHeight,viewportHeight:n.clientHeight,numberSize:parseFloat(getComputedStyle(n.querySelector('.rk-number')).fontSize),
        colorSize:parseFloat(getComputedStyle(n.querySelector('.rk-color')).fontSize)};
    });
    const submit=async()=>{
      const rev=fixture.room.revision, before=posts.length;
      await key('submit').click();
      await page.waitForFunction(()=>document.querySelector('.rk-draft-status')?.textContent.includes('等待'));
      assert.equal(posts.length,before+1);assert.equal(fixture.room.revision,rev+1);
      assert.equal(posts.at(-1).revision,rev);
    };
    await seed('opening');
    assert.equal(fixture.room.participants.length,count);
    assert.equal(await page.locator('.rk-hand .rk-tile').count(),14);
    for(const k of ['new','move','return','clear','undo','reset']) assert.equal(await key(k).count(),0,`${k} hidden initially`);
    assert.equal(await page.locator('.rk-controls .rk-button').count(),2,'only core actions remain');
    const idleControls=await page.locator('.rk-controls').evaluate(n=>n.getBoundingClientRect().height);
    assert.ok(idleControls<=76,'idle control strip is compact');
    const openingRack=await rackGeometry();
    if(width<500) {
      assert.equal(openingRack.rows,2,'14 tiles fit two mobile rows');
      assert.ok(openingRack.width>=35 && openingRack.width<=36 && openingRack.height>=44 && openingRack.height<=46);
      assert.ok(openingRack.numberSize>=20 && openingRack.colorSize>=10,'numbers and color symbols remain readable');
      assert.ok(openingRack.contentHeight<=104,'rack is lighter than the previous 38x48px version');
    }
    await screenshot('opening');
    await key('sort-number').click();await key('sort-color').click();
    await pick(['red-10-1','red-11-1','red-12-1']);
    assert.equal(await key('return').count(),0);
    const selectedControls=await page.locator('.rk-controls').evaluate(n=>n.getBoundingClientRect().height);
    assert.ok(selectedControls<=156,'selection does not create a loose control panel');
    await screenshot('selected');
    await key('clear').focus();await page.keyboard.press('Enter');
    assert.equal(await page.locator('.rk-selection').count(),0);
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.tileId),'red-10-1');
    await pick(['red-10-1','red-11-1','red-12-1']);await key('new').click();
    const singleGroup=await page.locator('.rk-meld').evaluate(n=>{
      const box=n.getBoundingClientRect(),tile=n.querySelector('.rk-tile').getBoundingClientRect();
      return {height:box.height,tileHeight:tile.height,tileTop:tile.top-box.top,tableHeight:n.parentElement.getBoundingClientRect().height};
    });
    assert.equal(await page.locator('.rk-meld-meta').count(),0,'ordinary group has no reserved toolbar');
    assert.equal(await page.locator('.rk-meld-label').textContent(),'1 · 顺子');
    assert.ok(singleGroup.height<=singleGroup.tileHeight+12 && singleGroup.tileTop<=6,'group height is determined by tiles');
    if(width<500) assert.ok(singleGroup.height<=58 && singleGroup.tableHeight<=80,'one group no longer fills a large panel');
    await screenshot('single-group');
    await pick(['red-12-1'],'.rk-table');
    const returnControls=await page.locator('.rk-controls').evaluate(n=>n.getBoundingClientRect().height);
    assert.ok(returnControls<=270,'history, return and reorder controls remain compact');
    const statusGeometry=await page.locator('.rk-selection .rk-header').evaluate(n=>{
      const r=n.getBoundingClientRect(),text=n.querySelector('.rk-meta').getBoundingClientRect(),cancel=n.querySelector('button').getBoundingClientRect();
      return {height:r.height,separated:text.right<=cancel.left,textHeight:text.height};
    });
    assert.ok(statusGeometry.height<=28 && statusGeometry.textHeight<=17 && statusGeometry.separated,'selection status and cancel share one compact row');
    await screenshot('return-context');await key('return').click();
    assert.equal(await page.locator('.rk-hand .rk-tile').count(),12);
    await key('undo').click();await key('reset').click();
    assert.equal(await page.locator('.rk-hand .rk-tile').count(),14);
    await key('undo').click();await key('undo').click();
    assert.equal(posts.length,0);assert.equal(await page.locator('.rk-hand .rk-tile').count(),14);
    await pick(['red-10-1','red-11-1','red-12-1']);await key('new').click();
    await screenshot('draft');await submit();assert.equal(fixture.room.private_state.hand.length,11);
    await screenshot('submitted');
    // Reload uses persisted state; another participant receives a different private rack.
    const roomId=fixture.room.room_id, own=fixture.room.private_state.hand.map(t=>t.id);
    fixture=backend({action:'state',room_id:roomId});await open();assert.equal(await key('submit').isDisabled(),true);
    viewer='p1';fixture=backend({action:'state',room_id:roomId,viewer});await open();
    const other=await page.locator('.rk-hand .rk-tile').evaluateAll(es=>es.map(e=>e.dataset.tileId));
    assert.equal(other.length,14);assert.ok(other.every(id=>!own.includes(id)));
    assert.equal(fixture.room.private_state.player_id||viewer,viewer);
    await screenshot('other-viewer');
    await seed('ambiguous');
    await pick(['red-9-1','joker-1','joker-2']);await key('new').click();
    await key('kind-0').selectOption('group');assert.equal(await key('submit').isDisabled(),true);
    assert.equal(await page.locator('.rk-meld-label').textContent(),'1 · 同数组');
    await key('kind-0').selectOption('run');assert.equal(await key('submit').isDisabled(),false);
    await pick(['joker-1'],'.rk-table');
    assert.equal(await page.locator('.rk-meld-meta').evaluate(n=>{
      const children=[...n.children].map(e=>e.getBoundingClientRect()),box=n.getBoundingClientRect();
      return children.every((r,i)=>r.left>=box.left && r.right<=box.right+1 && (!i || r.left>=children[i-1].right));
    }),true,'type chooser fits without overlap');
    await screenshot('joker-type-selected');
    // Split a six-tile public run and extend its second half with our own seven.
    await seed('split');const old=fixture.room.board_state.melds.flat();const before=posts.length;
    assert.equal(await page.locator('.rk-target').count(),0,'no target hit areas before selecting');
    const tileTop=await page.locator('.rk-table .rk-tile').first().evaluate(n=>n.getBoundingClientRect().top);
    await pick(['red-4-1'],'.rk-table');
    assert.equal(await page.locator('.rk-table .rk-tile').first().evaluate(n=>n.getBoundingClientRect().top),tileTop,'context controls appear below the tiles');
    await key('clear').click();
    await pick(['red-4-1','red-5-1','red-6-1'],'.rk-table');await key('new').click();
    assert.equal(await page.locator('.rk-meld').count(),2);await key('undo').click();
    assert.equal(await page.locator('.rk-meld').count(),1);
    await pick(['red-4-1','red-5-1','red-6-1'],'.rk-table');await key('new').click();
    await pick(['red-7-1']);
    const selectedIds=await page.locator('.rk-tile.is-selected').evaluateAll(es=>es.map(e=>e.dataset.tileId));
    await tapGroup(0);
    assert.equal(await key('target-0').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('[data-group="0"]').evaluate(n=>n.classList.contains('is-target')),true);
    assert.deepEqual(await page.locator('.rk-tile.is-selected').evaluateAll(es=>es.map(e=>e.dataset.tileId)),selectedIds,'tap on public tiles chooses the group and preserves rack selection');
    await key('target-1').focus();await page.keyboard.press('Enter');
    assert.equal(await key('target-1').getAttribute('aria-pressed'),'true','Enter selects group');
    await key('target-0').focus();await page.keyboard.press('Space');
    assert.equal(await key('target-0').getAttribute('aria-pressed'),'true','Space selects group');
    await tapGroup(1);
    assert.equal(await page.locator('.rk-table button').filter({hasText:/^(目标|已选)$/}).count(),0,'no target text buttons');
    assert.equal(await page.locator('.rk-meld-meta').count(),0,'targets add no tool row');
    await screenshot('whole-group-target');
    await key('move').click();
    assert.equal(posts.length,before,'splits/undo/target edits never post');
    await screenshot('split-draft');await submit();
    assert.deepEqual(fixture.room.board_state.melds,[['red-1-1','red-2-1','red-3-1'],['red-4-1','red-5-1','red-6-1','red-7-1']]);
    assert.ok(old.every(id=>fixture.room.board_state.melds.flat().includes(id)));
    await screenshot('split-submitted');
    // Twenty public melds and a 28-tile rack exercise both scrolling regions.
    await seed('long');
    assert.equal(await page.locator('.rk-meld').count(),20);assert.equal(await page.locator('.rk-hand .rk-tile').count(),28);
    assert.equal(await page.locator('.rk-table').evaluate(n=>n.scrollHeight>n.clientHeight),true);
    const tableGeometry=await page.locator('.rk-table').evaluate(n=>{
      const box=n.getBoundingClientRect(),groups=[...n.querySelectorAll('.rk-meld')].map(e=>e.getBoundingClientRect());
      return {height:box.height,scrollHeight:n.scrollHeight,rows:new Set(groups.map(g=>Math.round(g.top))).size,
        firstRow:groups.filter(g=>Math.abs(g.top-groups[0].top)<1).length,
        visible:groups.filter(g=>g.top>=box.top && g.bottom<=box.bottom).length};
    });
    assert.ok(tableGeometry.firstRow>=2 && tableGeometry.rows<20,'melds pack horizontally and wrap');
    assert.ok(tableGeometry.visible>=(width<500?8:4),'removing title rows increases visible groups');
    assert.ok(tableGeometry.height<=(width<500?286:350));
    const rack28=await rackGeometry();
    // Layout-only projection: check a common 20-tile rack and a nine-tile run,
    // then restore the exact backend view before submitting any moves.
    const fullFixture=structuredClone(fixture);
    fixture.room.private_state.hand=fixture.room.private_state.hand.slice(0,20);
    const melds=fixture.room.board_state.melds;
    fixture.room.board_state.melds=[melds.slice(0,3).flat(),...melds.slice(3)];
    fixture.room.board_state.meld_info=[fixture.room.board_state.meld_info[0],...fixture.room.board_state.meld_info.slice(3)];
    await open();
    const rack20=await rackGeometry();
    if(width<500) {
      assert.ok(rack20.rows<=3 && rack20.contentHeight<=164,'20 tiles fit at most three compact rows');
      assert.ok(await page.locator('[data-group="0"] .rk-tile').evaluateAll(es=>new Set(es.map(e=>Math.round(e.getBoundingClientRect().top))).size>1),'long run wraps within its group');
    }
    assert.deepEqual(await page.locator('[data-group="0"] .rk-tile').evaluateAll(es=>es.map(e=>e.dataset.tileId)),fixture.room.board_state.melds[0],'wrapped run keeps tile order');
    await screenshot('dense-20-long-run');
    // A larger projected rack exercises independent scrolling and selected-tile
    // visibility across rerenders; this scene does not submit to the backend.
    const spare=Object.values(fixture.room.board_state.table_tiles).slice(0,14);
    const spareIds=new Set(spare.map(t=>t.id));
    fixture.room.board_state.melds=fixture.room.board_state.melds.map(m=>m.filter(id=>!spareIds.has(id)));
    fixture.room.private_state.hand=fullFixture.room.private_state.hand.concat(spare);
    await open();
    if(width<500) {
      const rack=page.locator('.rk-hand');
      assert.equal(await rack.evaluate(n=>n.scrollHeight>n.clientHeight),true);
      await rack.evaluate(n=>{n.scrollTop=n.scrollHeight;n.dispatchEvent(new Event('scroll'));});
      const scrollBefore=await rack.evaluate(n=>n.scrollTop);
      await rack.locator('.rk-tile').last().click();
      assert.equal(await rack.evaluate(n=>n.scrollTop),scrollBefore,'selection keeps the rack scroll position');
      await screenshot('large-rack-selected');
    }
    fixture=fullFixture;await open();
    await page.locator('.rk-table').evaluate(n=>n.scrollTop=n.scrollHeight);
    await screenshot('long');
    const geometry=await page.evaluate(()=>{
      const props=selector=>{const e=document.querySelector(selector),s=getComputedStyle(e),r=e.getBoundingClientRect();return {x:r.x,right:r.right,width:r.width,height:r.height,font:s.fontFamily,fontSize:s.fontSize,lineHeight:s.lineHeight,border:s.border,shadow:s.boxShadow,padding:s.padding,gap:s.gap};};
      return {board:props('.rk-game'),tile:props('.rk-tile'),button:props('[data-rk-focus="submit"]'),sort:props('.rk-sort .rk-button'),suggest:props('[data-rk-focus="suggest"]'),chat:props('#chatInput'),refresh:props('#refreshButton')};
    });
    assert.ok(geometry.button.height>=36 && geometry.button.height<=38,'primary actions are compact but readable');
    assert.ok(geometry.sort.height>=30 && geometry.sort.height<=32,'sort buttons no longer inherit tall action sizing');
    assert.ok(await page.locator('.rk-sort').evaluate(n=>n.getBoundingClientRect().height<=34),'segmented sort including border is at most 34px');
    assert.ok(parseFloat(geometry.suggest.fontSize) < 13);
    measurements.push({width,count,idleControls,selectedControls,returnControls,statusGeometry,openingRack,singleGroup,rack20,rack28,tableGeometry,...geometry});
    await page.locator('.rk-hand .rk-tile').first().focus();await page.keyboard.press('Space');
    assert.equal(await page.locator('.rk-hand .is-selected').count(),1);
    await key('new').click();assert.equal(await key('submit').isDisabled(),true,'unfinished draft cannot submit');
    const pool=fixture.room.board_state.pool_count, rev=fixture.room.revision;
    await key('draw').click();await page.waitForFunction(()=>document.querySelector('.rk-draft-status')?.textContent.includes('等待'));
    assert.equal(posts.at(-1).move.action,'draw');assert.equal(fixture.room.revision,rev+1);
    assert.equal(fixture.room.private_state.hand.length,29);assert.equal(fixture.room.board_state.pool_count,pool-1);
    assert.equal(await page.locator('.rk-meld').count(),20,'draw discards the local unfinished draft');
    assert.equal(await page.locator('.rk-hand .rk-tile:enabled').count(),0,'newly drawn tile cannot be played this turn');
    await screenshot('drawn');
    await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(()=>document.documentElement.style.fontSize='200%');
    await screenshot('enlarged');
    assert.deepEqual(errors,[]);await page.close();await browser.close();browser=null;
    console.log(`PASS: ${width}px / ${count} players; 14-tile rows=${openingRack.rows}, 20-tile rows=${rack20.rows}, groups per row=${tableGeometry.firstRow}`);
    fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measurements,null,2));
  }
  fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measurements,null,2));
  console.log(JSON.stringify({ok:true,widths,players:[2,4],checks:measurements.length,screenshots:out,actions:['sort','select','new','split','target','undo','submit','draw'],persistedReload:true,privateViewers:2}));
 } finally { if(browser) await browser.close(); fs.rmSync(tmp,{recursive:true,force:true}); }
})().catch(e=>{console.error(e);process.exitCode=1});
