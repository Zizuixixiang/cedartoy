/* Actual Duel app + renderer, actual rule engine, isolated SQLite transport.
 * NODE_PATH may point to existing jsdom; DUEL_TEST_PYTHON chooses a read-only venv.
 * No production network, browser, DB or writes. Pixel geometry is NOT asserted here.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const artifactRoot = process.env.RUMMIKUB_TEST_TMP || path.join(root, 'artifacts/rummikub');
fs.mkdirSync(artifactRoot, {recursive:true});
const temporary = fs.mkdtempSync(path.join(artifactRoot, 'ui-'));
const env = {...process.env, DUEL_DB_PATH:path.join(temporary, 'ui.db'),
  PYTHONPATH:path.join(root,'vendor/duel'), PYTHONPYCACHEPREFIX:path.join(temporary,'pycache')};
const python = process.env.DUEL_TEST_PYTHON || 'python3';
function backend(input) {
  // File descriptors avoid pipe EOF restrictions in hardened test sandboxes.
  const inputPath=path.join(temporary,'request.json'), outputPath=path.join(temporary,'response.json'), errorPath=path.join(temporary,'error.log');
  fs.writeFileSync(inputPath,JSON.stringify(input));
  const descriptors=[fs.openSync(inputPath,'r'),fs.openSync(outputPath,'w'),fs.openSync(errorPath,'w')];
  let result;
  try {
    result=spawnSync(python,[path.join(root,'vendor/duel/tests/rummikub_ui_fixture.py')],
      {stdio:descriptors,env,cwd:root,encoding:'utf8',timeout:15000});
  } finally { descriptors.forEach(fd=>fs.closeSync(fd)); }
  assert.equal(result.status,0,fs.readFileSync(errorPath,'utf8') || String(result.error || ''));
  return JSON.parse(fs.readFileSync(outputPath,'utf8'));
}
const dom = new JSDOM(fs.readFileSync(path.join(root,'vendor/duel/app/static/index.html'),'utf8'),
  {url:'http://duel.test/',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window, doc=w.document;
const writes=[];
let forceFailure=false;
w.fetch=async(url,options={})=>{
  if(options.method==='POST' && url.includes('/move')) {
    const body=JSON.parse(options.body), roomId=url.split('/')[3];
    writes.push(body);
    const data=forceFailure ? {ok:false,message:'模拟网络失败'} : backend({action:'move',room_id:roomId,...body});
    return {ok:data.ok,status:data.status||400,json:async()=>data};
  }
  return {ok:true,json:async()=>({ok:true,bound:false,notifications:[]})};
};
w.HTMLElement.prototype.scrollIntoView=()=>{};
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;};
for(const file of ['styles.css','games/rummikub.css']) {
  const style=doc.createElement('style'); style.textContent=fs.readFileSync(path.join(root,'vendor/duel/app/static',file),'utf8'); doc.head.append(style);
  assert.ok(style.sheet,'CSS parses');
}
for(const file of ['game_ui_registry.js','games/rummikub.js']) w.eval(fs.readFileSync(path.join(root,'vendor/duel/app/static',file),'utf8'));
const app=fs.readFileSync(path.join(root,'vendor/duel/app/static/app.js'),'utf8');
w.eval(app.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')+'\nwindow.run=source=>eval(source);');
w.run("startRoomPolling=()=>{}; identity={human_player_id:'p0',human_name:'玩家0',machines:[],games:[]};");
function load(name='opening') {
  const result=backend({action:'seed',fixture:name}); assert.equal(result.ok,true);
  show(result.room); return result.room;
}
function show(room) { w.fixture=room; w.run('renderGame(fixture,"",[]);'); }
function key(k) { return doc.querySelector(`[data-rk-focus="${k}"]`); }
function click(k) { assert.ok(key(k),`missing ${k}`); assert.equal(key(k).disabled,false,`${k} enabled`); key(k).click(); }
function pick(id,where='.rk-hand') { const b=doc.querySelector(`${where} [data-tile-id="${id}"]`); assert.ok(b,id); assert.equal(b.disabled,false); b.click(); }
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 try {
  let room=load();
  assert.equal(doc.querySelectorAll('.rk-hand .rk-tile').length,14);
  assert.match(doc.querySelector('.rk-rack .rk-meta').textContent,/尚未开局.*0 \/ 30/);
  assert.equal(doc.querySelector('#moveConfirm').classList.contains('hidden'),true);
  for (const k of ['new','move','return','clear','undo','reset']) assert.equal(key(k),null,`${k} absent before selection/editing`);
  assert.equal(key('draw').classList.contains('rk-primary'),true);
  assert.equal(key('submit').classList.contains('rk-primary'),false);
  assert.ok(doc.querySelector('.rk-rack-tools [data-rk-focus="suggest"]'),'suggestion is a rack helper');
  pick('red-10-1');
  assert.ok(key('new')); assert.ok(key('clear')); assert.equal(key('move'),null); assert.equal(key('return'),null);
  click('clear'); assert.equal(doc.querySelector('.rk-selection'),null);
  assert.equal(doc.activeElement.dataset.tileId,'red-10-1','cancel restores selected tile focus');
  const playerBarBefore=doc.getElementById('roomParticipants').textContent;
  click('sort-number');
  const nums=[...doc.querySelectorAll('.rk-hand .rk-number')].map(n=>Number(n.textContent));
  assert.deepEqual(nums,[...nums].sort((a,b)=>a-b));
  click('sort-color');
  for(const id of ['red-10-1','red-11-1','red-12-1']) pick(id);
  assert.equal(writes.length,0,'selection is local');
  click('new');
  assert.equal(doc.querySelectorAll('.rk-meld').length,1);
  assert.equal(doc.querySelector('.rk-meld-meta'),null,'ordinary groups have no empty title/tool row');
  assert.equal(doc.querySelector('.rk-meld-label').textContent,'1 · 顺子');
  assert.match(doc.querySelector('.rk-meld').getAttribute('aria-label'),/顺子/);
  assert.equal(doc.querySelectorAll('.rk-hand .rk-tile').length,11);
  assert.match(doc.querySelector('.rk-draft-status').textContent,/可提交/);
  assert.equal(doc.querySelector('.rk-selection'),null,'context actions disappear after move');
  assert.equal(key('submit').classList.contains('rk-primary'),true);
  assert.equal(key('draw').classList.contains('rk-primary'),false);
  click('undo'); assert.equal(doc.querySelectorAll('.rk-hand .rk-tile').length,14);
  assert.ok(doc.activeElement.dataset.rkFocus,'focus moves to an enabled game control after undo becomes disabled');
  for(const id of ['red-10-1','red-11-1','red-12-1']) pick(id);
  click('new'); click('reset'); assert.equal(doc.querySelectorAll('.rk-meld').length,0);
  click('undo'); assert.equal(doc.querySelectorAll('.rk-hand .rk-tile').length,11,'reset remains undoable');
  pick('red-12-1','.rk-table'); click('return');
  assert.equal(doc.querySelectorAll('.rk-hand .rk-tile').length,12,'own draft tile can return');
  click('undo'); assert.equal(doc.querySelectorAll('.rk-hand .rk-tile').length,11);
  click('reset');
  assert.equal(doc.getElementById('roomParticipants').textContent,playerBarBefore,'draft does not move player state');
  click('suggest'); assert.equal(key('submit').disabled,false);
  click('reset');
  for(const id of ['red-10-1','red-11-1','red-12-1']) pick(id);
  click('new');
  forceFailure=true; click('submit');
  assert.equal(doc.querySelector('.rk-controls').getAttribute('aria-busy'),'true');
  assert.ok(key('submit').disabled && key('draw').disabled && key('undo').disabled);
  const pendingWrites=writes.length; key('submit').click(); assert.equal(writes.length,pendingWrites,'busy prevents duplicate submission');
  await flush();
  assert.match(doc.querySelector('.rk-error').textContent,/草稿已保留/);
  assert.equal(doc.querySelectorAll('.rk-meld').length,1);
  assert.equal(backend({action:'state',room_id:room.room_id}).room.revision,room.revision);
  forceFailure=false; click('submit'); await flush(); await flush();
  let after=backend({action:'state',room_id:room.room_id}).room;
  assert.equal(after.revision,room.revision+1);
  assert.equal(after.private_state.hand.length,11);
  assert.equal(after.private_state.opened,true);
  assert.deepEqual(after.board_state.melds,[['red-10-1','red-11-1','red-12-1']]);
  assert.equal(writes.at(-1).revision,room.revision,'host adds authoritative revision');
  assert.equal(key('submit').disabled,true,'out of turn after atomic submission');
  assert.equal(doc.querySelector('.rk-selection'),null);
  assert.equal(key('undo'),null); assert.equal(key('suggest'),null);
  // Changing revisions clears the local draft; selected entities cannot survive a sync.
  after=backend({action:'opponent',room_id:room.room_id}).room; show(after);
  pick('red-1-1'); click('new');
  assert.equal(doc.querySelectorAll('.rk-meld').length,2);
  const stale=cloneRoom(after); stale.revision+=1; show(stale);
  assert.equal(doc.querySelectorAll('.rk-meld').length,1);
  assert.equal(doc.querySelector('.rk-selection'),null,'revision change clears contextual actions');
  const locked=cloneRoom(stale); locked.revision+=1; locked.private_state.opened=false; show(locked);
  pick('red-1-1');
  assert.equal(key('target-0'),null,'opening restriction prevents targeting old public groups');
  assert.ok([...doc.querySelectorAll('.rk-table .rk-tile')].every(b=>b.disabled));
  // Select an old tile, move to another group, then undo without any network write.
  room=load('long'); const writeCount=writes.length;
  assert.equal(doc.querySelectorAll('.rk-meld').length,20);
  assert.equal(doc.querySelector('.rk-target'),null,'no group target without selected tiles');
  pick('red-3-1','.rk-table'); assert.equal(key('return'),null,'old table tiles cannot return to rack');
  assert.equal(doc.querySelector('[data-group="1"]').getAttribute('role'),'group');
  assert.equal(key('target-1').getAttribute('aria-label'),'选第 2 组为目标');
  assert.equal(key('target-1').getAttribute('aria-pressed'),'false');
  assert.equal(key('target-1').tagName,'BUTTON','whole-group hit area is a native keyboard button');
  assert.equal(key('target-1').textContent,'','no target text button or toolbar');
  assert.equal(doc.querySelector('.rk-meld-meta'),null,'selecting a target adds no tool row');
  click('target-1'); assert.equal(key('target-1').getAttribute('aria-pressed'),'true'); click('move');
  assert.equal(doc.querySelector('[data-group="1"] .rk-tiles').children.length,4);
  assert.equal(key('return'),null,'no return action without selection');
  click('undo'); assert.equal(doc.querySelector('[data-group="1"] .rk-tiles').children.length,3);
  assert.equal(writes.length,writeCount);
  // Run order/joker placement can be adjusted with click controls, no dragging.
  room=load('joker'); pick('blue-11-1'); pick('blue-10-1'); click('new');
  pick('blue-10-1','.rk-table'); click('shift--1');
  assert.equal(doc.querySelector('[data-group="2"] .rk-tile').dataset.tileId,'blue-10-1');
  click('reset');
  room=load('ambiguous');
  for(const id of ['red-9-1','joker-1','joker-2']) pick(id);
  click('new'); assert.equal(key('submit').disabled,false);
  assert.equal(key('kind-0').value,'run');
  key('kind-0').value='group'; key('kind-0').dispatchEvent(new w.Event('change'));
  assert.equal(doc.querySelector('.rk-meld-label').textContent,'1 · 同数组');
  assert.equal(key('submit').disabled,true);
  assert.match(doc.querySelector('.rk-draft-status').textContent,/27 \/ 30/);
  click('undo'); assert.equal(key('kind-0').value,'run');
  click('submit'); await flush(); await flush();
  assert.deepEqual(writes.at(-1).move.kinds,['run']);
  // Empty pool does not prevent legal play; pass is an explicit separate action.
  room=load('empty'); assert.equal(key('draw').textContent,'无牌可出');
  pick('red-4-1'); click('target-0'); click('move'); click('submit'); await flush(); await flush();
  after=backend({action:'state',room_id:room.room_id}).room;
  assert.equal(after.status,'playing'); assert.equal(after.private_state.hand.length,1);
  after=backend({action:'opponent',room_id:room.room_id}).room; show(after);
  click('draw'); await flush(); await flush();
  after=backend({action:'state',room_id:room.room_id}).room;
  assert.equal(after.status,'finished'); assert.match(doc.querySelector('.rk-result').textContent,/获胜/);
  assert.equal(key('submit'),null,'terminal has no move control');
  assert.ok(doc.querySelectorAll('.rk-scores span').length===2);
  assert.equal(doc.querySelectorAll('#rummikub-styles').length,1,'styles loaded once');
  assert.ok([...doc.querySelectorAll('.rk-tile')].every(b=>b.getAttribute('aria-label')));
  console.log('PASS: actual Duel DOM + isolated rules transport; sort, selection, new/target, reorder, undo/reset, failure recovery, revision reset, submit, empty-pool continuation, terminal. Pixel/browser review remains separate.');
 } finally { dom.window.close(); fs.rmSync(temporary,{recursive:true,force:true}); }
})().catch(e=>{console.error(e);process.exitCode=1});
function cloneRoom(room){return JSON.parse(JSON.stringify(room));}
