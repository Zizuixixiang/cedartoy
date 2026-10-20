/* Real projected demo + renderer DOM; --browser also checks full Duel page.
 * All HTTP is intercepted. No production account, room or network mutations.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'vendor/duel/app/static');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'rk-terminal-'));
const viewPath = path.join(temp, 'view.json');
const generated = spawnSync(process.env.DUEL_TEST_PYTHON || path.join(root, 'vendor/duel/.venv/bin/python'),
  [path.join(root, 'scripts/create_rummikub_review_demo.py'), '--export-view', viewPath], {encoding:'utf8'});
assert.equal(generated.status, 0, generated.stderr);
const fixture = JSON.parse(fs.readFileSync(viewPath));
const dom = new JSDOM('<div id="board"></div><div id="controls"></div>', {runScripts:'outside-only'});
const w = dom.window, doc = w.document, renderers = {};
w.DuelGameUI = {register:(name, renderer)=>renderers[name]=renderer};
for(const name of ['rummikub','guandan','liars_dice'])
  w.eval(fs.readFileSync(path.join(staticRoot, `games/${name}.js`), 'utf8'));
function context(room, terminal=true) {
  const ctx = {
    board:doc.getElementById('board'), controls:doc.getElementById('controls'),
    room, state:room.board_state, participants:room.participants, privateState:room.private_state,
    viewer:room.viewer, isTerminal:terminal, canMove:false, uiState:{}, legalActions:[],
    helpers:{setBoardLayout(){}, canMove:()=>false, rerender(){}, submitMove(){throw Error('read-only');},
      renderParticipantAvatar(target,p){target.textContent=p.display_name[0];}},
  };
  ctx.board.replaceChildren();ctx.controls.replaceChildren();
  return ctx;
}
function render(room, terminal=true) {
  const ctx=context(room,terminal);
  renderers.rummikub.renderBoard(ctx);renderers.rummikub.renderControls(ctx);
  return ctx;
}
render(fixture.room);
assert.equal(doc.querySelector('.rk-terminal-review').tagName,'SECTION');
assert.equal(doc.querySelectorAll('.rk-terminal-row').length,4);
assert.deepEqual([...doc.querySelectorAll('.rk-terminal-row')].map(n=>n.dataset.playerId),
  fixture.room.participants.map(p=>p.player_id));
assert.equal(doc.querySelectorAll('.rk-terminal-review button, .rk-terminal-review select').length,0);
assert.equal(doc.querySelector('.rk-rack'),null,'no duplicate own rack or irrelevant sort controls at terminal');
assert.equal(doc.querySelectorAll('.rk-scores').length,1,'retain one existing score strip');
assert.match(doc.querySelector('.rk-terminal-review').textContent,/剩余 58 点/);
render(fixture.room,false);
assert.equal(doc.querySelector('.rk-terminal-review'),null,'isTerminal is mandatory even if payload has terminal hands');
const noHands=structuredClone(fixture.room);delete noHands.board_state.terminal_hands;
render(noHands);assert.equal(doc.querySelector('.rk-terminal-review'),null);
const edge=structuredClone(fixture.room);
edge.board_state.terminal_hands['1']=[];
edge.board_state.terminal_hands[edge.participants[1].player_id]=[{id:'joker-2',color:'joker',number:0}];
edge.board_state.result={reason:'resignation',winning_player_ids:['1'],tile_scores:{}};
edge.board_state.resigned_player_ids=[edge.participants[1].player_id];
render(edge);
assert.match(doc.querySelector('.rk-terminal-review').textContent,/0 张 · 剩余 0 点.*无剩余手牌/s);
assert.match(doc.querySelector('.rk-terminal-review').textContent,/弃权.*剩余 30 点/s);
assert.equal(doc.querySelector('.rk-terminal-review .rk-joker .rk-number').textContent,'★');
assert.equal(doc.querySelector('.rk-scores'),null,'no invented resignation score');
edge.board_state.result={draw:true,reason:'blocked',winning_player_ids:['1',edge.participants[1].player_id],tile_scores:{}};
render(edge);assert.match(doc.querySelector('.rk-terminal-name').textContent,/并列获胜/);
edge.board_state.result={draw:true,winning_player_ids:[],tile_scores:{}};
render(edge);assert.match(doc.querySelector('.rk-terminal-name').textContent,/流局/);
for(const name of ['guandan','liars_dice']) {
  w.renderLiarsDice=()=>{};
  const room=structuredClone(fixture.room);
  room.board_state={phase:'finished',flow:{phase:'finished'},hand_counts:{},
    terminal_hands:{'1':[]},terminal_dice:{'1':[1,6]}};
  room.private_state={hand:[]};
  const ctx=context(room);renderers[name].renderBoard(ctx);
  const review=doc.querySelector('details');
  assert.ok(review?.open,`${name} initially open`);
  review.open=false;assert.equal(review.open,false);
  delete room.board_state.terminal_hands;delete room.board_state.terminal_dice;
  renderers[name].renderBoard(context(room,false));
  assert.equal(doc.querySelector('details'),null,`${name} live has no hidden faces`);
}
console.log('PASS: DOM terminal gate, seats, points, scores once, zero rack, joker, draw/tie/resignation, read-only, details default/collapse');

async function browserCheck() {
  const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const out=process.env.RUMMIKUB_REVIEW_ARTIFACTS || path.join(root,'artifacts/rummikub-terminal-review');
  fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const measures=[];
  try {
    for(const width of [360,430,1280]) {
      const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<500});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      let payload=structuredClone(fixture);
      await page.route('**/*',route=>{
        const url=new URL(route.request().url());
        if(url.hostname!=='rummikub-review.test')return route.abort();
        const pathname=url.pathname;
        if(pathname==='/' || pathname.startsWith('/static/')) {
          const rel=pathname==='/'?'index.html':pathname.slice(8), file=path.join(staticRoot,rel);
          if(!file.startsWith(staticRoot+'/') || !fs.existsSync(file))return route.fulfill({status:404,body:''});
          return route.fulfill({body:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
        }
        let data={ok:true,notifications:[],rooms:[],timeline:[]};
        if(pathname==='/api/whoami')data={...data,bound:true,human_player_id:'1',human_name:'南杉',machines:[],
          games:[{game_type:'rummikub',display_name:'拉密',category:'tabletop',allowed_player_counts:[2,3,4],supports_stakes:false}],wallet:{balance:0}};
        else if(pathname===`/api/rooms/${fixture.room.room_id}`) data=payload;
        return route.fulfill({json:data});
      });
      const open=async()=>{
        await page.goto(`http://rummikub-review.test/?room=${fixture.room.room_id}`);
        await page.locator('.rk-terminal-review').waitFor();
        // Exercise the existing modal's own close function; no modal edits.
        await page.evaluate(()=>{if(typeof closeResultModal==='function')closeResultModal();
          if(typeof hideWaitModeModal==='function')hideWaitModeModal();});
      };
      await open();
      assert.equal(await page.locator('.rk-terminal-row').count(),4);
      assert.equal(await page.locator('.rk-scores').count(),1);
      const geometry=await page.evaluate(()=>{
        const review=document.querySelector('.rk-terminal-review'), name=review.querySelector('.rk-terminal-name');
        const rows=[...review.querySelectorAll('.rk-terminal-tiles')];
        return {overflow:document.documentElement.scrollWidth>innerWidth,
          title:getComputedStyle(review.querySelector('strong')).fontSize,name:getComputedStyle(name).fontSize,
          number:getComputedStyle(review.querySelector('.rk-number')).fontSize,
          rows:rows.map(e=>({width:e.clientWidth,scroll:e.scrollWidth,overflow:getComputedStyle(e).overflowX}))};
      });
      assert.equal(geometry.overflow,false);
      assert.equal(geometry.title,'14px');assert.equal(geometry.name,'13px');
      if(width===360)assert.ok(geometry.rows.some(r=>r.scroll>r.width),'mobile rack scrolls horizontally');
      const tray=page.locator('.rk-terminal-tiles').last();
      await tray.focus();await page.keyboard.press('ArrowRight');
      await page.screenshot({path:path.join(out,`finished-${width}.png`),fullPage:true});
      payload.room.status='archived';await open();
      assert.equal(await page.locator('.rk-terminal-row').count(),4,'historical reopen');
      payload.room=structuredClone(edge);
      payload.room.participants[0].display_name='很长的演示玩家名字'.repeat(6);
      await open();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(out,`zero-joker-draw-${width}.png`),fullPage:true});
      await page.emulateMedia({reducedMotion:'reduce'});
      await page.locator('.rk-game').evaluate(e=>e.style.zoom='2');
      assert.equal(await page.locator('.rk-terminal-row').count(),4);
      assert.deepEqual(errors,[]);
      measures.push({width,...geometry});
      await page.close();
    }
    // Native details semantics: user pointer and keyboard can collapse/reopen.
    const page=await browser.newPage();
    await page.setContent('<div id="board"></div>');
    await page.evaluate(()=>{window.renderers={};window.DuelGameUI={register:(n,r)=>window.renderers[n]=r};window.renderLiarsDice=()=>{};});
    for(const name of ['liars_dice','guandan']) {
      await page.addScriptTag({path:path.join(staticRoot,`games/${name}.js`)});
      await page.evaluate(name=>{
        const board=document.querySelector('#board');board.replaceChildren();
        window.renderers[name].renderBoard({board,state:{phase:'finished',terminal_hands:{'1':[]},terminal_dice:{'1':[1,6]}},
          participants:[{player_id:'1',display_name:'南杉',seat_index:0}],privateState:{hand:[]},
          viewer:{player_id:'1',seat:0},room:{status:'finished'},uiState:{},isTerminal:true,canMove:false,legalActions:[],
          helpers:{setBoardLayout(){},renderParticipantAvatar(){},canMove:()=>false}});
      },name);
      const details=page.locator('details');
      assert.equal(await details.evaluate(e=>e.open),true);
      await details.locator('summary').click();assert.equal(await details.evaluate(e=>e.open),false);
      await details.locator('summary').focus();await page.keyboard.press('Enter');
      assert.equal(await details.evaluate(e=>e.open),true);
    }
    fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measures,null,2));
    console.log(`PASS: Chromium 360/430/1280, full page reentry, horizontal scroll, native details; screenshots ${out}`);
  } finally {await browser.close();}
}
(async()=>{
  try {if(process.argv.includes('--browser'))await browserCheck();}
  finally {dom.window.close();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
