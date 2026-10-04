/* Real Duel page + renderer in Chromium. All rooms are fixtures generated in
 * temporary SQLite databases; network is blocked except for intercepted assets.
 * PLAYWRIGHT_MODULE points to an existing Playwright install if not local.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync, spawn} = require('node:child_process');
const os = require('node:os');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const checkMonopolyMotion = require('./duel_monopoly_motion_cases');
const checkMonopolyEvents = require('./duel_monopoly_event_browser_cases');
const checkPendingTrades = require('./duel_monopoly_pending_trade_cases');
const output = process.env.DUEL_UI_SCREENSHOTS || '/tmp/duel-monopoly-browser';
fs.mkdirSync(output, {recursive: true});
const generated = spawnSync('.venv/bin/python', ['-c', `
import tempfile,json
from pathlib import Path
from app import database,invites,framework
from app.games import GAMES,game_catalog
from tests.test_monopoly_card_events import browser_card_cases
rooms=[]
with tempfile.TemporaryDirectory(prefix='monopoly-ui-') as d:
 database.DB_PATH=Path(d)/'test.db'
 database.init_db()
 for count in [2,4,6]:
  key='monopoly'
  r=invites.create_invite(key,'human','human:1',target_player_count=count,display_name='南杉')
  for i in range(1,count):
   r=invites.join_invite(r['invite_code'],'human',f'human:{i+1}',display_name=('一位名字很长的朋友阿青' if i==1 else f'朋友{i+1}'))
  r=invites.start_invite(r['room_id'],'human','human:1')
  rooms.append(framework.project_room_for_viewer(r,'human:1'))
 r=invites.create_invite('aeroplane_chess','human','human:1',target_player_count=2,display_name='南杉')
 r=invites.join_invite(r['invite_code'],'human','human:2',display_name='朋友')
 r=invites.start_invite(r['room_id'],'human','human:1')
 ordinary={}
 for game in ['monopoly','aeroplane_chess','uno']:
  seats=[dict(player_id='human:1',role='human',participant_kind='human',display_name='南杉'),dict(player_id='bound-test',role='ai',participant_kind='bound_machine',display_name='测试小机')]
  o=framework.create_room(game,'human_first','human','human:1','bound-test',require_confirmations=False,ordered_participants=seats)
  ordinary[game]=framework.project_room_for_viewer(o,'human:1')
 print(json.dumps(dict(ordinary=ordinary,rooms=rooms,aeroplane=framework.project_room_for_viewer(r,'human:1'),catalog=game_catalog(),engine='monopoly' in GAMES,card_cases=browser_card_cases(sorted(rooms[2]['participants'],key=lambda p:p['player_id'])))))
`], {cwd:path.join(root,'vendor/duel'),encoding:'utf8'});
assert.equal(generated.status,0,generated.stderr);
const fixtures=JSON.parse(generated.stdout);
assert.equal(fixtures.engine,true,'Monopoly must be registered for browser acceptance');
// Deliberate synthetic phase projections supplement real opening projections.
const syntheticBoard = count => ({phase:'manage',action_seq:5,turn_number:3,current_player_id:'human:1',turn_player_id:'human:1',dice:[3,4],
  players:Array.from({length:count},(_,i)=>({player_id:`human:${i+1}`,cash:1500+i*50,position:i?i*7:0,property_count:2,asset_value:1900+i*50})),
  tiles:Array.from({length:40},(_,i)=>({id:i,name: i===0?'起点':i===10?'监狱':i===20?'停车':i===30?'入狱':['松林路','梅花街','机会','竹影巷','车站'][i%5],kind:i%10===0?['start','jail','parking','go_to_jail'][i/10]:i%5===2?'chance':i%5===4?'railroad':'property',color:['#ae7550','#66a7b8','#cc78a5','#e49a42'][Math.floor(i/10)],price:120,build_cost:50,mortgage_value:60,redemption_cost:66,rent:20,rents:[6,30,90,270,400,550],owner:[1,3].includes(i)?'human:1':i===6?'human:2':null,level:i===3?2:0,mortgaged:i===6})),
  last_action_note:'经过起点领取 200。现在可以经营地产或与玩家交易。'});
function phaseRoom(base,phase='manage') {
 const r=structuredClone(base), count=r.participants.length;
 r.game_type='monopoly';r.game_name='大富翁';r.revision=100+['manage','purchase','auction','trade','debt','finished'].indexOf(phase);r.status='playing';r.current_player_id='human:1';r.current_turn='human';r.turn='human';r.current_actor=r.participants.find(p=>p.player_id==='human:1');
 r.viewer={...r.viewer,player_id:'human:1',role:'human',can_move:true,is_participant:true};r.board_state=syntheticBoard(count);r.board_state.phase=phase;
 r.private_state={legal_actions:[{action:'end_turn'},{action:'build',tile_id:1},{action:'mortgage',tile_id:1}],trade_options:{partners:['human:2'],give_tiles:[1],take_tiles_by_player:{'human:2':[6]},cash_by_player:{'human:1':1500,'human:2':1550}}};
 if(count===6){const npc=r.participants.find(p=>p.player_id==='human:6');npc.participant_kind='system_npc';npc.display_name='测试NPC';}
 r.private_state.trade_options.partners=Array.from({length:count-1},(_,i)=>`human:${i+2}`);
 for(let i=2;i<=count;i++){r.private_state.trade_options.cash_by_player[`human:${i}`]=1400+i*50;r.private_state.trade_options.take_tiles_by_player[`human:${i}`]=i===2?[6]:[];}
 if(phase==='purchase'){r.board_state.players[0].position=8;r.private_state.legal_actions=[{action:'buy'},{action:'auction'}];}
 if(phase==='auction'){r.board_state.auction={tile_id:8,bid:80,highest_bidder:'human:2'};r.private_state.legal_actions=[{action:'bid',amount:81},{action:'pass_bid'}];}
 if(phase==='trade'){r.private_state.trade_options={partners:[]};r.board_state.phase='roll';r.board_state.trade={from:'human:2',to:'human:1',give_cash:10,take_cash:0,give_tiles:[6],take_tiles:[1]};r.private_state.legal_actions=[{action:'respond_trade',accept:true},{action:'respond_trade',accept:false}];}
 if(phase==='finished'){r.status='finished';r.current_player_id=null;r.winner='human';r.winner_player_id='human:1';r.board_state.phase='finished';r.board_state.winner_player_id='human:1';r.board_state.players.forEach((p,i)=>p.bankrupt=i>0);r.private_state={legal_actions:[],trade_options:{partners:[]}};}
 if(phase==='debt'){r.board_state.debt={amount:2000,creditor:'human:2'};r.private_state.legal_actions=[{action:'mortgage',tile_id:1},{action:'bankrupt'}];}
 r.board_state.legal_actions=structuredClone(r.private_state.legal_actions);
 return r;
}

async function screenshot(page, options) {
 const file=path.basename(options.path);
 if (/^(ordinary|player-detail|invite-mentions)-/.test(file) || file==='auction-360.png' || file==='trade-6-360.png') await page.screenshot(options);
}

async function checkLiveBrowser(browser) {
 const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'monopoly-browser-live-'));
 const ready=path.join(temporary,'ready.json');
 const code=`
import json,os,socket,random
from pathlib import Path
from unittest.mock import patch
import uvicorn
from app import database,framework,invites
from app.games import GAMES
from app.games.monopoly import Monopoly
class Dice(random.Random):
 def randint(self,a,b):
  if (a,b)==(1,6):
   self.n=getattr(self,'n',0)+1
   return 1 if self.n%2 else 2
  return super().randint(a,b)
GAMES['monopoly']=Monopoly(Dice(7))
database.init_db()
seats=[dict(player_id='human:1',role='human',participant_kind='human',display_name='南杉'),dict(player_id='ai-test',role='ai',participant_kind='bound_machine',display_name='测试小机')]
bound=framework.create_room('monopoly','human_first','human','human:1',opponent_id='ai-test',ordered_participants=seats)
if bound.get('confirmation_required'): bound=framework.respond_to_invitation(bound['room_id'],'ai','ai-test','accept')
r=invites.create_invite('monopoly','human','human:1',target_player_count=6,display_name='南杉')
for i in range(2,7): r=invites.join_invite(r['invite_code'],'human',f'human:{i}',display_name=f'朋友{i}')
with patch.object(invites.secrets,'SystemRandom') as secure:
 secure.return_value.shuffle.side_effect=lambda x:None
 r=invites.start_invite(r['room_id'],'human','human:1')
card=framework.create_room('monopoly','human_first','human','human:1',opponent_id='ai-test',ordered_participants=seats,require_confirmations=False)
card_state=card['board_state']
card_state['players'][0]['position']=4
card_state['_decks']['chance']=[6]+[i for i in range(16) if i!=6]
with database.write_transaction() as conn:
 conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(card_state),card['room_id']))
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
Path(os.environ['MONOPOLY_UI_READY']).write_text(json.dumps(dict(port=sock.getsockname()[1],bound=bound['room_id'],invite=r['room_id'],card=card['room_id'])))
from app.main import app
uvicorn.run(app,fd=sock.fileno(),log_level='error')
`;
 const child=spawn(path.join(root,'vendor/duel/.venv/bin/python'),['-c',code],{cwd:path.join(root,'vendor/duel'),env:{...process.env,DUEL_DB_PATH:path.join(temporary,'test.db'),MONOPOLY_UI_READY:ready},stdio:['ignore','pipe','pipe']});
 let log='';child.stderr.on('data',chunk=>{log+=chunk.toString();});
 const started=Date.now();
 try {
  while(!fs.existsSync(ready)){assert.equal(child.exitCode,null,log);assert.ok(Date.now()-started<15000,'temporary server starts');await new Promise(r=>setTimeout(r,100));}
  const config=JSON.parse(fs.readFileSync(ready,'utf8'));const origin=`http://127.0.0.1:${config.port}`;
  for(const kind of ['bound','invite','card']) {
   const page=await browser.newPage({viewport:{width:360,height:900},extraHTTPHeaders:{'X-Duel-Human-Player':'human:1','X-Duel-Human-Name':encodeURIComponent('南杉'),'X-Duel-Bound-Ais':Buffer.from(JSON.stringify([{id:'ai-test',name:'测试小机'}])).toString('base64url')}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{
    const request=route.request(),url=new URL(request.url());if(url.origin!==origin)return route.abort();
    // Match server.py's authenticated proxy injection. Browser code does not
    // choose an identity; only this isolated fixture has the trusted test ID.
    if(request.method()==='POST'&&/\/(move|messages)$/.test(url.pathname))return route.continue({postData:JSON.stringify({...request.postDataJSON(),player_id:'human:1'})});
    return route.continue();
   });
   // Wait for the isolated server's lifespan initialization to finish.
   let response;for(let retry=0;retry<40;retry++){try{response=await page.request.get(`${origin}/api/whoami`);if(response.ok())break;}catch(_){}await new Promise(r=>setTimeout(r,100));}
   assert.ok(response&&response.ok(),log);
   await page.goto(`${origin}/?room=${config[kind]}`);
   const waitMode=page.locator('#dismissWaitModeModalButton');
   await page.locator('[data-action="roll"]').waitFor({state:'visible'});if(await waitMode.isVisible())await waitMode.click();
   // The normal-room wait-mode modal is ancillary to gameplay; close via its
   // own cancel control when present, without changing renderer or API code.
   await page.locator('dialog[open]').evaluateAll(ds=>ds.forEach(d=>d.close()));
   assert.equal(await page.locator('.chat-hint').isVisible(),kind==='invite');
   const chatText=kind==='invite'?'邀请房普通留言':'普通房 @ai-test 留言仍然发送';
   await page.locator('#chatInput').fill(chatText);
   const [chatReply]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/messages')&&r.request().method()==='POST'),page.locator('#sendMessageButton').click()]);
   assert.equal(chatReply.status(),200,await chatReply.text());
   assert.ok((await chatReply.text()).includes(chatText),'normal chat remains in real room timeline');
   await page.waitForFunction(()=>document.querySelector('#chatInput').value==='');
   const moveResponse=()=>page.waitForResponse(r=>r.url().endsWith(`/api/rooms/${config[kind]}/move`)&&r.request().method()==='POST');
   let [result]=await Promise.all([moveResponse(),page.locator('[data-action="roll"]').click()]);assert.equal(result.status(),200,await result.text());
   if(kind==='card'){
    const data=await result.json();assert.equal(data.room.board_state.players.find(p=>p.player_id==='human:1').cash,1550);
    const event=page.locator('.monopoly-event-layer[data-kind="chance"]');await event.waitFor();assert.match(await event.textContent(),/现金 \+50/);
    assert.equal(data.room.board_state.last_card_events[0].deck,'chance');
    await event.locator('.monopoly-event-detail:not(.pending-result)').waitFor({state:'visible'});
    await page.screenshot({path:`${output}/live-card-360.png`});
    await event.waitFor({state:'detached'});
    await page.waitForResponse(r=>r.request().method()==='GET'&&r.url().includes(`/api/rooms/${config.card}`),{timeout:15000});
    assert.equal(await event.count(),0,'ordinary polling does not repeat the card');
    await page.reload();await page.locator('.monopoly-ring').waitFor();assert.equal(await event.count(),0);
    assert.match(await page.locator('.monopoly-card-history').textContent(),/现金 \+50/);
    assert.deepEqual(errors,[]);await page.close();continue;
   }
   await page.locator('[data-action="buy"]').waitFor({state:'visible'});
   [result]=await Promise.all([moveResponse(),page.locator('[data-action="buy"]').click()]);assert.equal(result.status(),200,await result.text());
   await page.locator('[data-action="end_turn"]').waitFor({state:'visible'});
   const data=await result.json();assert.equal(data.room.board_state.tiles[3].owner,'human:1');assert.ok(data.room.board_state.players.find(p=>p.player_id==='human:1').cash<1500);
   await screenshot(page,{path:`${output}/live-${kind}-purchase-360.png`,fullPage:true});
   [result]=await Promise.all([moveResponse(),page.locator('[data-action="end_turn"]').click()]);assert.equal(result.status(),200,await result.text());
   assert.notEqual((await result.json()).room.current_player_id,'human:1');
   await page.reload();await page.locator('.monopoly-ring').waitFor();assert.equal(await page.locator('[data-action="roll"]:enabled').count(),0,'reload preserves other actor turn');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);await page.close();
  }
  return {ordinary:true,invite:true,card:true,actions:['chat','roll','buy','end_turn'],reload:true};
 }finally{child.kill('SIGTERM');const force=setTimeout(()=>child.kill('SIGKILL'),3000);await new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve));clearTimeout(force);fs.rmSync(temporary,{recursive:true,force:true});}
}

(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const measurements=[],rows=[],dialogs=[],boardCards=[],motions=[],events=[],pendingTrades=[];
 try {
  for(const width of (process.env.DUEL_UI_LIVE_ONLY ? [] : [360,430,1280])) {
   const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<600});const errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{window.setInterval=()=>0;});
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());if(url.host!=='duel.test')return route.abort();
    if(url.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true}});
    const rel=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');const file=path.join(root,'vendor/duel/app/static',rel);
    if(!fs.existsSync(file))return route.fulfill({status:404,body:''});let body=fs.readFileSync(file);
    if(rel==='app.js')body=body.toString().replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/,'')+'\nwindow.run=(source)=>eval(source);';
    return route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
   });
   await page.goto('http://duel.test/');
   await page.evaluate(()=>window.run(`identity={human_player_id:'human:1',human_name:'南杉',machines:[],games:[],npc_provider:{available:true}};startRoomPolling=()=>{};window.sent=[];submitMove=async (move)=>{sent.push(move);return true;};`));
   await page.evaluate(async()=>{await window.DuelGameUI.load('monopoly');await window.DuelGameUI.load('aeroplane_chess');await window.DuelGameUI.load('uno');});
   const render=async r=>{await page.evaluate(f=>{window.fixture=f;window.run('room=null;renderGame(fixture,"",[]);hideWaitModeModal();closeResultModal();');},r);await page.waitForFunction(()=>!!document.querySelector('.monopoly-ring')&&getComputedStyle(document.querySelector('.monopoly-ring')).display==='grid');};
   const checkEmbeddedFeedback=async()=>{
    const notice=await page.locator('#gameMessage').evaluate(e=>({text:e.textContent,display:getComputedStyle(e).display,height:e.getBoundingClientRect().height}));
    assert.deepEqual(notice,{text:'',display:'none',height:0},`${width}: empty host notice occupies no space`);
    assert.equal(await page.getByText('现在轮到你行动',{exact:true}).count(),0);
    const between=await page.evaluate(()=>{
     const header=document.querySelector('#turn').closest('header'),stage=document.querySelector('#battleStage'),visible=[];
     for(let e=header.nextElementSibling;e&&e!==stage;e=e.nextElementSibling)if(e.getBoundingClientRect().height)visible.push(e.id);
     return visible;
    });
    assert.deepEqual(between,[],`${width}: battle stage follows turn header without a visible placeholder`);
   };
   const oneRow=async phase=>{
    const buttons=await page.locator('.monopoly-action-strip button').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {label:e.textContent,top:r.top,height:r.height,width:r.width};}));
    assert.ok(buttons.length,'action strip contains buttons');
    assert.ok(Math.max(...buttons.map(b=>b.top))-Math.min(...buttons.map(b=>b.top))<1,`${phase}/${width}: buttons share a single line`);
    assert.ok(buttons.every(b=>b.height>=44&&b.width>=44));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    rows.push({width,phase,buttons});
   };
   if (!process.env.DUEL_UI_EVENTS_ONLY && !process.env.DUEL_UI_MOTION_ONLY) {
    pendingTrades.push(...await checkPendingTrades(page,fixtures.rooms.map(base=>phaseRoom(base)),width,output,render));
    if(process.env.DUEL_UI_TRADES_ONLY){assert.deepEqual(errors,[]);await page.close();continue;}
   }
   if (process.env.DUEL_UI_EVENTS_ONLY) {
    events.push(...await checkMonopolyEvents(page,fixtures.rooms.map(base=>phaseRoom(base)),fixtures.card_cases,width,output,render));
    assert.deepEqual(errors,[]);await page.close();continue;
   }
   if (process.env.DUEL_UI_MOTION_ONLY) {
    for (const base of fixtures.rooms) motions.push(await checkMonopolyMotion(page,phaseRoom(base),width,output));
    assert.deepEqual(errors,[]);await page.close();continue;
   }
   // Ordinary rooms keep plain chat, even if the user types a literal @.
   for(const [key,ordinary] of Object.entries(fixtures.ordinary)){
    await page.evaluate(f=>{window.fixture=f;window.run('room=null;renderGame(fixture,"",[]);hideWaitModeModal();closeResultModal();');},ordinary);
    if(key==='monopoly'){
     await page.waitForFunction(()=>getComputedStyle(document.querySelector('.monopoly-ring')).display==='grid');await oneRow('ordinary-roll');
     await checkEmbeddedFeedback();
     assert.ok(await page.locator('.monopoly-center .monopoly-turn').isVisible());
     const sea=ordinary.board_state.tiles.find(t=>t.name==='海街');
     for(const [label,changes,rentLabel,rent] of [
      ['unowned',{},'购买后基础租金','26'],
      ['owned',{owner:'human:1',rent:52},'当前租金','52'],
      ['mortgaged',{owner:'human:1',mortgaged:true,rent:0},'当前收租','已抵押，暂停收租'],
     ]){
      const room=structuredClone(ordinary);Object.assign(room.board_state.tiles[sea.id],changes);
      await render(room);await page.locator(`[data-tile-id="${sea.id}"]`).click();
      const detail=page.locator('dialog[open]');
      assert.equal(await detail.locator('.monopoly-values dt').filter({hasText:rentLabel}).evaluate(e=>e.nextElementSibling.textContent),rent);
      assert.equal(await detail.locator('.monopoly-rents caption').textContent(),'租金');
      assert.deepEqual(await detail.locator('.monopoly-rents th').evaluateAll(es=>es.map(e=>e.firstChild.textContent)),['空地','1 级建筑','2 级建筑','3 级建筑','4 级建筑','旅馆']);
      assert.deepEqual(await detail.locator('.monopoly-rents td').allTextContents(),sea.rents.map(v=>v.toLocaleString('zh-CN')));
      const box=await detail.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);
      assert.equal(await detail.evaluate(e=>e.scrollWidth>e.clientWidth),false);
      await page.screenshot({path:`${output}/rent-${label}-${width}.png`});await page.keyboard.press('Escape');
     }
     await render(ordinary);
     const seat=page.locator('.monopoly-player').first();await seat.locator('.board-edge-avatar').click();
     assert.ok(await page.getByRole('dialog').isVisible());await page.getByRole('button',{name:'关闭对话框'}).click();
     await page.locator('.monopoly-controls').scrollIntoViewIfNeeded();await screenshot(page,{path:`${output}/ordinary-${width}.png`,fullPage:true});
    }else{
     assert.equal(await page.locator('#opponentRow:visible, #humanRow:visible').count(),2,`${key} keeps public player cards`);
     assert.equal(await page.locator('.monopoly-player').count(),0);
    }
    assert.equal(await page.locator('.chat-hint').isVisible(),false);
    assert.equal(await page.locator('#chatInput').getAttribute('role'),null);
    await page.locator('#chatInput').fill('@');assert.equal(await page.locator('#mentionOptions').isVisible(),false);
    assert.equal(await page.locator('#sendMessageButton').isEnabled(),true);
   }
   for(const base of fixtures.rooms){
    const r=phaseRoom(base),count=r.participants.length;
    if(fixtures.engine){await render(base);assert.equal(await page.locator('.monopoly-player').count(),count);assert.equal(await page.locator('.monopoly-tile').count(),40);await screenshot(page,{path:`${output}/opening-${count}-${width}.png`,fullPage:true});}
    await render(r);await oneRow(`manage-${count}`);
    await checkEmbeddedFeedback();
    assert.equal(await page.locator('.monopoly-player').count(),count);assert.equal(await page.locator('.monopoly-tile').count(),40);
    if(count===6)assert.match(await page.locator('.monopoly-player').last().textContent(),/你/);
    const dimensions=await page.evaluate(()=>({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,ring:document.querySelector('.monopoly-ring').getBoundingClientRect().toJSON(),font:getComputedStyle(document.querySelector('.monopoly-cash')).fontSize,seatFont:getComputedStyle(document.querySelector('.board-edge-copy strong')).fontSize}));
    assert.equal(dimensions.overflow,false,`${width}px/${count} players overflow`);assert.ok(dimensions.ring.x>=0&&dimensions.ring.right<=width);assert.ok(Math.abs(dimensions.ring.width-dimensions.ring.height)<1);
    assert.equal(await page.locator('#opponentRow:visible, #humanRow:visible, #roomParticipants:visible, #viewerParticipant:visible').count(),0,'only board edge seats are shown');
    assert.equal(await page.locator('.monopoly-player .board-edge-avatar').count(),count);
    assert.equal(await page.locator('.monopoly-player.current').count(),1);
    assert.equal(await page.locator('.monopoly-roster').count(),0,'old asset grid removed');
    assert.equal(await page.locator('.monopoly-action-panel').getByRole('button',{name:'提出交易',exact:true}).count(),1);
    assert.equal(await page.locator('.monopoly-action-panel').getByRole('button',{name:'地产与经营',exact:true}).count(),1);
    assert.equal(await page.getByText('现在轮到你落子',{exact:true}).count(),0);
    measurements.push({width,count,...dimensions});
    await page.locator('#board').scrollIntoViewIfNeeded();await screenshot(page,{path:`${output}/board-${count}-${width}.png`,fullPage:true});
    for(let i=0;i<count;i++){
     const card=page.locator('.monopoly-player').nth(i);const id=await card.getAttribute('data-player-id');
     if(i===0){await card.focus();await page.keyboard.press('Enter');}else await card.click();
     const detail=page.locator('dialog[open]');assert.match(await detail.textContent(),/现金.*总资产.*地产.*已抵押.*建筑/s);
     assert.equal(await detail.locator('.board-edge-avatar').count(),1);
     assert.equal(await detail.locator('.monopoly-asset').count(),r.board_state.tiles.filter(t=>t.owner===id).length);
     assert.ok(!/jail_cards|trade_options|guide|private_state/.test(await detail.textContent()));
     if(count===6&&id==='human:1'){
      await screenshot(page,{path:`${output}/player-detail-${width}.png`,fullPage:true});
      await detail.locator('.monopoly-asset').first().click();assert.ok(await page.locator('.monopoly-rents').isVisible());
     }
     await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.monopoly-dialog'));
     assert.equal(await card.evaluate(e=>e===document.activeElement),true,'detail and nested land restore seat focus');
    }
    assert.equal(await page.locator('.chat-hint').isVisible(),true);
    await page.locator('#chatInput').fill('@');assert.equal(await page.locator('#mentionOptions').isVisible(),true);
    assert.equal(await page.locator('#mentionOptions button').count(),r.participants.filter(p=>p.player_id!=='human:1'&&p.participant_kind!=='system_npc').length);
    if(count===6)await screenshot(page,{path:`${output}/invite-mentions-${width}.png`,fullPage:true});
    await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');assert.match(await page.locator('#chatInput').inputValue(),/^@\S+ $/);
    assert.equal(await page.locator('#mentionOptions').isVisible(),false);
    const tile=page.locator('[data-tile-id="1"]');await tile.click();assert.equal(await page.locator('dialog[open] .monopoly-rents tr').count(),6);
    const after=await page.locator('.monopoly-ring').boundingBox();assert.equal(after.width,dimensions.ring.width,'dialog preserves board geometry');
    const box=await page.locator('.monopoly-dialog').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);
    await screenshot(page,{path:`${output}/details-${count}-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.monopoly-dialog'));assert.equal(await page.locator('.monopoly-dialog').count(),0);assert.equal(await tile.evaluate(e=>e===document.activeElement),true,'dialog restores focus');
    await page.getByRole('button',{name:'地产与经营',exact:true}).click();
    assert.equal(await page.locator('.monopoly-dialog select').count(),0);
    const assetsTabs=page.getByRole('group',{name:'查看玩家'}).getByRole('button');
    assert.equal(await assetsTabs.count(),count);await assetsTabs.nth(1).click();
    assert.equal(await assetsTabs.nth(1).getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('.monopoly-asset-list .monopoly-asset').count(),1);
    if(count===6)await screenshot(page,{path:`${output}/assets-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'提出交易',exact:true}).click();await page.locator('input[name="give_cash"]').fill('35');await page.locator('.monopoly-trade-form input[type="checkbox"]').first().check();
    assert.equal(await page.locator('.monopoly-dialog select').count(),0);
    assert.equal(await page.locator('.monopoly-trade-columns legend').allTextContents().then(a=>a.join('/')),'我给出/希望对方给出');
    const partnerTabs=page.getByRole('group',{name:'交易对象'}).getByRole('button');
    assert.equal(await partnerTabs.count(),count-1);
    await page.locator('input[name="take_cash"]').fill('40');
    await page.locator('.monopoly-trade-columns fieldset').nth(1).locator('input[type="checkbox"]').check();
    assert.match(await page.locator('.monopoly-trade-preview').textContent(),/现金 35.*现金 40/);
    if(count>2){
     await partnerTabs.nth(1).click();
     assert.equal(await page.locator('input[name="take_cash"]').inputValue(),'0');
     assert.equal(await page.locator('input[name="take_cash"]').getAttribute('max'),'1550');
     assert.equal(await page.locator('input[name="give_cash"]').inputValue(),'35');
     assert.equal(await page.locator('.monopoly-trade-columns fieldset').first().locator('input:checked').count(),1);
     assert.equal(await page.locator('.monopoly-trade-columns fieldset').nth(1).locator('input:checked').count(),0);
     await partnerTabs.first().click();
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await screenshot(page,{path:`${output}/trade-${count}-${width}.png`,fullPage:true});await page.getByRole('button',{name:'确认条件并提出交易'}).click();
    await page.waitForFunction(()=>!document.querySelector('.monopoly-dialog'));
    const sent=await page.evaluate(()=>sent.at(-1));assert.equal(sent.action,'propose_trade');assert.equal(sent.give_cash,35);assert.deepEqual(sent.give_tiles,[1]);assert.equal(sent.action_seq,5);
    // Rapid repeat clicks cannot submit concurrently, and stale viewer cannot act.
    await page.evaluate(()=>window.run('submitMove=async move=>{sent.push(move);await new Promise(r=>setTimeout(r,80));return true;};'));
    const before=await page.evaluate(()=>sent.length);await page.locator('[data-action="end_turn"]').evaluate(e=>{e.click();e.click();});await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>sent.length),before+1);
    motions.push(await checkMonopolyMotion(page,phaseRoom(base),width,output));
   }
   const boardBase=structuredClone(fixtures.rooms[2]);
   boardBase.room_id=`board-card-acceptance-${width}`;
   boardBase.participants.sort((a,b)=>a.player_id.localeCompare(b.player_id));
   const boardSnapshot=(snapshot,revision=200)=>({...structuredClone(boardBase),revision,board_state:structuredClone(snapshot),current_player_id:snapshot.turn_player_id,current_actor:boardBase.participants.find(p=>p.player_id===snapshot.turn_player_id),private_state:{legal_actions:[],trade_options:{partners:[]}}});
   const boardOpening=fixtures.card_cases['chance-money'].before;
   await render(boardSnapshot(boardOpening));
   const specialKinds=await page.locator('.monopoly-tile:not(.kind-property)').evaluateAll(es=>es.map(e=>({kind:e.className,color:getComputedStyle(e).backgroundColor,icon:!!e.querySelector('svg')})));
   assert.ok(specialKinds.every(t=>t.icon));
   assert.notEqual(specialKinds.find(t=>t.kind.includes('kind-chance')).color,specialKinds.find(t=>t.kind.includes('kind-chest')).color);
   const badgeMap=await page.locator('.monopoly-player').evaluateAll(es=>es.map(e=>({id:e.dataset.playerId,number:e.querySelector('.monopoly-seat-badge').textContent})));
   assert.equal(new Set(badgeMap.map(p=>p.number)).size,6);
   const checkTokens=async label=>{
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(resolve)));
    const geometry=await page.evaluate(()=>{
     const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
     const overlap=(a,b)=>Math.min(a.right,b.right)-Math.max(a.x,b.x)>.5&&Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>.5;
     const tokens=[...document.querySelectorAll('.monopoly-tokens .monopoly-token')].map(e=>({...box(e),number:e.textContent,id:e.dataset.playerId,tile:box(e.closest('.monopoly-tile')),tileId:Number(e.closest('.monopoly-tile').dataset.tileId)}));
     const labels=[...document.querySelectorAll('.monopoly-tile-name, .monopoly-tile-icon, .monopoly-owner, .monopoly-buildings, .monopoly-center > *')].filter(e=>getComputedStyle(e).display!=='none').map(e=>({...box(e),label:e.textContent||e.closest('.monopoly-tile')?.dataset.tileId}));
     const ring=box(document.querySelector('.monopoly-ring'));
     return {tokens,labelsVisible:labels.every(l=>l.width>0&&l.height>0),overlaps:tokens.flatMap(t=>labels.filter(l=>overlap(t,l)).map(l=>({token:t,label:l}))),collisions:tokens.some((t,i)=>labels.some(l=>overlap(t,l))||tokens.slice(i+1).some(l=>overlap(t,l))),links:document.querySelectorAll('.monopoly-token-links').length, translated:[...document.querySelectorAll('.monopoly-tokens')].some(e=>getComputedStyle(e).translate!=='none'||getComputedStyle(e).transform!=='none'), inside:tokens.every(t=>t.x>=t.tile.x-1&&t.right<=t.tile.right+1&&t.y>=t.tile.y-1&&t.bottom<=t.tile.bottom+1),overflow:document.documentElement.scrollWidth>innerWidth};
    });
    assert.equal(geometry.collisions,false,`${label}/${width}: tokens do not cover names, symbols or each other: ${JSON.stringify(geometry.overlaps)}`);
    assert.equal(geometry.labelsVisible,true,`${label}/${width}: every name and symbol has visible dimensions`);
    assert.equal(geometry.inside,true,`${label}/${width}: all pawns stay inside their own tile`);assert.equal(geometry.overflow,false);assert.equal(geometry.links,0);assert.equal(geometry.translated,false);
    for(const token of geometry.tokens){assert.ok(token.width>=10&&token.width<=14);assert.equal(token.height,token.width);assert.equal(token.tileId,(await page.evaluate(()=>fixture.board_state.players)).find(p=>p.player_id===token.id).position);assert.equal(badgeMap.find(p=>p.id===token.id).number,token.number);}
    return geometry;
   };
   for (const count of [2,4,6]) {
    const scenarios=[['separate',[1,11,21,31,5,15]],['two-together',[1,1,21,31,5,15]],['four-together',[3,3,3,3,21,31]],['all-together',Array(count).fill(6)],['corners',[0,10,20,30,0,10]]];
    for (const [label,positions] of scenarios) {
     const r=boardSnapshot(boardOpening);r.board_state.players=r.board_state.players.slice(0,count);
     r.participants=r.participants.slice(0,count);r.board_state.players.forEach((p,i)=>{p.position=positions[i];});
     // Exercise ownership and every building level while players occupy land.
     r.board_state.tiles.filter(t=>t.kind==='property').forEach((t,i)=>{t.owner=r.board_state.players[i%count].player_id;t.level=i%5+1;});
     await render(r);const geometry=await checkTokens(`${count}-${label}`);boardCards.push({width,count,label,...geometry});
     assert.equal(geometry.tokens.length,count);
     assert.ok(await page.locator('.monopoly-seat-badge').evaluateAll(es=>es.every(e=>e.getBoundingClientRect().width===16&&e.getBoundingClientRect().height===16)));
     await page.locator('.monopoly-game').screenshot({path:`${output}/inside-${count}-${label}-${width}.png`});
    }
   }
   for(const [count,position,label] of [[6,0,'map'],[4,7,'four-together'],[6,6,'six-together']]){
    const r=boardSnapshot(boardOpening);r.board_state.players.forEach((p,i)=>{p.position=i<count?position:20+i;});
    await render(r);const geometry=await checkTokens(label);boardCards.push({width,label,...geometry});
    await page.locator('.monopoly-game').screenshot({path:`${output}/board-${label}-${width}.png`});
   }
   // Every edge and corner, plus adjacent occupied streets: contained pawns
   // must remain readable even on the narrowest cells, without obscuring names.
   for(const position of Array.from({length:40},(_,i)=>i)){
    const r=boardSnapshot(boardOpening);r.board_state.players.forEach(p=>{p.position=position;});const t=r.board_state.tiles[position];if(t.kind==='property'){t.owner=r.board_state.players[0].player_id;t.level=4;}await render(r);await checkTokens(`six-on-${position}`);
   }
   for(const position of [6,16,26,36]){
    const r=boardSnapshot(boardOpening);r.board_state.players.forEach((p,i)=>{p.position=position+(i<3?0:1);});await render(r);await checkTokens(`adjacent-${position}`);
   }
   for(const [a,b] of [[0,1],[9,10],[10,11],[19,20],[20,21],[29,30],[30,31],[39,0],[1,2],[8,9],[11,12],[18,19],[21,22],[28,29],[31,32],[38,39]]){
    const r=boardSnapshot(boardOpening);r.board_state.players.forEach((p,i)=>{p.position=i<4?a:b;});await render(r);await checkTokens(`corner-neighbors-${a}-${b}`);
    if(a===0&&b===1&&width===360)await page.locator(".monopoly-game").screenshot({path:`${output}/board-corner-neighbors-360.png`});
   }
   events.push(...await checkMonopolyEvents(page,fixtures.rooms.map(base=>phaseRoom(base)),fixtures.card_cases,width,output,render));
   // Full document reload: module memory is fresh and the latest server card is
   // history. The recent-card text stays available without an automatic popup.
   const reloadRoom=boardSnapshot(fixtures.card_cases['chest-jail-card'].after,400);
   await page.reload();
   await page.evaluate(()=>window.run(`identity={human_player_id:'human:1',human_name:'南杉',machines:[],games:[],npc_provider:{available:true}};startRoomPolling=()=>{};window.sent=[];submitMove=async move=>{sent.push(move);return true;};`));
   await page.evaluate(async()=>{await window.DuelGameUI.load('monopoly');await window.DuelGameUI.load('aeroplane_chess');await window.DuelGameUI.load('uno');});
   await render(reloadRoom);assert.equal(await page.locator('.monopoly-card-event').count(),0);
   assert.match(await page.locator('.monopoly-card-history').textContent(),/出狱卡已收入手中/);
   // Visual acceptance: six long names, empty/multiple estates, and both
   // sides of a cash/property proposal. Only public fixture data is displayed.
   const visual=phaseRoom(fixtures.rooms[2]);
   visual.board_state.bank_supply={houses:30,hotels:12};
   visual.participants.forEach(p=>{p.display_name=['南杉','许知衡和名字很长的朋友','乔麦与远道而来的朋友','山间听雨的朋友小林','名字特别长的第五位玩家','系统小机六号'][Number(p.player_id.split(':')[1])-1];});
   for(const [id,label,owner,level] of [[1,'溪街','human:1',0],[3,'月街','human:1',2],[5,'山街','human:1',0],[6,'松林路','human:2',0],[8,'竹影巷','human:2',0],[9,'长长的樱花小路','human:2',0]]){
    Object.assign(visual.board_state.tiles[id],{name:label,owner,kind:'property',level,rent:[6,30,90][level],mortgaged:id===6});
   }
   visual.private_state.trade_options.give_tiles=[1,5];
   visual.private_state.trade_options.take_tiles_by_player['human:2']=[6,8,9];
   await render(visual);
   const captureDialog=async label=>{
    const metrics=await page.locator('dialog[open]').evaluate(d=>{
     const b=d.getBoundingClientRect(),font=s=>parseFloat(getComputedStyle(d.querySelector(s)).fontSize);
     const tabs=[...d.querySelectorAll('.monopoly-player-tabs button')].map(e=>({height:e.getBoundingClientRect().height,width:e.getBoundingClientRect().width,title:e.title}));
     return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,overflow:d.scrollWidth>d.clientWidth,titleFont:font('h3'),wealthFont:d.querySelector('.monopoly-player-wealth dd')?font('.monopoly-player-wealth dd'):null,helpFont:d.querySelector('.monopoly-help')?font('.monopoly-help'):null,tabs};
    });
    assert.ok(metrics.left>=0&&metrics.right<=width&&metrics.top>=0&&metrics.bottom<=900);
    assert.equal(metrics.overflow,false,`${label}/${width}: no modal horizontal overflow`);
    if(metrics.helpFont)assert.ok(metrics.titleFont>metrics.helpFont);
    if(metrics.wealthFont)assert.ok(metrics.titleFont>metrics.wealthFont&&metrics.wealthFont>metrics.helpFont);
    assert.ok(metrics.tabs.every(t=>t.height===44&&t.width<=104&&t.title));
    dialogs.push({width,label,...metrics});
    if(width===360||['player','trade-cash'].includes(label))await page.screenshot({path:`${output}/polish-${label}-${width}.png`});
   };
   await page.locator('.monopoly-player[data-player-id="human:1"] .board-edge-avatar').click();
   assert.equal(await page.locator('.monopoly-player-wealth dd').allTextContents().then(t=>t.join('/')),'1,500/1,900');
   assert.equal(await page.locator('.monopoly-asset').count(),3);
   await captureDialog('player');await page.getByRole('button',{name:'关闭对话框'}).click();
   await page.locator('.monopoly-player[data-player-id="human:3"]').click();
   assert.equal(await page.locator('.monopoly-empty').textContent(),'暂无地产。');
   await captureDialog('player-empty');await page.keyboard.press('Escape');
   await page.locator('[data-tile-id="1"]').click();
   assert.equal(await page.locator('.monopoly-rents tr.current').count(),1);
   assert.match(await page.locator('.monopoly-rents tr.current').textContent(),/空地.*当前档位.*6/);
   assert.equal(await page.locator('.monopoly-values dd').allTextContents().then(t=>t.join('/')),'120/6/50/60/66');
   await captureDialog('land');await page.keyboard.press('Escape');
   await page.locator('[data-tile-id="3"]').click();
   assert.match(await page.locator('.monopoly-rents tr.current').textContent(),/2 级建筑.*当前档位.*90/);
   await page.keyboard.press('Escape');
   await page.getByRole('button',{name:'地产与经营',exact:true}).click();
   const tabs=page.getByRole('group',{name:'查看玩家'}).getByRole('button');
   assert.equal(await tabs.count(),6);await tabs.nth(1).click();
   assert.equal(await page.locator('.monopoly-asset').count(),3);await captureDialog('assets');
   await tabs.last().click();assert.equal(await page.locator('.monopoly-empty').count(),1);
   assert.equal(await tabs.last().getAttribute('aria-pressed'),'true');await captureDialog('assets-empty');
   await page.keyboard.press('Escape');
   await page.getByRole('button',{name:'提出交易',exact:true}).click();
   assert.match(await page.locator('.monopoly-trade-preview').textContent(),/你：不付现金、无地产.*许知衡.*不付现金、无地产/);
   const columns=page.locator('.monopoly-trade-columns fieldset');
   await columns.nth(1).locator('input[type="checkbox"]').nth(0).check();
   await columns.nth(1).locator('input[type="checkbox"]').nth(1).check();
   await columns.nth(1).locator('input[type="checkbox"]').nth(2).check();
   await captureDialog('trade-property');
   await page.locator('input[name="give_cash"]').fill('240');await page.locator('input[name="take_cash"]').fill('60');
   await columns.first().locator('input[type="checkbox"]').nth(0).check();
   await columns.first().locator('input[type="checkbox"]').nth(1).check();
   assert.match(await page.locator('.monopoly-trade-preview').textContent(),/你：现金 240、溪街 \/ 山街.*现金 60、松林路 \/ 竹影巷 \/ 长长的樱花小路/);
   await captureDialog('trade-cash');
   await page.getByRole('group',{name:'交易对象'}).getByRole('button').last().click();
   assert.equal(await page.locator('input[name="take_cash"]').inputValue(),'0');
   assert.equal(await page.locator('input[name="give_cash"]').inputValue(),'240');
   assert.equal(await columns.first().locator('input:checked').count(),2);
   assert.equal(await columns.nth(1).locator('input:checked').count(),0);
   await page.keyboard.press('Escape');await page.locator('.monopoly-dialog').waitFor({state:'detached'});assert.equal(await page.locator('.monopoly-dialog').count(),0);
   for(const phase of ['purchase','auction','trade','debt','finished']){
    await render(phaseRoom(fixtures.rooms[2],phase));await page.locator('.monopoly-controls').scrollIntoViewIfNeeded();await oneRow(phase);
    if(phase!=='finished')await checkEmbeddedFeedback();
    if(phase==='purchase'){await page.locator('[data-action="buy"]').click();assert.equal(await page.evaluate(()=>sent.at(-1).action),'buy');}
    if(phase==='auction'){await page.locator('.monopoly-bid-form input').fill('125');await page.getByRole('button',{name:'确认出价'}).click();assert.equal(await page.evaluate(()=>sent.at(-1).amount),125);}
    if(phase==='trade'){await page.getByRole('button',{name:'接受交易'}).click();assert.equal(await page.evaluate(()=>sent.at(-1).accept),true);await page.waitForTimeout(100);await page.getByRole('button',{name:'拒绝交易'}).click();assert.equal(await page.evaluate(()=>sent.at(-1).accept),false);}
 if(phase==='finished'){assert.equal(await page.locator('.monopoly-player.bankrupt').count(),5);assert.equal(await page.locator('.monopoly-controls [data-action]').count(),0);}
    if(phase==='debt'){await page.locator('[data-action="bankrupt"]').click();assert.equal(await page.getByRole('button',{name:'确认破产并离场'}).count(),1);await page.keyboard.press('Escape');}
    await screenshot(page,{path:`${output}/${phase}-${width}.png`,fullPage:true});
   }
   // Switching games removes scoped monopoly layout/classes and overlays.
   await page.evaluate(f=>{window.fixture=f;window.run('room=null;renderGame(fixture,"",[]);');},fixtures.aeroplane);
   assert.equal(await page.locator('.board.monopoly').count(),0);assert.equal(await page.locator('.monopoly-dialog').count(),0);assert.equal(await page.locator('.aeroplane-game').count(),1);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await screenshot(page,{path:`${output}/aeroplane-regression-${width}.png`,fullPage:true});
   assert.deepEqual(errors,[]);await page.close();
  }
  const live=fixtures.engine&&!process.env.DUEL_UI_MOTION_ONLY&&!process.env.DUEL_UI_EVENTS_ONLY&&!process.env.DUEL_UI_TRADES_ONLY?await checkLiveBrowser(browser):null;
  fs.writeFileSync(path.join(output,'measurements.json'),JSON.stringify({realMonopolyOpenings:fixtures.engine,live,measurements,rows,dialogs,boardCards,motions,events,pendingTrades},null,2));
  console.log(JSON.stringify({ok:true,realMonopolyOpenings:fixtures.engine,screenshots:output,checks:measurements.length,motionChecks:motions.length,eventChecks:events.length,pendingTradeChecks:pendingTrades.length,live}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
