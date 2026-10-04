/* Category-only acceptance: actual page, catalog and rules; disposable DBs,
 * intercepted local assets only. No production server or authentication. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const out=process.env.DUEL_UI_SCREENSHOTS||'/tmp/duel-tabletop-check/browser';
fs.mkdirSync(out,{recursive:true});
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'duel-categories-'));
const env={...process.env,TMPDIR:tmp,PYTHONPYCACHEPREFIX:path.join(tmp,'pycache')};
let sequence=0;
const python=String.raw`
import json,sys
from unittest.mock import patch
from app import database,framework
from app.games import GAMES,game_catalog
request=json.load(sys.stdin)
if request['action']=='seed':
 database.init_db();rooms={}
 for key,old_category in [('monopoly','board'),('rummikub','card')]:
  with patch.object(GAMES[key],'category',old_category):
   room=framework.create_room(key,'human_first','human','p0','p1',require_confirmations=False)
  rooms[key]=room['room_id']
 result={'catalog':game_catalog(),'rooms':rooms}
else:
 room=framework.get_room(request['room_id'])
 if request['action']=='move':
  room=framework.play_move(room['room_id'],'human','p0',request['move'],expected_revision=request['revision'])
 result={'ok':True,'room':framework.project_room_for_viewer(room,'p0'),'timeline':[]}
print(json.dumps(result))
`;
function backend(input){
 if(input.action==='seed')env.DUEL_DB_PATH=path.join(tmp,`fixture-${++sequence}.db`);
 const r=spawnSync(process.env.DUEL_TEST_PYTHON||path.join(root,'vendor/duel/.venv/bin/python'),['-c',python],
  {cwd:path.join(root,'vendor/duel'),env,input:JSON.stringify(input),encoding:'utf8',timeout:15000});
 assert.equal(r.status,0,r.stderr||String(r.error||''));return JSON.parse(r.stdout);
}
(async()=>{
 let browser;const measurements=[];
 try{
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  for(const width of [360,430,1280]){
   const fixture=backend({action:'seed'}),posts=[];
   const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<500});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());if(url.hostname!=='categories.test')return route.abort();
    if(url.pathname==='/'||url.pathname.startsWith('/static/')){
     const rel=url.pathname==='/'?'index.html':url.pathname.slice(8);
     const file=path.join(root,'vendor/duel/app/static',rel);
     if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
     return route.fulfill({body:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
    }
    let data={ok:true,rooms:[],notifications:[],timeline:[]};
    if(url.pathname==='/api/whoami')data={...data,bound:true,human_player_id:'p0',human_name:'测试玩家',
     machines:[{id:'p1',name:'测试小机'}],games:fixture.catalog,npc_provider:{available:true},wallet:{balance:0}};
    else if(/^\/api\/rooms\/[A-Z0-9]+$/.test(url.pathname))data=backend({action:'state',room_id:url.pathname.split('/')[3]});
    else if(url.pathname.endsWith('/move')){
     const body=route.request().postDataJSON();posts.push(body);
     data=backend({action:'move',room_id:url.pathname.split('/')[3],move:body.move,revision:body.revision});
    }
    return route.fulfill({json:data});
   });
   const options=selector=>page.locator(`${selector} option`).evaluateAll(es=>es.map(e=>({value:e.value,text:e.textContent})));
   const shot=async name=>{
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${name}/${width} page overflow`);
    if(width!==430)await page.screenshot({path:path.join(out,`${name}-${width}.png`),fullPage:true});
   };
   const checkSelects=async(category,game)=>{
    assert.deepEqual(await options(category),[{value:'board',text:'棋'},{value:'card',text:'牌'},{value:'dice',text:'骰'},{value:'tabletop',text:'桌游'}]);
    for(const key of ['board','card','dice']){
     await page.locator(category).selectOption(key);
     assert.ok((await options(game)).every(g=>!['monopoly','rummikub'].includes(g.value)));
    }
    await page.locator(category).selectOption('tabletop');
    assert.deepEqual((await options(game)).map(g=>g.value),['rummikub','monopoly']);
    for(const key of ['monopoly','rummikub']){
     await page.locator(game).selectOption(key);assert.equal(await page.locator(game).inputValue(),key);
     if(game==='#gameType'){
      assert.equal(await page.locator('#gameTokenEstimate').textContent(),key==='monopoly'?'（约1500–14000 token/轮）':'（约500–6000 token/轮）');
      assert.match(await page.locator('#gameTokenEstimate').getAttribute('title'),/不是整轮实际计费/);
     }
    }
    if(game==='#gameType'){
     const tabletopStyle=await page.locator('#gameTokenEstimate').evaluate(e=>{const s=getComputedStyle(e);return [s.fontSize,s.color,s.fontFamily];});
     await page.locator(category).selectOption('board');await page.locator(game).selectOption('aeroplane_chess');
     assert.equal(await page.locator('#gameTokenEstimate').textContent(),'（约40–150 token/轮）');
     assert.equal(await page.locator('#gameTokenEstimate').getAttribute('title'),'');
     assert.deepEqual(await page.locator('#gameTokenEstimate').evaluate(e=>{const s=getComputedStyle(e);return [s.fontSize,s.color,s.fontFamily];}),tabletopStyle);
     await page.locator(category).selectOption('tabletop');await page.locator(game).selectOption('monopoly');
    }
    measurements.push(await page.locator(category).evaluate(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {width:innerWidth,selector:e.id,height:r.height,font:s.fontSize,fontFamily:s.fontFamily,border:s.border,shadow:s.boxShadow};}));
   };
   await page.goto('http://categories.test/');await page.locator('#gameCategory').waitFor({state:'visible'});
   await checkSelects('#gameCategory','#gameType');await shot('ordinary-selector');
   await page.locator('#inviteCreateEntry').click();
   await checkSelects('#inviteCategory','#inviteGame');await shot('invite-selector');
   await page.locator('#inviteDismiss').click();
   for(const game of ['rummikub','monopoly']){
    const id=fixture.rooms[game];await page.goto(`http://categories.test/?room=${id}`);
    const selector=game==='rummikub'?'.rk-game':'.monopoly-ring';await page.locator(selector).waitFor();
    const dismiss=page.locator('#dismissWaitModeModalButton');if(await dismiss.isVisible())await dismiss.click();
    await page.waitForFunction(key=>key==='rummikub'?document.querySelector('.rk-tile')?.getBoundingClientRect().width===44:
     getComputedStyle(document.querySelector('.monopoly-ring')).display==='grid',game);
    assert.equal(await page.locator('#battleStage').getAttribute('data-game-category'),'tabletop');
    const widths=await page.evaluate(key=>{
     const stage=document.querySelector('#battleStage'),board=document.querySelector('#board'),controls=document.querySelector('#gameControls');
     const before={board:board.getBoundingClientRect().width,controls:controls.getBoundingClientRect().width};
     stage.dataset.gameCategory=key==='rummikub'?'card':'board';
     const old={board:board.getBoundingClientRect().width,controls:controls.getBoundingClientRect().width};
     stage.dataset.gameCategory='tabletop';return {before,old};
    },game);
    assert.deepEqual(widths.before,widths.old,'category does not change existing game geometry');
    if(game==='rummikub'){
     assert.equal(await page.locator('.rk-hand .rk-tile').count(),14);
     await page.locator('.rk-hand .rk-tile').first().click();await page.locator('[data-rk-focus="new"]').click();
     await page.locator('[data-rk-focus="undo"]').click();assert.equal(posts.length,0);
     await page.locator('[data-rk-focus="draw"]').click();
     await page.waitForFunction(()=>document.querySelector('.rk-draft-status')?.textContent.includes('等待'));
     assert.equal(await page.locator('.rk-hand .rk-tile').count(),15);
    }else{
     assert.equal(await page.locator('.monopoly-tile').count(),40);
     await page.locator('[data-tile-id="1"]').click();await page.locator('.monopoly-dialog').waitFor();await page.keyboard.press('Escape');
     await page.locator('[data-action="roll"]').click();await page.waitForFunction(()=>document.querySelectorAll('.monopoly-die').length===2);
    }
    assert.equal(posts.at(-1).move.action,game==='rummikub'?'draw':'roll');
    await shot(game);await page.reload();await page.locator(selector).waitFor();
    assert.equal(backend({action:'state',room_id:id}).room.revision,1);
   }
   assert.deepEqual(errors,[]);await page.close();
  }
  fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measurements,null,2));
  console.log(JSON.stringify({ok:true,widths:[360,430,1280],selectors:['ordinary','invite'],games:['rummikub','monopoly'],oldRoomActions:true,screenshots:out}));
 }finally{if(browser)await browser.close();fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
