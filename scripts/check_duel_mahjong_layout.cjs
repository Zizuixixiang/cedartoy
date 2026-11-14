// Offline real-DOM/CSS checks: node scripts/check_duel_mahjong_layout.cjs --dom
// Rendered geometry/screenshots: PLAYWRIGHT_MODULE=/path/to/playwright node scripts/check_duel_mahjong_layout.cjs
// Uses local assets and disposable SQLite fixtures only; never contacts a service.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const repo = path.join(root, 'vendor/duel');
const assets = path.join(repo, 'app/static');
const widths = process.env.DUEL_UI_WIDTHS ? process.env.DUEL_UI_WIDTHS.split(',').map(Number) : [360, 390, 430, 900, 1200, 1280, 1440, 1700, 1920];
const cssFiles = ['styles.css', 'games/mahjong.css'];
const app = fs.readFileSync(path.join(assets, 'app.js'), 'utf8')
  .replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '') + '\nwindow.run = source => eval(source);';
const generated = spawnSync(process.env.DUEL_PYTHON || path.join(repo, '.venv/bin/python'), ['-c', `
import json,tempfile
from pathlib import Path
from app import database,invites,framework
from app.games import game_catalog
with tempfile.TemporaryDirectory(prefix='mj-layout-') as tmp:
 database.DB_PATH=Path(tmp)/'test.db'; database.init_db()
 fixtures={}
 for key,count in [('mahjong',4),('ka_wu_xing',3)]:
  r=invites.create_invite(key,'human','human:1',target_player_count=count,display_name='南杉')
  for i in range(1,count):
   r=invites.join_invite(r['invite_code'],'human',f'human:{i+1}',display_name=f'朋友{i}')
  waiting=framework.project_room_for_viewer(r,'human:1')
  r=invites.start_invite(r['room_id'],'human','human:1')
  fixtures[key]=dict(waiting=waiting,playing=framework.project_room_for_viewer(r,r['current_player_id']))
 print(json.dumps(dict(fixtures=fixtures,catalog=game_catalog())))
`], {cwd: repo, encoding: 'utf8'});
assert.equal(generated.status, 0, generated.stderr);
const {fixtures,catalog} = JSON.parse(generated.stdout);
// Deterministic mixed-script names at the right rail in both variants.
for (const {playing:f} of Object.values(fixtures)) {
  const viewer=f.participants.find(p=>p.player_id===f.viewer.player_id);
  const names=['南杉','Codex_杉星_0828','朋友阿青','北座小紫'];
  f.participants.forEach(p=>{p.display_name=names[(p.seat_index-viewer.seat_index+f.participants.length)%f.participants.length];});
}
function show(win, fixture) {
  win.fixture = fixture;
  win.catalog = catalog;
  win.run(`identity={human_player_id:fixture.viewer.player_id,human_name:'南杉',machines:[],games:catalog}; room=null; renderGame(fixture);`);
}
function discards(fixture, counts, longNames=false) {
  const f = structuredClone(fixture);
  const viewer=f.participants.find(p=>p.player_id===f.viewer.player_id);
  f.participants.forEach(p => {
    const index=(p.seat_index-viewer.seat_index+f.participants.length)%f.participants.length;
    if(longNames) p.display_name = index===1 ? 'Codex_杉星_0828_extra_long_name_123456' : '长名字测试朋友'.repeat(6);
    const count=Array.isArray(counts)?counts[index]:counts;
    f.board_state.discards[p.player_id] = Array.from({length:count}, (_,i) => ({...f.private_state.hand[i%14],id:`discard-${p.player_id}-${i}`}));
  });
  return f;
}
const stress = fixture => discards(fixture,30,true);
function sideHands(fixture, count) {
  const f = structuredClone(fixture);
  const viewer = f.participants.find(p=>p.player_id===f.viewer.player_id);
  for (const p of f.participants) {
    const relative = (p.seat_index-viewer.seat_index+f.participants.length)%f.participants.length;
    if (relative===1 || relative===f.participants.length-1) f.board_state.hand_counts[p.player_id]=count;
  }
  return f;
}
function checkDOM(doc, game) {
  assert.equal(doc.querySelectorAll('.mahjong-seat').length, game === 'mahjong' ? 4 : 3);
  assert.equal(doc.querySelectorAll('.position-top.mahjong-seat').length, game === 'mahjong' ? 1 : 0);
  assert.equal(doc.querySelectorAll('.mahjong-own-hand-scroll button').length, 14);
  for (const name of doc.querySelectorAll('.mahjong-seat-name')) assert.equal(name.title, name.textContent);
  assert.ok(!doc.querySelector('.game-header').classList.contains('hidden'));
}
// JSDOM has no media evaluator or layout engine. Flatten media queries explicitly
// to test the cascade; cqw/clamp values below remain expressions, not measurements.
function mediaMatches(query, width) {
  return query.split(',').some(part => part.split(/\s+and\s+/).every(term => {
    const m = term.trim().match(/^\((min-width|max-width):\s*(\d+)px\)$/);
    if (m) return m[1] === 'min-width' ? width >= +m[2] : width <= +m[2];
    if (/^\((hover: hover|pointer: fine)\)$/.test(term.trim())) return width >= 900;
    if (/prefers-reduced-motion/.test(term)) return false;
    throw new Error(`Unsupported media query: ${term}`);
  }));
}
function activeCSS(win, css, width) {
  const sheet = new win.CSSStyleSheet(); sheet.replaceSync(css);
  const flatten = rules => [...rules].map(r => r.type === 4
    ? (mediaMatches(r.conditionText, width) ? flatten(r.cssRules) : '') : r.cssText).join('\n');
  return flatten(sheet.cssRules);
}
async function domChecks() {
  const {JSDOM} = await import('jsdom');
  const before = cssFiles.map(file => {
    const result = spawnSync('git', ['show', `HEAD:app/static/${file}`], {cwd:repo, encoding:'utf8'});
    assert.equal(result.status, 0, result.stderr); return result.stdout;
  });
  const after = cssFiles.map(file => fs.readFileSync(path.join(assets,file),'utf8'));
  for (const width of widths) {
    const dom = new JSDOM(fs.readFileSync(path.join(assets,'index.html'),'utf8'), {url:'https://duel.test/',runScripts:'outside-only',pretendToBeVisual:true});
    const w = dom.window, doc = w.document;
    w.matchMedia = query => ({matches:mediaMatches(query,width),addEventListener(){},removeEventListener(){}});
    for (const file of ['game_ui_registry.js','games/mahjong.js']) w.eval(fs.readFileSync(path.join(assets,file),'utf8'));
    w.eval(app);
    const style = doc.createElement('style'); doc.head.append(style);
    const setCSS = sources => {style.textContent = sources.map(s => activeCSS(w,s,width)).join('\n');};
    const computed = el => {
      const s=w.getComputedStyle(el);
      return Object.fromEntries([...s].map(p=>[p,s.getPropertyValue(p)]));
    };
    for (const [game, states] of Object.entries(fixtures)) {
      setCSS(after); show(w,states.waiting);
      assert.ok(doc.querySelector('#gameView').classList.contains('invite-waiting-view'));
      show(w,states.playing); checkDOM(doc,game);
      const side = doc.querySelector('.position-left .mahjong-seat-name');
      assert.equal(w.getComputedStyle(side).writingMode,'vertical-rl');
      assert.equal(w.getComputedStyle(side).textOrientation,width<900?'upright':'mixed');
      if (width < 900) {
        // Every computed property of the actual board subtree stays identical.
        const nodes = [...doc.querySelectorAll('.mahjong-board-layout, .mahjong-board-layout *')];
        const current=nodes.map(computed);
        setCSS(before); assert.deepEqual(nodes.map(computed),current,`${game} phone CSS ${width}`);
        setCSS(after);
        const expected=width===360?['23px','33px']:['25px','36px'];
        const tile=w.getComputedStyle(doc.querySelector('.mahjong-own-hand-scroll button'));
        assert.deepEqual([tile.width,tile.height],expected);
      } else {
        assert.equal(w.getComputedStyle(doc.querySelector('.mahjong-board-layout')).width,'100%');
        assert.equal(w.getComputedStyle(side).overflow,'hidden');
        assert.equal(w.getComputedStyle(side).whiteSpace,'nowrap');
        assert.equal(w.getComputedStyle(side).maxHeight,'148px');
        assert.equal(w.getComputedStyle(doc.querySelector('.position-left.mahjong-seat')).alignSelf,'stretch');
        assert.equal(w.getComputedStyle(doc.querySelector('.position-left.mahjong-seat')).alignContent,'start');
        assert.equal(w.getComputedStyle(doc.querySelector('.position-left .mahjong-seat-identity')).alignSelf,'start');
        assert.equal(w.getComputedStyle(doc.querySelector('.mahjong-board-layout')).maxWidth,'1100px');
        assert.equal(w.getComputedStyle(doc.querySelector('.mahjong-own-hand-scroll')).overflowX,'auto');
        // Ensure room switching removes the wider workspace rule.
        const stage=doc.querySelector('#battleStage'), main=doc.querySelector('.duel-main');
        // JSDOM cannot compute min()/cqw geometry; verify semantic scope here.
        const wideScope='.duel-main:has(> .game-view:not(.hidden) > .battle-stage:is([data-game-type="ka_wu_xing"], [data-game-type="mahjong"]))';
        assert.ok(main.matches(wideScope));
        stage.dataset.gameType='gomoku'; assert.ok(!main.matches(wideScope)); const otherAfter=computed(main);
        setCSS(before); assert.deepEqual(computed(main),otherAfter,'other game width unchanged');
        setCSS(after); stage.dataset.gameType=game;
      }
      show(w,stress(states.playing)); checkDOM(doc,game);
      assert.equal(doc.querySelectorAll('.mahjong-discard-grid .mahjong-tile').length,game==='mahjong'?120:90);
    }
    dom.window.close();
    console.log(`PASS DOM/CSS ${width}px (no rendered geometry)`);
  }
}
async function browserChecks() {
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const output=process.env.DUEL_UI_SCREENSHOTS || fs.mkdtempSync(path.join(require('node:os').tmpdir(),'mj-layout-visuals-'));
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined,args:['--no-sandbox']});
  try {
    for (const width of widths) {
      const page=await browser.newPage({viewport:{width,height:1000},hasTouch:width<900,isMobile:width<900});
      const errors=[]; page.on('pageerror',e=>errors.push(e.message));
      const device=await page.context().newCDPSession(page);
      await page.route('**/*',route=>{
        const u=new URL(route.request().url()); if(u.host!=='duel.test')return route.abort();
        if(u.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true}});
        const rel=u.pathname==='/'?'index.html':u.pathname.replace(/^\/static\//,'');
        const file=path.join(assets,rel); if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
        return route.fulfill({body:rel==='app.js'?app:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':rel.endsWith('.svg')?'image/svg+xml':'text/html'});
      });
      await page.goto('http://duel.test/');
      // Optional local CJK font for Linux hosts whose fallback has no vertical metrics.
      // Keep production CSS untouched; Windows can use its native Microsoft YaHei.
      if(process.env.DUEL_UI_CJK_FONT){
        const font=fs.readFileSync(process.env.DUEL_UI_CJK_FONT).toString('base64');
        await page.addStyleTag({content:`@font-face {font-family:"Microsoft YaHei";src:url(data:font/otf;base64,${font})}`});
        await page.evaluate(()=>document.fonts.load('14px "Microsoft YaHei"'));
        if(width<900) await page.addStyleTag({content:'.mahjong-seat-name {font-family:system-ui,"Microsoft YaHei",sans-serif !important}'});
      }
      await page.evaluate(()=>window.DuelGameUI.load('mahjong'));
      for(const [game,states] of Object.entries(fixtures)) {
        const cases = [['waiting',states.waiting],['playing',states.playing],['midgame',discards(states.playing,[11,10,10,10])],['full',discards(states.playing,15)],['stress',stress(states.playing)]];
        if(width>=900) for(const count of [13,14]) for(const long of [false,true]) {
          for(const [phase,discardsCount] of [['empty',0],['mid',[11,10,10,10]],['full',15]]) {
            cases.push([`backs-${count}-${long?'long':'normal'}-${phase}`,sideHands(discards(states.playing,discardsCount,long),count)]);
          }
        }
        for(const [state,fixture] of cases) {
          // Full-page screenshots can reset Chromium's touch emulation after a
          // tall fixture. Restore the input device before measuring the next one.
          if(width<900) await device.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
          await page.evaluate(({f,catalog})=>{window.fixture=f;window.catalog=catalog;window.run(`identity={human_player_id:fixture.viewer.player_id,human_name:'南杉',machines:[],games:catalog};room=null;renderGame(fixture);`);},{f:fixture,catalog});
          if(state==='waiting'){assert.ok(await page.locator('#inviteRoomPanel').isVisible());continue;}
          await page.locator('.mahjong-table').waitFor();
          const m=await page.evaluate(()=>{
            const rect=s=>document.querySelector(s).getBoundingClientRect().toJSON();
            const hand=document.querySelector('.mahjong-own-hand-scroll');
            return {viewport:{inner:innerWidth,fine:matchMedia('(pointer: fine)').matches},page:document.documentElement.scrollWidth,table:rect('.mahjong-table'),tile:rect('.mahjong-own-hand-scroll button'),hand:{width:hand.clientWidth,scroll:hand.scrollWidth},chat:rect('.game-chat-area'),center:rect('.mahjong-center'),top:[...document.querySelectorAll('.position-top .mahjong-tile-back')].map(e=>e.getBoundingClientRect().toJSON()),side:[...document.querySelectorAll('.mahjong-seat:is(.position-left,.position-right)')].map(s=>{
              const r=c=>s.querySelector(c).getBoundingClientRect().toJSON();return {player:s.dataset.playerId,seat:s.getBoundingClientRect().toJSON(),avatar:r('.mahjong-seat-avatar'),name:r('.mahjong-seat-name'),badges:r('.mahjong-seat-badges'),hand:r('.mahjong-opponent-hand'),backs:[...s.querySelectorAll('.mahjong-tile-back')].map(t=>t.getBoundingClientRect().toJSON()),writing:getComputedStyle(s.querySelector('.mahjong-seat-name')).writingMode,orientation:getComputedStyle(s.querySelector('.mahjong-seat-name')).textOrientation};
            }),status:[...document.querySelector('.mahjong-center-status').children].map(e=>e.getBoundingClientRect().toJSON()),discards:[...document.querySelectorAll('.mahjong-discard-grid')].map(g=>[...g.children].map(t=>t.getBoundingClientRect().toJSON()))};
          });
          assert.equal(m.viewport.fine,width>=900,'input device must match viewport fixture');
          assert.ok(m.page<=width,JSON.stringify(m));
          assert.ok(m.table.x>=0 && m.table.right<=width,'table must stay inside viewport');
          for(const s of m.side){
            assert.equal(s.writing,'vertical-rl');
            for(const box of [s.avatar,s.name,s.badges]){
              assert.ok(box.right<=s.hand.x || s.hand.right<=box.x,'identity overlaps backs');
              assert.ok(box.x>=s.seat.x && box.right<=s.seat.right && box.y>=s.seat.y && box.bottom<=s.seat.bottom,'identity clipped by seat');
            }
            assert.ok(s.avatar.bottom<=s.name.y,'avatar overlaps name');
            assert.ok(s.name.bottom<=s.badges.y,'name overlaps badges');
            if(width>=900){
              assert.equal(s.orientation,'mixed');
              assert.ok(Math.abs(s.seat.y-m.center.y)<1,'side group must start beside center');
              assert.ok(s.avatar.y-s.seat.y<=8,'avatar must stay at top of group');
              assert.ok(s.hand.y-s.seat.y<=8,'backs must start beside identity');
              assert.ok(s.name.height>=14 && s.name.height<=148.5,'vertical name must be visible and bounded');
              assert.ok(Math.abs(s.seat.bottom-m.center.bottom)<1,'side frame must end level with center');
              assert.equal(s.backs.length,fixture.board_state.hand_counts[s.player]);
              for(const [i,tile] of s.backs.entries()) {
                assert.ok(tile.height>=19.9 && tile.height<=24.1 && tile.width>=30.9 && tile.width<=37.3,'side backs must stay legible without shrinking');
                assert.ok(tile.x>=s.hand.x && tile.right<=s.hand.right+0.1 && tile.y>=s.hand.y && tile.bottom<=s.hand.bottom+0.1,'back clipped by hand');
                assert.ok(tile.x>=s.seat.x+1 && tile.right<=s.seat.right-1 && tile.y>=s.seat.y+1 && tile.bottom<=s.seat.bottom-1,'back clipped by frame');
                if(i) assert.ok(tile.y>=s.backs[i-1].bottom-0.1,'side backs overlap');
              }
            }
          }
          if(width>=1200){assert.ok(m.hand.scroll<=m.hand.width,'14 tiles must fit');assert.ok(m.chat.width<=300.5);}
          if(width>=900)assert.ok(m.table.width<=1100.5,'table must not stretch into a wide empty field');
          if(width>=900){
            if(state==='playing') assert.ok(m.table.height>=600 && m.table.height<=700.5,'preserve desktop table height');
            assert.ok(m.status.every(t=>Math.abs((t.y+t.height/2)-(m.status[0].y+m.status[0].height/2))<1),'center status must stay on one line');
          }
          if(width>=1280)assert.ok(m.table.width>=900,'do not return to a 760px card table');
          if(game==='mahjong'){
            assert.equal(m.top.length,13);
            assert.ok(m.top.every(t=>Math.abs(t.y-m.top[0].y)<1),'top player backs stay on one row');
            if(width>=900) assert.ok(Math.abs(m.top[0].width-Math.max(16,Math.min(22,m.table.width*0.018)))<0.1,'side enlargement must not change top backs');
          }
          if(width>=900 || game==='ka_wu_xing')for(const grid of m.discards){
            for(let i=1;i<grid.length;i++)if(grid[i].y>grid[i-1].y)assert.ok(grid[i].y>=grid[i-1].bottom-1,'discard rows overlap');
          }
          // Capture tall phones without fullPage temporarily switching input media.
          if(width<900){
            const height=await page.evaluate(()=>Math.max(1000,document.documentElement.scrollHeight));
            await page.setViewportSize({width,height});
            await device.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
            await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
          }
          if(!state.startsWith('backs-') || state==='backs-14-long-full') {
            await page.screenshot({path:path.join(output,`${game}-${width}-${state}.png`),fullPage:width>=900});
          }
          if(width<900) await page.setViewportSize({width,height:1000});
          if(state==='playing') {
            await page.locator('.mahjong-own-hand-scroll button:not(:disabled)').first().focus();
            await page.keyboard.press('Enter');
            assert.equal(await page.locator('.mahjong-own-hand-scroll button.selected').count(),1);
          }
          console.log(JSON.stringify({game,width,state,viewport:m.viewport,table:[m.table.width,m.table.height],tile:[m.tile.width,m.tile.height],hand:m.hand,side:m.side.map(s=>({height:s.seat.height,bottomDelta:s.seat.bottom-m.center.bottom,back:[s.backs[0]?.width,s.backs[0]?.height],count:s.backs.length,nameHeight:s.name.height,orientation:s.orientation}))}));
        }
      }
      assert.deepEqual(errors,[]); await page.close();
    }
    console.log(`Screenshots: ${output}`);
  } finally {await browser.close();}
}
(process.argv.includes('--dom')?domChecks():browserChecks()).catch(e=>{console.error(e);process.exitCode=1;});
