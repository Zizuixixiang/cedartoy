// Local renderer, disposable SQLite fixtures and intercepted requests only.
// node scripts/check_mahjong_interaction.cjs --dom
// PLAYWRIGHT_MODULE=... PLAYWRIGHT_BROWSERS_PATH=... node scripts/check_mahjong_interaction.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..'), assets = path.join(root,'vendor/duel/app/static');
const app = fs.readFileSync(path.join(assets,'app.js'),'utf8')
  .replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')+'\nwindow.run=source=>eval(source);';
const result = spawnSync(path.join(root,'vendor/duel/.venv/bin/python'),['-c',`
import json,tempfile
from pathlib import Path
from app import database,invites,framework
from app.games import game_catalog
with tempfile.TemporaryDirectory(prefix='mj-interaction-') as tmp:
 database.DB_PATH=Path(tmp)/'fixture.db';database.init_db();out={}
 for key,count in [('ka_wu_xing',3),('mahjong',4)]:
  r=invites.create_invite(key,'human','human:1',target_player_count=count,display_name='南杉')
  for i in range(1,count):r=invites.join_invite(r['invite_code'],'human',f'human:{i+1}',display_name=f'牌友{i}')
  r=invites.start_invite(r['room_id'],'human','human:1')
  out[key]=framework.project_room_for_viewer(r,r['current_player_id'])
 print(json.dumps(dict(fixtures=out,catalog=game_catalog())))
`],{cwd:path.join(root,'vendor/duel'),encoding:'utf8'});
assert.equal(result.status,0,result.stderr);
const {fixtures,catalog}=JSON.parse(result.stdout);
let revision=100;
function fixture(game, phase='discard', long=false) {
  const f=structuredClone(fixtures[game]); f.revision=revision++;
  const viewer=f.viewer.player_id;
  f.participants.forEach((p,i)=>p.display_name=long ? `${i?'Codex_杉星_0828_':'南杉'}长名字测试`.repeat(5) : (p.player_id===viewer?'南杉':`牌友${i}`));
  const tiles=f.private_state.hand;
  f.private_state.legal_actions=tiles.map(t=>({kind:'discard',action_id:`discard:${t.id}`,label:`打${t.label}`}));
  if(phase==='response') f.private_state.legal_actions=[{kind:'peng',action_id:'peng:B1',label:long?'碰1筒 · 长响应说明用于换行检查':'碰1筒'},{kind:'pass',action_id:'pass:1',label:'过'}];
  if(phase==='waiting') {
    const other=f.participants.find(p=>p.player_id!==viewer);
    f.current_player_id=other.player_id; f.current_actor=other; f.current_player=other;
    f.board_state.turn_player_id=other.player_id;
    f.private_state.hand.pop(); f.private_state.legal_actions=[];
  }
  if(phase==='terminal') {
    f.status='finished'; f.winner_player_id=viewer; f.private_state.legal_actions=[];
    const outcome={winner_player_id:viewer,win_type:'self_draw',multiplier:long?128:2,total_fan:12,
      fans:game==='ka_wu_xing'?[{name:'平胡',multiplier:1},{name:'亮倒',multiplier:2}]:[{name:'清一色',points:24,count:1,fan:24}],
      winning_tile:tiles[tiles.length-1],score_units:Object.fromEntries(f.participants.map(p=>[p.player_id,p.player_id===viewer?32:-16])),
      score_breakdown:{hu:Object.fromEntries(f.participants.map(p=>[p.player_id,p.player_id===viewer?32:-16]))}};
    Object.assign(f.board_state,{phase:'finished',game_result:outcome,terminal_hands:Object.fromEntries(f.participants.map(p=>[p.player_id,tiles]))});
    f.result=outcome;
  }
  return f;
}
function show(w,f,reset=true) {
  w.fixture=f;w.catalog=catalog;
  w.run(`identity={human_player_id:fixture.viewer.player_id,human_name:'南杉',machines:[],games:catalog};startRoomPolling=()=>{};${reset?'room=null;':''}renderGame(fixture);`);
}
async function domChecks() {
  const {JSDOM}=await import('jsdom');
  const dom=new JSDOM(fs.readFileSync(path.join(assets,'index.html'),'utf8'),{url:'http://duel.test/',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document;
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  for(const file of ['game_ui_registry.js','games/mahjong.js'])w.eval(fs.readFileSync(path.join(assets,file),'utf8'));
  w.eval(app);
  try {for(const game of Object.keys(fixtures)) {
    show(w,fixture(game));
    const table=d.querySelector('.mahjong-table'),hand=d.querySelector('.mahjong-own-hand-scroll'),tile=hand.querySelector('button');
    tile.focus(); tile.click();
    assert.equal(d.querySelector('.mahjong-table'),table);assert.equal(d.querySelector('.mahjong-own-hand-scroll'),hand);
    assert.equal(d.activeElement,tile);assert.equal(tile.getAttribute('aria-pressed'),'true');
    tile.click();assert.equal(tile.getAttribute('aria-pressed'),'false');
    show(w,fixture(game,'response'));
    d.querySelector('.kind-peng').click();
    assert.equal(d.querySelectorAll('.mahjong-controls button').length,2);
    assert.equal(d.querySelectorAll('.mahjong-controls .mahjong-action').length,0);
    assert.equal(d.activeElement.className,'mahjong-confirm-cancel');
    d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.ok(d.activeElement.classList.contains('kind-peng'));
    d.querySelector('.kind-peng').click();
    show(w,fixture(game,'waiting'),false);
    assert.equal(d.querySelector('.mahjong-confirmation'),null,'stale response cleared by revision');
    const readonly=fixture(game);Object.assign(readonly,{read_only:true,demo:true,spectating:true});show(w,readonly);
    assert.equal(d.querySelectorAll('.mahjong-controls button:not(:disabled)').length,0);
    assert.equal(d.querySelectorAll('.mahjong-own-hand-scroll button:not(:disabled)').length,0);
    show(w,fixture(game,'terminal',true));
    assert.equal(d.querySelectorAll('.mahjong-terminal-row').length,game==='ka_wu_xing'?3:4);
    assert.equal(d.querySelectorAll('.winning-tile').length,1);
    if(game==='ka_wu_xing')assert.equal(d.querySelectorAll('.mahjong-terminal-score').length,3);
    assert.equal(d.querySelector('#mahjongTerminalTitle').tagName,'H3');
  }
  console.log('PASS DOM: retained selection/focus, one response bar, Escape, stale revision, read-only, terminal scores (both variants)');
  } finally {dom.window.close();}
}
async function browserChecks() {
  const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
  const output=process.env.DUEL_UI_SCREENSHOTS||fs.mkdtempSync(path.join(os.tmpdir(),'mj-actions-visual-'));
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
  try {for(const width of (process.env.DUEL_UI_WIDTHS||'360,390,430,1280,1700').split(',').map(Number)) {
    const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<900,isMobile:width<900});
    const cdp=await page.context().newCDPSession(page),errors=[],requests=[];
    let nextResponse=null,failNext=false;
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
      const u=new URL(route.request().url());if(u.host!=='duel.test')return route.abort();
      if(u.pathname.endsWith('/move')) {
        requests.push(route.request().postDataJSON());
        if(failNext){failNext=false;return route.fulfill({status:409,json:{detail:'测试：请重试'}});}
        assert.ok(nextResponse,'unexpected move');return route.fulfill({json:{room:nextResponse,timeline:[],message:'已行动'}});
      }
      if(u.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true}});
      const rel=u.pathname==='/'?'index.html':u.pathname.replace(/^\/static\//,''),file=path.join(assets,rel);
      if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({body:rel==='app.js'?app:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':rel.endsWith('.svg')?'image/svg+xml':'text/html'});
    });
    await page.goto('http://duel.test/');
    if(process.env.DUEL_UI_CJK_FONT) {
      const font=fs.readFileSync(process.env.DUEL_UI_CJK_FONT).toString('base64');
      await page.addStyleTag({content:`@font-face{font-family:"Microsoft YaHei";src:url(data:font/otf;base64,${font})}`});
      await page.evaluate(()=>document.fonts.load('15px "Microsoft YaHei"'));
      if(width<900)await page.addStyleTag({content:'.mahjong-seat-name{font-family:system-ui,"Microsoft YaHei",sans-serif !important}'});
    }
    await page.evaluate(()=>DuelGameUI.load('mahjong'));
    const render=async(f,reset=true)=>{
      if(width<900)await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
      await page.evaluate(({f,catalog,reset})=>{window.fixture=f;window.catalog=catalog;run(`identity={human_player_id:fixture.viewer.player_id,human_name:'南杉',machines:[],games:catalog};startRoomPolling=()=>{};${reset?'room=null;':''}renderGame(fixture);`);},{f,catalog,reset});
      await page.locator('.mahjong-table').waitFor();
    };
    const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const measure=()=>page.evaluate(()=>{
      const r=s=>document.querySelector(s).getBoundingClientRect().toJSON();
      const h=document.querySelector('.mahjong-own-hand-scroll');
      return {y:scrollY,table:r('.mahjong-table'),controls:r('.mahjong-controls'),scroll:document.documentElement.scrollWidth,handLeft:h?.scrollLeft,handMax:h ? Math.max(0,h.scrollWidth-h.clientWidth) : 0};
    });
    const stable=(a,b,label)=>{
      assert.ok(Math.abs(a.y-b.y)<=1,`${label}: scroll ${a.y} -> ${b.y}`);
      assert.ok(Math.abs(a.table.y-b.table.y)<=1,`${label}: table ${a.table.y} -> ${b.table.y}`);
      assert.ok(Math.abs(a.controls.height-b.controls.height)<=1,`${label}: controls ${a.controls.height} -> ${b.controls.height}`);
    };
    for(const game of Object.keys(fixtures)) {
      const live=fixture(game);await render(live);
      await page.locator('.mahjong-controls').scrollIntoViewIfNeeded();await settle();
      await page.evaluate(()=>{window.originalTable=document.querySelector('.mahjong-table');window.originalHand=document.querySelector('.mahjong-own-hand-scroll');originalHand.scrollLeft=30;});
      const initial=await measure();
      if(width>=900) {
        const sizes=await page.evaluate(()=>Object.fromEntries(['mahjong-center-status','mahjong-own-hand-head','mahjong-discard-label','mahjong-seat-name'].map(cls=>[cls,parseFloat(getComputedStyle(document.querySelector('.'+cls)).fontSize)])));
        assert.ok(sizes['mahjong-center-status']>=16&&sizes['mahjong-own-hand-head']>=16&&sizes['mahjong-discard-label']>=15&&sizes['mahjong-seat-name']>=15,JSON.stringify(sizes));
      }
      // Pick an already visible tile: no Playwright auto-scroll masks app movement.
      await page.evaluate(()=>{const h=document.querySelector('.mahjong-own-hand-scroll');const t=[...h.children].find(t=>t.offsetLeft>=h.scrollLeft);t.focus({preventScroll:true});t.click();});
      await settle();const selected=await measure();stable(initial,selected,'select');
      assert.ok(await page.evaluate(()=>originalTable===document.querySelector('.mahjong-table')&&originalHand===document.querySelector('.mahjong-own-hand-scroll')));
      assert.equal(await page.locator('.mahjong-confirmation').count(),0);
      const action=await page.locator('.mahjong-discard-button').getAttribute('data-action-id');
      // The actual submitMove/request/renderGame path handles a new revision.
      nextResponse=fixture(game,'waiting');nextResponse.room_id=live.room_id;
      const beforeSubmit=await measure(),count=requests.length;
      await page.locator('.mahjong-discard-button').click();await page.locator('.state-waiting').waitFor();await settle();
      const submitted=await measure();stable(beforeSubmit,submitted,'discard revision');
      assert.equal(requests.length,count+1);assert.equal(requests.at(-1).move.action_id,action);
      assert.ok(await page.evaluate(()=>originalTable===document.querySelector('.mahjong-table')));
      assert.ok(Math.abs(submitted.handLeft-Math.min(selected.handLeft,submitted.handMax))<=1,`hand scroll preserved (or clamped to shorter hand): ${JSON.stringify({selected,submitted})}`);
      const newTurn=fixture(game);newTurn.room_id=live.room_id;
      await render(newTurn,false);await settle();stable(submitted,await measure(),'next turn');
      if(width===1700) await page.screenshot({path:path.join(output,`${game}-${width}-table.png`),fullPage:true});
      for(const cancelMethod of ['button','escape']) {
        await render(fixture(game,'response'));await page.locator('.mahjong-controls').scrollIntoViewIfNeeded();await settle();
        const before=await measure();await page.locator('.kind-peng').click();await settle();
        stable(before,await measure(),'response select');
        assert.equal(await page.locator('.mahjong-controls button').count(),2);
        assert.equal(await page.evaluate(()=>document.activeElement.className),'mahjong-confirm-cancel');
        if(cancelMethod==='button')await page.locator('.mahjong-confirm-cancel').click();else await page.keyboard.press('Escape');
        await settle();stable(before,await measure(),'response cancel');
        assert.ok(await page.locator('.kind-peng').evaluate(e=>e===document.activeElement));
      }
      await page.locator('.kind-peng').click();
      await page.locator('.mahjong-controls').screenshot({path:path.join(output,`${game}-${width}-confirmation.png`)});
      nextResponse=fixture(game,'waiting');const responseBefore=await measure();
      failNext=true;await page.locator('.mahjong-confirm-submit').click();
      await page.waitForFunction(()=>!document.querySelector('.mahjong-confirm-submit').disabled);
      assert.equal(await page.locator('.mahjong-confirmation').count(),1,'failed submit remains retryable');
      await settle();stable(responseBefore,await measure(),'failed response');
      assert.ok(await page.locator('#toast.show').isVisible(),'failed move has accessible feedback');
      const responseCount=requests.length;
      assert.ok(await page.locator('.mahjong-confirm-submit').evaluate(e=>e===document.activeElement),'retry focus restored');
      await page.keyboard.press('Enter');
      await page.locator('.state-waiting').waitFor();await settle();
      assert.equal(requests.length,responseCount+1);assert.equal(requests.at(-1).move.action_id,'peng:B1');
      stable(responseBefore,await measure(),'response confirm');
      await page.locator('#toast.show').waitFor({state:'hidden'});
      await render(fixture(game,'response',true));await page.locator('.kind-peng').click();
      assert.ok((await measure()).scroll<=width,'long response overflows viewport');
      for(const selector of ['.mahjong-confirm-cancel','.mahjong-confirm-submit']) {
        const box=await page.locator(selector).boundingBox();assert.ok(box.width>=44&&box.height>=44&&box.x>=0&&box.x+box.width<=width);
      }
      const demo=fixture(game);Object.assign(demo,{demo:true,read_only:true,spectating:true});await render(demo);
      assert.equal(await page.locator('.mahjong-controls button:not(:disabled)').count(),0);
      assert.equal(await page.locator('.mahjong-own-hand-scroll button:not(:disabled)').count(),0);
      for(const long of [false,true]) {
        await render(fixture(game,'terminal',long));await settle();
        const fonts=await page.evaluate(()=>{
          const f=s=>{const c=getComputedStyle(document.querySelector(s));return {size:parseFloat(c.fontSize),weight:+c.fontWeight}};
          return {title:f('.mahjong-terminal-title'),summary:f('.mahjong-terminal-summary'),body:f('.mahjong-terminal-review'),name:f('.mahjong-terminal-name'),note:f('.mahjong-terminal-note'),score:f('.mahjong-terminal-score'),scroll:document.documentElement.scrollWidth};
        });
        assert.ok(fonts.title.size>=fonts.body.size+5 && fonts.title.weight>=700);
        assert.ok(fonts.summary.size>fonts.body.size && fonts.summary.weight>=700);
        assert.ok(fonts.name.size>fonts.body.size && fonts.name.weight>=700);
        assert.ok(fonts.score.size>fonts.name.size && fonts.score.weight>=700);
        assert.ok(fonts.note.size<fonts.body.size);assert.ok(fonts.scroll<=width,JSON.stringify(fonts));
        if(!long||width===360) {
          await page.locator('#mahjongTerminalReview').screenshot({path:path.join(output,`${game}-${width}-review${long?'-long':''}.png`)});
        }
      }
      console.log(JSON.stringify({game,width,controlHeight:selected.controls.height,selectDelta:selected.y-initial.y,submitDelta:submitted.y-beforeSubmit.y,status:'PASS'}));
    }
    assert.deepEqual(errors,[]);await page.close();
  }} finally {await browser.close();if(!process.env.DUEL_UI_SCREENSHOTS)fs.rmSync(output,{recursive:true,force:true});}
}
(process.argv.includes('--dom')?domChecks():browserChecks()).catch(e=>{console.error(e);process.exitCode=1});
