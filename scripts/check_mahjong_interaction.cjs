// Local renderer, disposable SQLite fixtures and intercepted requests only.
// node scripts/check_mahjong_interaction.cjs --dom
// Add --hands-only for remaining terminal hands, privacy and meld geometry.
// Add --review-only to focus on terminal typography/header and shared chat layout.
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
    for(const long of [false,true]) {
      const terminal=fixture(game,'terminal',long);
      terminal.participants.forEach((p,i)=>terminal.board_state.terminal_hands[p.player_id]=terminal.board_state.terminal_hands[p.player_id].slice(-(10+i)));
      show(w,terminal);
      const rows=[...d.querySelectorAll('.mahjong-terminal-row')];
      assert.equal(rows.length,game==='ka_wu_xing'?3:4);
      assert.equal(d.querySelectorAll('.winning-tile').length,1);
      assert.equal(d.querySelectorAll('.mahjong-terminal-score').length,rows.length);
      for(const row of rows) {
        const player=terminal.participants.find(p=>p.player_id===row.dataset.playerId);
        const tiles=terminal.board_state.terminal_hands[player.player_id];
        const count=row.querySelector('.mahjong-terminal-player .mahjong-terminal-hand-count');
        assert.equal(count.textContent,`手牌${tiles.length}张`);
        assert.equal((row.textContent.match(/手牌\d+张/g)||[]).length,1);
        assert.equal(row.querySelector('.mahjong-terminal-name').textContent,player.display_name);
        assert.equal(row.querySelector('.mahjong-terminal-hand').children.length,tiles.length);
        assert.equal(row.querySelector('.mahjong-terminal-score').textContent,player.player_id===terminal.viewer.player_id?'+32':'-16');
      }
      assert.equal(d.querySelector('#mahjongTerminalTitle').tagName,'H3');
    }
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
    let nextResponse=null,chatResponse=null,failNext=false;
    const messages=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
      const u=new URL(route.request().url());if(u.host!=='duel.test')return route.abort();
      if(u.pathname.endsWith('/messages')) {
        messages.push(route.request().postDataJSON());
        assert.ok(chatResponse,'unexpected message');return route.fulfill({json:chatResponse});
      }
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
      if(process.argv.includes('--hands-only')) {
        for(const terminal of [false,true]) {
          const f=fixture(game,terminal?'terminal':'discard');
          const hand=f.private_state.hand.slice(-10);
          f.private_state.hand=hand;
          const meld={kind:'peng',tiles:Array.from({length:3},(_,i)=>({id:`peng-${i}`,code:'B1',label:'1筒',suit:'B',rank:1}))};
          f.private_state.own_melds=[meld];
          f.board_state.melds=Object.fromEntries(f.participants.map(p=>[p.player_id,[meld]]));
          f.board_state.hand_counts=Object.fromEntries(f.participants.map(p=>[p.player_id,10]));
          // Public terminal tiles must never be consulted in a live room.
          f.board_state.terminal_hands=Object.fromEntries(f.participants.map(p=>[p.player_id,p.player_id===f.viewer.player_id?hand:hand.map((t,i)=>({...t,id:`secret-${p.player_id}-${i}`}))]));
          f.board_state.discards=Object.fromEntries(f.participants.map(p=>[p.player_id,hand.map((t,i)=>({...t,id:`discard-${p.player_id}-${i}`}))]));
          for(const reentry of [false,true]) {
            await render(f);await settle();
            const geometry=await page.evaluate(()=>{
              const rect=s=>{const e=document.querySelector(s),r=e.getBoundingClientRect();return {w:r.width,h:r.height,font:parseFloat(getComputedStyle(e.querySelector('.mahjong-tile-face')).fontSize)}};
              const table=document.querySelector('.mahjong-table').getBoundingClientRect();
              const review=document.querySelector('.mahjong-terminal-review')?.getBoundingClientRect();
              const center=document.querySelector('.mahjong-center').getBoundingClientRect();
              const sides=[...document.querySelectorAll('.position-left,.position-right')].map(e=>{
                const frame=e.getBoundingClientRect();
                return {bottom:frame.bottom,contained:[...e.querySelectorAll('.mahjong-meld .mahjong-tile')].every(t=>{const r=t.getBoundingClientRect();return r.left>=frame.left&&r.right<=frame.right&&r.bottom<=frame.bottom;})};
              });
              return {centerBottom:center.bottom,sides,reviewMeld:review?rect('.mahjong-terminal-melds .mahjong-tile'):null,left:rect('.position-left .mahjong-meld .mahjong-tile'),right:rect('.position-right .mahjong-meld .mahjong-tile'),bottom:rect('.position-bottom .mahjong-meld .mahjong-tile'),scroll:document.documentElement.scrollWidth,tableBottom:table.bottom,reviewTop:review?.top};
            });
            console.log(JSON.stringify({game,width,terminal,reentry,geometry}));
            for(const side of ['left','right'])for(const dim of ['w','h','font'])assert.ok(geometry[side][dim]<=geometry.bottom[dim]+0.1,`${side} meld ${dim} exceeds bottom`);
            assert.ok(geometry.scroll<=width);
            assert.ok(geometry.sides.every(s=>s.contained),'melds must stay inside side frames');
            if(width>=900)assert.ok(geometry.sides.every(s=>Math.abs(s.bottom-geometry.centerBottom)<=1),'side frames still align with center');
            if(terminal)for(const side of ['left','right'])for(const dim of ['w','h','font'])assert.ok(geometry[side][dim]<=geometry.reviewMeld[dim]+0.1,`${side} exceeds review meld ${dim}`);
            assert.equal(await page.locator('.position-bottom .mahjong-meld .mahjong-tile').count(),3);
            const own=page.locator('.mahjong-own-hand-scroll .mahjong-tile');
            assert.equal(await own.count(),10);
            assert.deepEqual(await own.evaluateAll(es=>es.map(e=>e.dataset.cardId)),hand.map(t=>String(t.id)));
            if(terminal) {
              assert.ok(geometry.reviewTop>geometry.tableBottom);
              assert.equal(await page.locator('.mahjong-own-hand-scroll button:not(:disabled)').count(),0);
              assert.equal(await page.locator('.mahjong-controls button:not(:disabled)').count(),0);
              await own.first().evaluate(e=>e.click());
              assert.equal(await page.locator('.mahjong-own-hand-scroll .selected').count(),0);
              assert.equal(await page.locator('.mahjong-terminal-row').count(),f.participants.length);
              assert.ok(await page.locator('.mahjong-own-hand-scroll').evaluate(e=>e.scrollWidth<=e.clientWidth+1),'all ten remaining tiles fit');
              if(!reentry) {
                // Tall locator screenshots resize the viewport and reset touch
                // emulation in Chromium. Keep the phone media queries active.
                if(width<900) {
                  const height=await page.evaluate(()=>document.documentElement.scrollHeight);
                  await page.setViewportSize({width,height});
                  await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
                  await page.evaluate(()=>scrollTo(0,0));await settle();
                }
                const box=await page.locator('#board').boundingBox();
                assert.ok(box.x>=0&&box.x+box.width<=width+1,'capture keeps board inside viewport');
                if(width<900)await page.screenshot({path:path.join(output,`${game}-${width}-terminal-table.png`),clip:box});
                else await page.locator('#board').screenshot({path:path.join(output,`${game}-${width}-terminal-table.png`)});
                if(width<900)await page.setViewportSize({width,height:900});
              }
            } else {
              assert.equal(await page.locator('[data-card-id^="secret-"]').count(),0);
              for(const side of ['left','right'])assert.equal(await page.locator(`.position-${side} .mahjong-opponent-hand .mahjong-tile-back`).count(),10);
              assert.equal(await page.locator('.mahjong-terminal-review').count(),0);
            }
          }
        }
        continue;
      }
      if(!process.argv.includes('--review-only')) {
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
      console.log(JSON.stringify({game,width,controlHeight:selected.controls.height,selectDelta:selected.y-initial.y,submitDelta:submitted.y-beforeSubmit.y,status:'PASS'}));
      }
      for(const long of [false,true]) {
        await render(fixture(game,'terminal',long));await settle();
        const fonts=await page.evaluate(()=>{
          const f=s=>{const c=getComputedStyle(document.querySelector(s));return {size:parseFloat(c.fontSize),weight:+c.fontWeight}};
          return {title:f('.mahjong-terminal-title'),summary:f('.mahjong-terminal-summary'),body:f('.mahjong-terminal-review'),name:f('.mahjong-terminal-name'),note:f('.mahjong-terminal-note'),score:f('.mahjong-terminal-score'),scroll:document.documentElement.scrollWidth};
        });
        assert.ok(fonts.title.size>=fonts.body.size+(width<900?3:5) && fonts.title.weight>=700);
        assert.ok(fonts.summary.size>fonts.body.size && fonts.summary.weight>=700);
        assert.ok(fonts.name.size>fonts.body.size && fonts.name.weight>=700);
        assert.ok(fonts.score.size>fonts.name.size && fonts.score.weight>=700);
        assert.equal(fonts.title.size,width<900?16:24);
        assert.equal(fonts.summary.size,width<900?13:19);
        assert.equal(fonts.name.size,width<900?13:17);
        assert.equal(fonts.score.size,width<900?17:23);
        assert.ok(fonts.note.size<fonts.body.size);assert.ok(fonts.scroll<=width,JSON.stringify(fonts));
        const headings=await page.locator('.mahjong-terminal-player').evaluateAll(nodes=>nodes.map(e=>{
          const name=e.querySelector('.mahjong-terminal-name').getBoundingClientRect();
          const count=e.querySelector('.mahjong-terminal-hand-count').getBoundingClientRect();
          return {nameTop:name.top,nameBottom:name.bottom,countTop:count.top,countBottom:count.bottom};
        }));
        if(!long)assert.ok(headings.every(r=>r.countTop<r.nameBottom&&r.countBottom>r.nameTop),'normal name and hand count share a line');
        console.log(JSON.stringify({game,width,long,fonts,status:'PASS review'}));
        if(!long||width===360) {
          await page.locator('#mahjongTerminalReview').screenshot({path:path.join(output,`${game}-${width}-review${long?'-long':''}.png`)});
        }
      }
    }
    if(process.argv.includes('--hands-only')) {assert.deepEqual(errors,[]);await page.close();continue;}
    // Actual shared chat renderer: short feed starts at top; long feed follows
    // new messages until the reader scrolls up, retaining the visible anchor.
    await render(fixture('ka_wu_xing'));
    const list=page.locator('#recentChatMessages');
    const chat=async count=>{
      await page.evaluate(count=>{
        window.events=Array.from({length:count},(_,i)=>({sequence:i+1,event_type:'message',is_public:true,
          sender:{player_id:fixture.participants[0].player_id,role:'human',name:'朋友'},text:`消息 ${i+1}`}));
        run('renderRecentChat(events)');
      },count);await settle();
      assert.deepEqual(await list.locator('li').evaluateAll(es=>es.map(e=>+e.dataset.sequence)),Array.from({length:count},(_,i)=>i+1));
    };
    await chat(1);
    const short=await list.evaluate(e=>({top:e.scrollTop,offset:e.firstElementChild.getBoundingClientRect().top-e.getBoundingClientRect().top,height:e.clientHeight}));
    assert.equal(short.top,0);assert.ok(Math.abs(short.offset)<=1,JSON.stringify(short));
    if(width>=900)await page.locator('#gameChatArea').screenshot({path:path.join(output,`${width}-chat-short.png`)});
    await chat(200);
    const bottom=()=>list.evaluate(e=>Math.abs(e.scrollTop+e.clientHeight-e.scrollHeight)<=1);
    assert.ok(await list.evaluate(e=>e.scrollHeight>e.clientHeight));assert.ok(await bottom());
    await chat(201);assert.ok(await bottom(),'new messages follow bottom');
    await list.evaluate(e=>{e.scrollTop=600;e.dispatchEvent(new Event('scroll'));});
    const anchor=()=>list.evaluate(e=>{
      const top=e.getBoundingClientRect().top,row=[...e.children].find(r=>r.getBoundingClientRect().bottom>top);
      return {sequence:row.dataset.sequence,offset:row.getBoundingClientRect().top-top};
    });
    const before=await anchor();await chat(202);const after=await anchor();
    assert.equal(after.sequence,before.sequence);assert.ok(Math.abs(after.offset-before.offset)<=1);
    await page.evaluate(()=>{events=events.slice(2);run('renderRecentChat(events)');});await settle();
    const trimmed=await anchor();assert.equal(trimmed.sequence,before.sequence);assert.ok(Math.abs(trimmed.offset-before.offset)<=1);
    await list.evaluate(e=>{e.scrollTop=e.scrollHeight;e.dispatchEvent(new Event('scroll'));});
    await chat(203);assert.ok(await bottom(),'scrolling down resumes following');
    if(width>=900)await page.locator('#gameChatArea').screenshot({path:path.join(output,`${width}-chat-full.png`)});
    chatResponse=await page.evaluate(()=>({room:run('room'),timeline:[...events,{...events[0],sequence:204,text:'发送检查'}]}));
    await page.locator('#chatInput').fill('发送检查');
    await page.locator('#sendMessageButton').click();
    await page.waitForFunction(()=>document.querySelector('#recentChatMessages').children.length===204);
    assert.deepEqual(messages,[{message:'发送检查'}]);
    assert.equal(await page.locator('#chatInput').inputValue(),'');
    assert.equal(await list.locator('li').last().locator('p').innerText(),'发送检查');
    assert.ok(await bottom(),'sending retains bottom follow');
    console.log(JSON.stringify({width,short,status:'PASS chat order/top/overflow/follow/history/send'}));
    assert.deepEqual(errors,[]);await page.close();
  }} finally {await browser.close();if(!process.env.DUEL_UI_SCREENSHOTS)fs.rmSync(output,{recursive:true,force:true});}
}
(process.argv.includes('--dom')?domChecks():browserChecks()).catch(e=>{console.error(e);process.exitCode=1});
