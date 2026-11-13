// Local assets and synthetic fixtures only. No service or production DB access.
// PLAYWRIGHT_MODULE=/path/to/playwright CHROME_PATH=/path/to/chrome node scripts/check_kwx_midgame_browser.cjs
// Set DUEL_UI_SCREENSHOTS to retain screenshots for human visual acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const assets = path.join(root, 'vendor/duel/app/static');
const generated = spawnSync(path.join(root, 'vendor/duel/.venv/bin/python'), ['-c', `
import json, tempfile
from pathlib import Path
from scripts import create_kwx_midgame_demo as demo
with tempfile.TemporaryDirectory(prefix='kwx-browser-') as tmp:
    demo.database.DB_PATH=Path(tmp)/'fixture.db'
    demo.database.init_db()
    kwx=demo.create_at(demo.database.DB_PATH)[1]
    entries=json.loads((demo.ROOT/'vendor/duel/scripts/desktop_demo_states.json').read_text())['rooms']
    mj=next(e['room'] for e in entries if e['room']['game_type']=='mahjong')
    mj=demo.framework.project_room_for_viewer(demo.framework._decorate(mj),'7874')
    mj['spectating']=True
    print(json.dumps([kwx,mj]))
`], {cwd:root,encoding:'utf8'});
assert.equal(generated.status,0,generated.stderr);
const fixtures = JSON.parse(generated.stdout);
(async () => {
  const output = process.env.DUEL_UI_SCREENSHOTS || fs.mkdtempSync(path.join(os.tmpdir(),'kwx-visual-'));
  fs.mkdirSync(output,{recursive:true});
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || undefined, args:['--no-sandbox']});
    for (const width of [360,390,430,1280,1700]) {
      const context = await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600});
      const page = await context.newPage();
      const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.host !== 'duel.test') return route.abort();
        if (url.pathname.startsWith('/api/')) return route.fulfill({json:{ok:true}});
        const rel=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        const file=path.join(assets,rel);
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
        let body=fs.readFileSync(file);
        if (rel==='app.js') body=body.toString().replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')+'\nwindow.run=source=>eval(source);';
        return route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});
      });
      await page.goto('http://duel.test/');
      await page.evaluate(()=>window.DuelGameUI.load('mahjong'));
      for (const fixture of fixtures) {
        // 0/8/15 are visual stress data only; the real read-only room is never edited.
        for (const count of fixture.game_type==='ka_wu_xing' ? [null,0,8,15] : [null]) {
          await page.evaluate(({fixture,count})=>{
            const copy=structuredClone(fixture);
            if (count!==null) for (const [pid,tiles] of Object.entries(copy.board_state.discards)) {
              copy.board_state.discards[pid]=Array.from({length:count},(_,i)=>({...tiles[i%tiles.length],id:`visual-${pid}-${i}`}));
            }
            window.fixture=copy;
            window.run(`identity={human_player_id:'1',human_name:'南杉',machines:[],games:[]};room=null;renderGame(window.fixture);`);
          },{fixture,count});
          await page.locator('.mahjong-table').waitFor();
          const dimensions=await page.evaluate(()=>{
            const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
            return {scroll:document.documentElement.scrollWidth,
              hand:rect(document.querySelector('.mahjong-own-hand-wrap')),
              zones:[...document.querySelectorAll('.mahjong-discards')].map(e=>({box:rect(e),tiles:[...e.querySelectorAll('.mahjong-tile')].map(rect)})),
              bottom:rect(document.querySelector('.discard-bottom')),
              controls:rect(document.querySelector('.mahjong-controls'))};
          });
          assert.ok(dimensions.scroll<=width,JSON.stringify({width,dimensions}));
          for (const zone of dimensions.zones) {
            assert.ok(zone.box.left>=0 && zone.box.right<=width);
            if (fixture.game_type==='ka_wu_xing') for (const [i,t] of zone.tiles.entries()) {
              assert.ok(t.left>=zone.box.left-1 && t.right<=zone.box.right+1 && t.bottom<=zone.box.bottom+1,'tile clipped '+JSON.stringify({width,count,zone}));
              for (const other of zone.tiles.slice(i+1)) assert.ok(t.right<=other.left+1 || other.right<=t.left+1 || t.bottom<=other.top+1 || other.bottom<=t.top+1,'tiles overlap');
            }
          }
          if (fixture.game_type==='ka_wu_xing') assert.ok(dimensions.bottom.height>=99,'bottom collapsed');
          assert.ok(dimensions.hand.top>=Math.max(...dimensions.zones.map(z=>z.box.bottom)));
          await page.locator('.mahjong-own-hand-wrap').scrollIntoViewIfNeeded();
          assert.ok(await page.locator('.mahjong-own-hand-wrap').isVisible());
          await page.screenshot({path:path.join(output,`${fixture.game_type}-${width}-${count??'real'}.png`),fullPage:true});
          console.log(JSON.stringify({game:fixture.game_type,width,count:count??'real',bottomHeight:dimensions.bottom.height,status:'passed'}));
        }
      }
      // Ordinary play uses the same layout: hand selection must keep the submit reachable.
      await page.evaluate(fixture=>{
        const copy=structuredClone(fixture), actor=copy.participants[0];
        Object.assign(copy,{demo:false,read_only:false,spectating:false,current_player_id:'1',current_player:actor,current_actor:actor});
        const tile=copy.private_state.hand[0];
        copy.private_state.legal_actions=[{kind:'discard',action_id:'discard:'+tile.id,tile_id:tile.id}];
        window.fixture=copy;
        window.run('room=null;renderGame(window.fixture);');
      },fixtures[0]);
      await page.locator('.mahjong-own-hand-scroll button:not(:disabled)').first().click();
      const submit=page.locator('.mahjong-discard-button');
      await submit.scrollIntoViewIfNeeded();
      assert.ok(await submit.isVisible());
      assert.ok(await submit.isEnabled());
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:path.join(output,`ka_wu_xing-${width}-ordinary-controls.png`),fullPage:true});
      assert.deepEqual(errors,[]);
      await context.close();
    }
  } finally {
    if (browser) await browser.close();
    if (!process.env.DUEL_UI_SCREENSHOTS) fs.rmSync(output,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
