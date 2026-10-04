/* DOM integration only: no claim of rendered geometry. Real app + engine/SQLite. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts/bomb_plane');
fs.mkdirSync(out,{recursive:true});const tmp=fs.mkdtempSync(path.join(out,'dom-'));
const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:path.join(root,'vendor/duel'),TMPDIR:tmp};
let n=0;
function backend(input){
 if(input.action==='seed')env.DUEL_DB_PATH=path.join(tmp,`${++n}.db`);
 const files=['request.json','response.json','error.log'].map(f=>path.join(tmp,f));fs.writeFileSync(files[0],JSON.stringify(input));
 const fds=[fs.openSync(files[0],'r'),fs.openSync(files[1],'w'),fs.openSync(files[2],'w')];let r;
 try{r=spawnSync(process.env.DUEL_TEST_PYTHON||'python3',[path.join(root,'vendor/duel/tests/bomb_plane_ui_fixture.py')],{stdio:fds,env,cwd:root,timeout:15000});}finally{fds.forEach(fd=>fs.closeSync(fd));}
 assert.equal(r.status,0,fs.readFileSync(files[2],'utf8'));return JSON.parse(fs.readFileSync(files[1],'utf8'));
}
const dom=new JSDOM(fs.readFileSync(path.join(root,'vendor/duel/app/static/index.html'),'utf8'),{url:'http://bomb-plane.test/',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,doc=w.document,posts=[];let viewer='p0',failure=false,releaseRequest=null,holdRequest=false;
// Deterministic clock for the plugin's two-second presentation delay only.
let clock=0,timerId=0;const presentationTimers=new Map();
const nativeTimeout=w.setTimeout.bind(w),nativeClear=w.clearTimeout.bind(w);
w.setTimeout=(fn,ms,...args)=>{
 if(ms!==2000)return nativeTimeout(fn,ms,...args);
 const id=`presentation-${++timerId}`;presentationTimers.set(id,{at:clock+ms,run:()=>fn(...args)});return id;
};
w.clearTimeout=id=>{if(!presentationTimers.delete(id))nativeClear(id);};
async function tick(ms){clock+=ms;await Promise.resolve();for(const [id,timer] of [...presentationTimers])if(timer.at<=clock){presentationTimers.delete(id);timer.run();}await flush();}
w.fetch=async(url,options={})=>{
 if(options.method==='POST'&&url.includes('/move')){
  const body=JSON.parse(options.body);posts.push(body);
  if(holdRequest) await new Promise(resolve=>{releaseRequest=resolve;});
  const data=failure?{ok:false,message:'模拟网络失败'}:backend({action:'move',viewer,room_id:url.split('/')[3],...body});
  return {ok:data.ok,status:data.status||400,json:async()=>data};
 }
 return {ok:true,json:async()=>({ok:true,bound:false,notifications:[]})};
};
w.HTMLElement.prototype.scrollIntoView=()=>{};w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
for(const file of ['styles.css','games/bomb_plane.css']){const s=doc.createElement('style');s.textContent=fs.readFileSync(path.join(root,'vendor/duel/app/static',file),'utf8');doc.head.append(s);assert.ok(s.sheet);}
for(const file of ['game_ui_registry.js','games/bomb_plane.js'])w.eval(fs.readFileSync(path.join(root,'vendor/duel/app/static',file),'utf8'));
const app=fs.readFileSync(path.join(root,'vendor/duel/app/static/app.js'),'utf8');
w.eval(app.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/,'')+'\nwindow.run=source=>eval(source);');
w.run("startRoomPolling=()=>{};identity={human_player_id:'p0',human_name:'玩家零',machines:[],games:[]};");
function show(room){w.fixture=room;w.run(`identity.human_player_id='${viewer}';renderGame(fixture,'',[]);renderBoard([]);`);}
const key=k=>doc.querySelector(`[data-bp-key="${k}"]`);
function click(k){assert.ok(key(k),k);assert.equal(key(k).disabled,false,`${k} enabled`);key(k).click();}
const flush=()=>new Promise(r=>setImmediate(r));
// Allow bounded runs on hosts that terminate long browser/DOM checks.
const suite=process.env.DUEL_BOMB_PLANE_FOCUSED?'focused':(process.env.DUEL_BOMB_PLANE_SUITE||'all');
assert.ok(['all','ordinary','invite','focused'].includes(suite));
(async()=>{try{
 for(const invited of suite==='focused'?[]:suite==='ordinary'?[false]:suite==='invite'?[true]:[false,true]){
  viewer='p0';const seeded=backend({action:'seed',invited});let r=seeded.room;w.catalog=seeded.games;
  w.run("identity.games=catalog;document.querySelector('#gameCategory').value='board';syncGameTypeOptions(catalog);document.querySelector('#inviteCategory').value='board';syncInviteGameOptions();");
  for(const id of ['gameType','inviteGame'])assert.ok([...doc.querySelector('#'+id).options].some(o=>o.value==='bomb_plane'));
  w.run("document.querySelector('#gameType').value='bomb_plane';updateGameTokenEstimate();");assert.match(doc.querySelector('#gameTokenEstimate').title,/不是(?:实际计费|计费上限)/);
  show(r);const id=r.room_id;
  const refresh=()=>{r=backend({action:'state',room_id:id,viewer}).room;show(r);};
  const move=async k=>{const rev=r.revision;click(k);await flush();await flush();const rendered=doc.querySelector('.bp-game').textContent;refresh();assert.equal(doc.querySelector('.bp-game').textContent,rendered,`${k} renders without reload`);assert.equal(r.revision,rev+1);assert.equal(posts.at(-1).revision,rev);};
  assert.equal(doc.querySelectorAll('.bp-cell').length,100);assert.equal(key('ready'),null);
  assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(key('attack').disabled,true);
  assert.match(doc.querySelector('.bp-status').textContent,/已放置 0\/3 架（可重叠）/);
  assert.equal(doc.querySelectorAll('.bp-status').length,1);assert.equal(doc.querySelector('#gameMessage').textContent,'');
  assert.equal(doc.querySelector('.bp-selection'),null);assert.equal(key('place').disabled,true);
  assert.equal(doc.querySelector('.bp-steps, .bp-directions, #bp-shuffle-help'),null);
  assert.doesNotMatch(doc.querySelector('.bp-game').textContent+doc.querySelector('.bp-controls').textContent,/①|②|③|共放3架|仅生成布阵|先点棋盘|确认放置/);
  assert.equal(key('undo').disabled,true);assert.equal(key('clear').disabled,true);
  assert.equal(doc.querySelector('.bp-legend'),null);
  assert.equal(w.getComputedStyle(doc.querySelector('.bp-grid')).display,'grid');assert.equal(w.getComputedStyle(key('rotate')).minHeight,'44px');
  assert.ok(doc.querySelector('#privateStatePanel').classList.contains('hidden'));
  viewer='p1';refresh();assert.equal(doc.querySelectorAll('.bp-cell:enabled').length,100);assert.equal(key('shuffle').disabled,false);assert.equal(key('rotate').disabled,false);assert.match(doc.querySelector('.bp-status').textContent,/已放置 0\/3 架/);
  click('C1');await move('place');assert.deepEqual(r.private_state.planes,[{head:'C1',direction:'N'}]);
  assert.equal(r.current_player_id,'p0');await move('undo');
  viewer='p0';refresh();assert.equal(doc.querySelectorAll('.bp-plane').length,0);
  const roster=doc.querySelector('#roomParticipants').textContent;
  const before=posts.length;click('E5');
  for(const direction of ['右','下','左','上']){click('rotate');assert.equal(doc.querySelectorAll('.bp-preview').length,10);assert.equal(key('rotate').getAttribute('aria-label'),`旋转，当前朝${direction}`);assert.equal(doc.activeElement.dataset.bpKey,'rotate');}
  click('rotate');assert.equal(doc.querySelectorAll('.bp-preview').length,10);assert.equal(posts.length,before);
  assert.equal(doc.querySelector('#roomParticipants').textContent,roster);
  failure=true;holdRequest=true;key('place').focus();const preRequest=posts.length;click('place');
  assert.match(doc.querySelector('.bp-status').textContent,/提交中/);assert.equal(key('place').disabled,true);assert.equal(key('shuffle').disabled,true);assert.equal(doc.querySelectorAll('.bp-cell:enabled').length,0);
  key('place').click();assert.equal(posts.length,preRequest+1);releaseRequest();holdRequest=false;await flush();await flush();assert.equal(doc.activeElement.dataset.bpKey,'place');assert.match(doc.querySelector('#gameMessage').textContent,/模拟网络失败/);assert.equal(key('place').disabled,false);assert.equal(doc.querySelectorAll('.bp-preview').length,10);failure=false;
  await move('place');assert.deepEqual(r.private_state.planes,[{head:'E5',direction:'E'}]);assert.equal(doc.querySelectorAll('.bp-plane').length,10);
  assert.equal(doc.querySelectorAll('.bp-fleet li').length,1);assert.match(doc.querySelector('.bp-fleet').textContent,/飞机1 E5 →/);
  click('E5');assert.match(doc.querySelector('.bp-error').textContent,/机头不能重合/);assert.equal(key('place').disabled,true);
  click('D5');assert.equal(key('place').disabled,false);assert.equal(doc.querySelectorAll('.bp-preview').length,10);await move('place');
  assert.equal(doc.querySelectorAll('.bp-fleet li').length,2);assert.equal(key('D5').textContent,'↑');
  click('E4');assert.equal(key('place').disabled,false);await move('place');
  assert.equal(doc.querySelectorAll('.bp-fleet li').length,3);assert.equal(key('E5').textContent,'→');assert.match(key('E5').getAttribute('aria-label'),/飞机1机头朝右/);
  assert.ok(doc.querySelectorAll('.bp-plane').length<30);await move('undo');await move('undo');
  await move('undo');click('A1');assert.equal(key('place').disabled,true);assert.match(doc.querySelector('.bp-error').textContent,/飞机出界/);
  await move('shuffle');assert.equal(r.private_state.planes.length,3);
  assert.ok(doc.querySelectorAll('.bp-plane').length<=30);assert.equal(doc.querySelectorAll('.bp-fleet li').length,3);
  assert.equal(new Set(r.private_state.planes.map(p=>p.head)).size,3);
  assert.equal(r.board_state.ready.p0,false);assert.equal(key('ready').disabled,false);assert.equal(key('place'),null);assert.equal(key('rotate'),null);
  await move('shuffle');assert.equal(r.private_state.planes.length,3);assert.equal(r.board_state.ready.p0,false);
  await move('clear');assert.equal(doc.querySelectorAll('.bp-plane').length,0);assert.equal(doc.querySelector('.bp-fleet'),null);assert.equal(doc.querySelector('.bp-error'),null);assert.equal(key('place').disabled,true);
  for(const [i,cell] of ['C1','H1','C6'].entries()){click(cell);await move('place');assert.equal(doc.querySelectorAll('.bp-fleet li').length,i+1);assert.equal(doc.querySelectorAll('.bp-plane').length,(i+1)*10);}
  assert.equal(key('ready').textContent,'完成布阵');assert.equal(doc.querySelectorAll('.bp-cell:enabled').length,0);
  await move('undo');assert.equal(doc.querySelectorAll('.bp-fleet li').length,2);click('C6');await move('place');
  await move('ready');assert.equal(key('shuffle'),null);assert.equal(key('place'),null);assert.equal(key('attack').disabled,true);assert.match(doc.querySelector('.bp-status').textContent,/等待对方/);
  viewer='p1';refresh();assert.equal(doc.querySelectorAll('.bp-plane').length,0);assert.equal(doc.querySelector('.bp-fleet'),null);
  viewer='p0';r=backend({action:'opponent',room_id:id}).room;show(r);
  assert.equal(key('attack').getAttribute('aria-pressed'),'true');assert.equal(doc.querySelectorAll('.bp-plane').length,0);
  assert.equal(key('shuffle'),null);assert.equal(key('rotate'),null);assert.equal(key('ready'),null);assert.equal(key('place'),null);
  assert.equal(doc.querySelector('.bp-instruction'),null);assert.match(doc.querySelector('.bp-legend').textContent,/空 = 未命中/);
  for(const [cell,result] of [['J10','miss'],['C1','head'],['C2','hit']]){
   click('attack');
   click(cell);await move('attack-confirm');assert.equal(r.board_state.shots.p0.at(-1).result,result);
   assert.equal(key('attack').getAttribute('aria-pressed'),'true');
   assert.equal(doc.querySelector('.bp-latest-outgoing').dataset.cell,cell);await tick(1999);
   assert.equal(key('attack').getAttribute('aria-pressed'),'true');await tick(1);
   assert.equal(key('own').getAttribute('aria-pressed'),'true');
   r=backend({action:'opponent',room_id:id}).room;show(r);
   assert.equal(key('own').getAttribute('aria-pressed'),'true');await tick(2000);
   assert.equal(key('attack').getAttribute('aria-pressed'),'true');
  }
  click('attack');
  assert.equal(key('C1').disabled,true);assert.equal(doc.querySelectorAll('.bp-plane').length,0);
  click('own');assert.equal(doc.querySelectorAll('.bp-plane').length,30);assert.equal(key('attack-confirm'),null);
  r=backend({action:'finish',room_id:id}).room;show(r);assert.equal(doc.querySelectorAll('.bp-plane').length,30);
  assert.equal(key('attack-confirm'),null);assert.equal(doc.querySelectorAll('.bp-cell:enabled').length,0);
  assert.equal(key('ready'),null);assert.equal(key('shuffle'),null);assert.equal(doc.querySelector('.bp-placement'),null);
  click('own');assert.equal(doc.querySelectorAll('.bp-plane').length,30);
  console.log('PASS',invited?'invite':'ordinary','compact UI / rotate cycle / full preview / body overlap / unique heads / simultaneous setup / 1-2-3 planes / shuffle-undo-clear-ready / private viewers / shots / terminal / network retry');
 }
 if(suite==='ordinary'||suite==='invite')return;
 // Regression: actual framework projections across revisions, without reloads.
 viewer='p0';let r=backend({action:'seed'}).room;const id=r.room_id,db=env.DUEL_DB_PATH;
 const project=()=>{r=backend({action:'state',room_id:id,viewer}).room;show(r);};
 const engineMove=(pid,move)=>{const result=backend({action:'move',room_id:id,viewer:pid,revision:r.revision,move});assert.equal(result.ok,true,result.message);project();};
 show(r);click('E3');assert.equal(key('E3').textContent,'↑');assert.ok(key('E3').classList.contains('bp-plane-head'));
 engineMove('p0',{action:'set_layout',planes:[{head:'E3',direction:'N'},{head:'H2',direction:'N'},{head:'C6',direction:'N'}]});
 assert.equal(doc.querySelectorAll('.bp-plane-head').length,3);assert.ok(!key('E4').classList.contains('bp-plane-head'));
 engineMove('p0',{action:'ready'});r=backend({action:'opponent',room_id:id}).room;show(r);
 assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 const attack=async (cell,dwell=true)=>{click('attack');click(cell);click('attack-confirm');await flush();await flush();project();if(dwell)await tick(2000);};
 // Failed attack retains view and selection; committed attack changes view.
 failure=true;await attack('G3');assert.equal(key('attack').getAttribute('aria-pressed'),'true');assert.equal(key('attack-confirm').textContent,'确认攻击 G3');failure=false;
 await attack('G3',false);assert.equal(r.board_state.shots.p0.at(-1).result,'miss');
 assert.equal(key('attack').getAttribute('aria-pressed'),'true');assert.equal(key('G3').textContent,'空');
 assert.equal(doc.querySelector('.bp-latest-outgoing').dataset.cell,'G3');
 const outgoingScheduled=timerId;await tick(1000);project();w.run('renderBoard([])');await tick(999);
 assert.equal(key('attack').getAttribute('aria-pressed'),'true');assert.equal(timerId,outgoingScheduled);await tick(1);
 assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.match(doc.querySelector('.bp-status').textContent,/等待对方攻击/);
 assert.ok(!key('G3').classList.contains('bp-miss'),'own board excludes own attacks');
 click('attack');project();assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 engineMove('p1',{action:'attack',cell:'E5'});
 assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(presentationTimers.size,1);
 assert.equal(doc.querySelector('.bp-incoming').textContent,'对方刚攻击 E5 · 伤');
 click('own');assert.equal(presentationTimers.size,0);await tick(2100);project();project();key('own').focus();w.run('renderBoard([])');
 assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(key('E5').textContent,'伤');
 show({...r,revision:r.revision+1});assert.equal(key('own').getAttribute('aria-pressed'),'true');project();
 assert.ok(key('E5').classList.contains('bp-latest-incoming'));
 click('attack');project();project();assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 assert.equal(key('G3').textContent,'空');assert.equal(key('E5').textContent,'');assert.equal(doc.querySelectorAll('.bp-plane-head, .bp-latest-incoming').length,0);
 // Even an unrelated revision must preserve the manual view with the same shots.
 show({...r,revision:r.revision+1});assert.equal(key('attack').getAttribute('aria-pressed'),'true');project();
 await attack('J10',false);assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 click('attack');assert.equal(presentationTimers.size,0);await tick(2100);project();project();assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 show({...r,revision:r.revision+1});assert.equal(key('attack').getAttribute('aria-pressed'),'true');project();
 click('own');engineMove('p1',{action:'attack',cell:'E4'});
 assert.equal(key('own').getAttribute('aria-pressed'),'true');
 assert.equal(doc.querySelector('.bp-incoming').textContent,'对方刚攻击 E4 · 伤');
 click('attack');assert.equal(presentationTimers.size,0);await tick(2100);project();assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 click('own');project();assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(key('E4').textContent,'伤');assert.equal(key('E5').textContent,'伤');
 assert.equal(doc.querySelectorAll('.bp-latest-incoming').length,1);assert.ok(key('E4').classList.contains('bp-latest-incoming'));
 assert.ok(!key('E5').classList.contains('bp-latest-incoming'));
 for(const width of [360,430,1280]){
  // JSDOM verifies DOM/state at these viewport inputs, not rendered geometry.
  w.innerWidth=width;doc.documentElement.style.width=`${width}px`;w.dispatchEvent(new w.Event('resize'));project();
  assert.equal(doc.querySelectorAll('.bp-cell').length,100);assert.equal(key('own').getAttribute('aria-pressed'),'true');
  assert.equal(doc.querySelector('.bp-incoming').textContent,'对方刚攻击 E4 · 伤');
  assert.equal(doc.querySelector('.bp-latest-incoming').dataset.cell,'E4');
  assert.equal(w.getComputedStyle(doc.querySelector('.bp-grid')).display,'grid');
  assert.equal(w.getComputedStyle(doc.querySelector('.bp-incoming')).fontSize,'12px');
  assert.equal(w.getComputedStyle(key('E3')).backgroundColor,'rgb(255, 199, 181)');
  assert.equal(w.getComputedStyle(key('E6')).backgroundColor,'rgb(229, 221, 240)');
  console.log('PASS viewport DOM',width,'(layout requires browser)');
 }
 // Separate player memory, and terminal-only reveal even if extra data is supplied.
 click('attack');show({...r,board_state:{...r.board_state,revealed_planes:{p1:[{head:'C1',direction:'N'}]}}});
 assert.equal(doc.querySelectorAll('.bp-plane-head').length,0);
 viewer='p1';project();assert.equal(key('own').getAttribute('aria-pressed'),'true');viewer='p0';project();assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 // B starts on its own default, A restores its manual view and consumed count.
 const other=backend({action:'seed'}).room;show(other);assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(doc.querySelector('.bp-incoming'),null);
 env.DUEL_DB_PATH=db;project();assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 await attack('I10',false);assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 const beforeReply=timerId;await tick(500);engineMove('p1',{action:'attack',cell:'E6'});
 assert.equal(key('attack').getAttribute('aria-pressed'),'true');assert.equal(key('I10').textContent,'空');
 assert.equal(timerId,beforeReply,'fast reply preserves outgoing deadline');assert.equal(presentationTimers.size,1);
 await tick(1499);project();assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 await tick(1);assert.equal(timerId,beforeReply+1,'incoming gets its own full dwell after outgoing');
 assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(doc.querySelector('.bp-incoming').textContent,'对方刚攻击 E6 · 伤');
 assert.equal(w.getComputedStyle(key('E6')).backgroundColor,'rgb(255, 176, 0)');
 assert.equal(presentationTimers.size,1);const scheduled=timerId;await tick(1000);project();w.run('renderBoard([])');await tick(999);
 assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(timerId,scheduled,'polls never restart the timer');
 await tick(1);assert.equal(key('attack').getAttribute('aria-pressed'),'true');assert.equal(presentationTimers.size,0);
 click('own');project();show({...r,revision:r.revision+1});
 assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(doc.querySelector('.bp-latest-incoming').dataset.cell,'E6');project();
 // Projection-only edge case: a newly delivered shot while the turn stays away.
 // Backend rules always exchange turns; these snapshots isolate UI event priority.
 const held={...r,room_id:'HELD-TURN',current_player_id:'p1',revision:0,board_state:{...r.board_state,shots:{...r.board_state.shots,p1:r.board_state.shots.p1.slice(0,-1)}}};
 show(held);click('attack');
 const delivered={...held,revision:1,board_state:r.board_state};show(delivered);
 assert.equal(key('own').getAttribute('aria-pressed'),'true');assert.equal(doc.querySelector('.bp-latest-incoming').dataset.cell,'E6');
 click('attack');show(delivered);show({...delivered,revision:2});assert.equal(key('attack').getAttribute('aria-pressed'),'true');
 click('own');show({...delivered,revision:3,current_player_id:'p0'});assert.equal(key('attack').getAttribute('aria-pressed'),'true','turn transition alone is sufficient');
 click('own');show({...delivered,revision:4,current_player_id:'p0'});assert.equal(key('own').getAttribute('aria-pressed'),'true');project();
 await attack('H10');engineMove('p1',{action:'attack',cell:'E3'});assert.equal(key('own').getAttribute('aria-pressed'),'true');click('own');
 assert.equal(key('E3').textContent,'头');assert.ok(key('E3').classList.contains('bp-head'));assert.ok(key('E3').classList.contains('bp-plane-head'));
 assert.equal(w.getComputedStyle(key('E3')).backgroundColor,'rgb(213, 43, 32)');
 await attack('G10');engineMove('p1',{action:'attack',cell:'J10'});click('own');
 assert.equal(key('J10').textContent,'空');assert.equal(w.getComputedStyle(key('J10')).backgroundColor,'rgb(8, 123, 158)');
 await attack('F10');engineMove('p1',{action:'attack',cell:'H5'});click('own');
 assert.equal(key('H5').textContent,'伤');assert.equal(doc.querySelector('.bp-latest-incoming').dataset.cell,'H5');
 for(const cell of ['E3','E4','E5','E6','J10','H5']){
  const style=w.getComputedStyle(key(cell));assert.equal(style.fontSize,'15px');assert.ok(Number(style.fontWeight)>=800);
  assert.equal(style.borderTopWidth,'2px');assert.equal(style.borderTopStyle,'solid');
 }
 assert.equal(w.getComputedStyle(key('E7')).fontSize,'12px');
 // JSDOM preserves the outline shorthand but does not expand its longhands.
 assert.match(w.getComputedStyle(key('H5')).outline,/^4px solid /);assert.ok(!key('E5').classList.contains('bp-latest-incoming'));
 assert.equal(w.getComputedStyle(key('H5')).backgroundColor,'rgb(255, 176, 0)');
 // Both tabs cancel outgoing, including an already queued fast response.
 for(const queued of [false,true])for(const tab of ['own','attack']){
  const before={...r,room_id:`TAKEOVER-${queued}-${tab}`,revision:0,current_player_id:'p0',board_state:{...r.board_state,shots:{...r.board_state.shots,p0:r.board_state.shots.p0.slice(0,-1),p1:r.board_state.shots.p1.slice(0,-1)}}};
  const sent={...before,revision:1,current_player_id:'p1',board_state:{...r.board_state,shots:{...r.board_state.shots,p1:r.board_state.shots.p1.slice(0,-1)}}};
  show(before);show(sent);assert.equal(presentationTimers.size,1);
  const latest=queued?{...sent,revision:2,current_player_id:'p0',board_state:r.board_state}:sent;
  if(queued)show(latest);
  click(tab);assert.equal(presentationTimers.size,0);await tick(5000);show(latest);w.run('renderBoard([])');
  assert.equal(key(tab).getAttribute('aria-pressed'),'true');assert.equal(presentationTimers.size,0);
 }
 project();
 // Project real fixture shots into lifecycle snapshots; no server writes here.
 for(const stage of ['incoming','outgoing','queued'])for(const reason of ['revision','room','viewer','terminal','unmount','hidden','style','visibility','pagehide']){
  const before={...r,room_id:`CANCEL-${stage}-${reason}`,revision:0,current_player_id:'p1',board_state:{...r.board_state,shots:{...r.board_state.shots,p1:r.board_state.shots.p1.slice(0,-1)}}};
  const after={...before,revision:1,current_player_id:'p0',board_state:r.board_state};
  if(stage!=='incoming'){
   before.current_player_id='p0';
   before.board_state={...r.board_state,shots:{...r.board_state.shots,p0:r.board_state.shots.p0.slice(0,-1),p1:r.board_state.shots.p1.slice(0,-1)}};
   after.current_player_id='p1';
   after.board_state={...r.board_state,shots:{...r.board_state.shots,p1:r.board_state.shots.p1.slice(0,-1)}};
  }
  show(before);show(after);
  if(stage==='queued'){after.revision++;after.current_player_id='p0';after.board_state=r.board_state;show(after);}
  assert.equal(presentationTimers.size,1,`${stage}/${reason}`);
  if(reason==='revision')show({...after,revision:after.revision+1});
  if(reason==='room')show({...after,room_id:`OTHER-ROOM-${stage}`});
  if(reason==='viewer'){viewer='p1';show({...after,viewer:{...after.viewer,player_id:'p1'}});}
  if(reason==='terminal')show({...after,status:'finished',board_state:{...after.board_state,phase:'finished'}});
  if(reason==='unmount')doc.querySelector('.bp-game').remove();
  if(reason==='hidden')doc.querySelector('#gameView').classList.add('hidden');
  if(reason==='style')doc.querySelector('#gameView').style.display='none';
  if(reason==='visibility'){Object.defineProperty(doc,'hidden',{configurable:true,value:true});doc.dispatchEvent(new w.Event('visibilitychange'));}
  if(reason==='pagehide')w.dispatchEvent(new w.Event('pagehide'));
  await flush();assert.equal(presentationTimers.size,0,`${reason} cancels pending switch`);
  await tick(2100);viewer='p0';doc.querySelector('#gameView').classList.remove('hidden');
  doc.querySelector('#gameView').style.display='';Object.defineProperty(doc,'hidden',{configurable:true,value:false});
  show(after);assert.equal(presentationTimers.size,0,`${reason} does not revive an old timer`);project();
 }
 r=backend({action:'finish',room_id:id}).room;show(r);click('attack');assert.equal(doc.querySelectorAll('.bp-plane-head').length,3);
 console.log('PASS outgoing G3 dwell, fast I10/E6 serial reply, both-tab queue cancellation, outgoing/incoming/queued lifecycle; G3/E5/E4/E6: 1999ms own -> 2000ms attack once; either tab cancels; polls preserve deadline; revision/room/viewer/terminal/unmount/hidden cancel; result colors, isolation and reveal');
 assert.equal(doc.querySelectorAll('#bomb-plane-styles').length,1);
 console.log('PASS DOM integration; rendered screenshots and geometry require browser acceptance');
}finally{dom.window.close();fs.rmSync(tmp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1});
