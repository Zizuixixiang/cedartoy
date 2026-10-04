/* DOM + real backend acceptance, not a substitute for rendered screenshots. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process'),{JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts/carcassonne');
fs.mkdirSync(out,{recursive:true});const tmp=fs.mkdtempSync(path.join(out,'dom-'));
const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:path.join(root,'vendor/duel'),TMPDIR:tmp};let seq=0;
function backend(input){
 if(input.action==='seed')env.DUEL_DB_PATH=path.join(tmp,`fixture-${++seq}.db`);
 const names=['in.json','out.json','error.log'].map(n=>path.join(tmp,n));fs.writeFileSync(names[0],JSON.stringify(input));
 const fds=[fs.openSync(names[0],'r'),fs.openSync(names[1],'w'),fs.openSync(names[2],'w')];let r;
 try{r=spawnSync(process.env.DUEL_TEST_PYTHON||'python3',[path.join(root,'vendor/duel/tests/carcassonne_ui_fixture.py')],{env,cwd:root,stdio:fds,timeout:30000});}finally{fds.forEach(fd=>fs.closeSync(fd));}
 assert.equal(r.status,0,fs.readFileSync(names[2],'utf8')||String(r.error));return JSON.parse(fs.readFileSync(names[1],'utf8'));
}
const dom=new JSDOM(fs.readFileSync(path.join(root,'vendor/duel/app/static/index.html'),'utf8'),{url:'http://carcassonne.test',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,doc=w.document;let forceFailure=false;const writes=[];
w.fetch=async(url,opt={})=>{
 if(opt.method==='POST'&&url.includes('/move')){
  const body=JSON.parse(opt.body);writes.push(body);const result=forceFailure?{ok:false,message:'模拟过期 revision'}:backend({action:'move',room_id:url.split('/')[3],...body});
  return {ok:result.ok,status:result.status||409,json:async()=>result};
 }
 return {ok:true,json:async()=>({ok:true,bound:false,notifications:[],rooms:[]})};
};
w.HTMLElement.prototype.scrollIntoView=()=>{};
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
for(const file of ['styles.css','games/carcassonne.css']){const s=doc.createElement('style');s.textContent=fs.readFileSync(path.join(root,'vendor/duel/app/static',file),'utf8');doc.head.append(s);assert.ok(s.sheet);}
for(const file of ['game_ui_registry.js','games/carcassonne.js'])w.eval(fs.readFileSync(path.join(root,'vendor/duel/app/static',file),'utf8'));
const app=fs.readFileSync(path.join(root,'vendor/duel/app/static/app.js'),'utf8');
w.eval(app.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/,'')+'\nwindow.run=source=>eval(source);');
w.run("startRoomPolling=()=>{};identity={human_player_id:'p0',human_name:'南杉',machines:[],games:[]};");
const show=room=>{w.fixture=room;w.run('renderGame(fixture,"",[]);');};
const key=k=>doc.querySelector(`[data-cc-key="${k}"]`);
const click=k=>{const b=key(k);assert.ok(b,`missing ${k}`);assert.equal(b.disabled,false,k);b.click();};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 try{
  for(const count of [2,5]){
   let result=backend({action:'seed',fixture:'scoring',count,invited:count===5});assert.ok(result.ok);let room=result.room;
   w.catalog=result.games;
   w.run("identity.games=catalog;document.querySelector('#gameCategory').value='tabletop';syncGameTypeOptions(catalog);document.querySelector('#inviteCategory').value='tabletop';syncInviteGameOptions();");
   for(const id of ['gameType','inviteGame'])assert.deepEqual([...doc.querySelector('#'+id).options].map(o=>o.value).sort(),['carcassonne','monopoly','rummikub']);
   w.run("document.querySelector('#gameType').value='carcassonne';updateGameTokenEstimate();");
   assert.match(doc.querySelector('#gameTokenEstimate').textContent,/500–3500/);
   assert.match(doc.querySelector('#gameTokenEstimate').title,/不是整轮实际计费/);
   show(room);
   assert.equal(doc.querySelectorAll('.cc-tile').length,1);assert.equal(key('submit'),null);
   assert.equal(doc.querySelector('.cc-map-hint').previousElementSibling.className,'cc-viewport');
   assert.equal(doc.querySelector('.cc-touch-hint').textContent,'拖动地图 · 双指缩放');
   assert.equal(doc.querySelector('.cc-mouse-hint').textContent,'拖动地图 · 滚轮缩放');
   assert.equal(doc.querySelector('#moveConfirm').classList.contains('hidden'),true);
   assert.equal(doc.querySelector('#privateStatePanel').classList.contains('hidden'),true);
   assert.equal(doc.querySelectorAll('#roomParticipants .room-participant,#viewerParticipant .room-participant').length,count);
   assert.equal(doc.querySelector('#opponentRow').classList.contains('hidden'),true);
   assert.equal(doc.querySelector('#humanRow').classList.contains('hidden'),true);
   const before=writes.length;click('rotate');click('rotate');click('spot-0--1');click('region-c0');
   assert.equal(writes.length,before);assert.equal(key('region-c0').getAttribute('aria-pressed'),'true');
   assert.equal(doc.querySelectorAll('.cc-spot.is-selected').length,1);assert.ok(doc.querySelector('.cc-draft'));
   assert.equal(doc.querySelector('.cc-confirm').firstElementChild,key('none'));
   assert.equal(doc.querySelector('.cc-confirm').lastElementChild,key('submit'));
   assert.equal(key('none').closest('.cc-regions'),null);
   const map=doc.querySelector('.cc-viewport');
   map.dispatchEvent(new w.WheelEvent('wheel',{deltaY:-150,bubbles:true,cancelable:true}));
   map.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
   const placedTransform=doc.querySelector('.cc-world').style.transform;
   const oldState=backend({action:'state',room_id:room.room_id}).room;assert.equal(oldState.revision,room.revision);
   forceFailure=true;click('submit');await flush();await flush();assert.ok(doc.querySelector('.cc-error'));assert.equal(key('submit').disabled,false);
   forceFailure=false;click('submit');await flush();await flush();
   room=backend({action:'state',room_id:room.room_id}).room;
   assert.equal(doc.querySelector('.cc-world').style.transform,placedTransform,'own revision preserves camera');
   assert.equal(doc.querySelector('.cc-draft'),null);assert.equal(doc.querySelector('.cc-regions'),null);
   assert.equal(doc.querySelector('.cc-current-tile svg g').getAttribute('transform'),'rotate(0 50 50)');
   assert.equal(room.revision,oldState.revision+1);assert.equal(room.board_state.scores.p0,4);assert.equal(room.board_state.supply.p0,7);
   assert.deepEqual(writes.at(-1).move,{action:'place',x:0,y:-1,rotation:2,meeple:'c0'});
   assert.equal(writes.at(-1).revision,oldState.revision);assert.equal(doc.querySelectorAll('.cc-tile').length,2);
   assert.equal(doc.querySelectorAll('.cc-meeple').length,0);assert.match(doc.querySelector('.cc-activity').textContent,/城市 4分/);
   assert.match(doc.querySelector('#viewerParticipant').textContent,/得分 4.*随从 7/);show(room);assert.equal(key('submit'),null);
   room=backend({action:'opponents',room_id:room.room_id}).room;show(room);
   assert.equal(doc.querySelector('.cc-world').style.transform,placedTransform,'opponent revision preserves camera');
   const cachedRoom=room;
   doc.querySelector('.cc-spot').click();click('submit');await flush();await flush();
   assert.equal(writes.at(-1).move.meeple,null);
   const cachedTransform=doc.querySelector('.cc-world').style.transform;
   room=backend({action:'seed',fixture:'late',count,invited:count===5}).room;show(room);
   const roomDefault=doc.querySelector('.cc-world').style.transform;
   assert.notEqual(roomDefault,cachedTransform,'new room has its own default camera');
   show(cachedRoom);assert.equal(doc.querySelector('.cc-world').style.transform,cachedTransform,'return to room restores only its camera');
   assert.equal(doc.querySelector('.cc-draft'),null);
   show(room);assert.equal(doc.querySelector('.cc-world').style.transform,roomDefault);
   assert.equal(doc.querySelectorAll('.cc-tile').length,61);assert.ok(doc.querySelector('.cc-meeple'));
   for(const removed of ['find','zoom-out','zoom-in'])assert.equal(key(removed),null);
   assert.equal(doc.querySelector('.cc-tools'),null);assert.equal(doc.querySelector('.cc-regions'),null);
   assert.deepEqual([...doc.querySelectorAll('.cc-controls button')].map(b=>b.dataset.ccKey),['rotate']);
   let viewport=doc.querySelector('.cc-viewport');assert.equal(key('latest').parentElement,viewport);assert.ok(key('latest').getAttribute('aria-label'));
   assert.ok([...doc.querySelectorAll('.cc-spot')].every(b=>b.tabIndex===0));
   // jsdom has no layout or pointer capture. Supply only geometry/capture; the
   // production listeners still own all gesture math and click suppression.
   viewport.getBoundingClientRect=()=>({left:20,top:40,width:320,height:310});
   const captures=new Set();viewport.setPointerCapture=id=>captures.add(id);viewport.hasPointerCapture=id=>captures.has(id);viewport.releasePointerCapture=id=>captures.delete(id);
   const state=()=>{const a=doc.querySelector('.cc-world').style.transform.match(/-?[\d.]+/g).map(Number);return {x:a[0],y:a[1],z:a[2]};};
   const near=(a,b)=>assert.ok(Math.abs(a-b)<.00001,`${a} != ${b}`);
   const pointer=(type,id,x,y,target=viewport)=>{const e=new w.Event(type,{bubbles:true});Object.assign(e,{pointerId:id,clientX:x,clientY:y,button:0});target.dispatchEvent(e);};
   const wheel=(delta,x=210,y=210,mode=0)=>{const e=new w.WheelEvent('wheel',{deltaY:delta,deltaMode:mode,clientX:x,clientY:y,bubbles:true,cancelable:true});viewport.dispatchEvent(e);assert.ok(e.defaultPrevented);};
   const original=state();wheel(-100);let after=state();assert.ok(after.z>original.z);
   near((30-original.x)/original.z,(30-after.x)/after.z);near((15-original.y)/original.z,(15-after.y)/after.z);
   wheel(100);near(state().z,original.z);click('latest');near(state().x,original.x);
   pointer('pointerdown',1,110,180);pointer('pointermove',1,113,182);near(state().x,original.x);
   pointer('pointermove',1,150,210);near(state().x,original.x+40);pointer('pointerup',1,150,210);
   let spot=doc.querySelector('.cc-spot');spot.dispatchEvent(new w.MouseEvent('click',{bubbles:true,detail:1}));assert.equal(doc.querySelector('.cc-draft'),null);
   click('latest');const beforePinch=state();
   pointer('pointerdown',1,110,180,spot);pointer('pointerdown',2,210,180);
   // Transferring implicit touch capture from the spot must not end a finger.
   pointer('lostpointercapture',1,110,180,spot);
   pointer('pointermove',1,90,200);pointer('pointermove',2,250,200);
   after=state();near(after.z,beforePinch.z*1.6);
   near((-20-beforePinch.x)/beforePinch.z,(-10-after.x)/after.z);
   near((-15-beforePinch.y)/beforePinch.z,(5-after.y)/after.z);
   pointer('pointerup',2,250,200);const one=state();pointer('pointermove',1,105,210);near(state().x,one.x+15);near(state().y,one.y+10);
   pointer('pointercancel',1,105,210);const cancelled=state();pointer('pointermove',1,170,220);assert.deepEqual(state(),cancelled);
   spot.dispatchEvent(new w.MouseEvent('click',{bubbles:true,detail:1}));assert.equal(doc.querySelector('.cc-draft'),null);
   for(let i=0;i<10;i++)wheel(500);near(state().z,.6);
   wheel(3,210,210,1);near(state().z,.6);
   for(let i=0;i<10;i++)wheel(-500);near(state().z,1.8);
   viewport.dispatchEvent(new w.KeyboardEvent('keydown',{key:'-',bubbles:true}));near(state().z,1.65);
   const prev=state();viewport.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));near(state().x,prev.x+80);
   // Native keyboard activation emits detail=0 and must work after a gesture.
   spot.click();assert.ok(doc.querySelector('.cc-spot.is-selected'));assert.equal(key('submit').disabled,false);
   assert.equal(key('submit').textContent,'确认放置');assert.equal(key('none').getAttribute('aria-pressed'),'true');
   const choice=doc.querySelector('.cc-regions button[data-cc-key^="region-"]');
   if(choice){
    const k=choice.dataset.ccKey;click(k);assert.equal(k,doc.activeElement.dataset.ccKey);assert.equal(key(k).getAttribute('aria-pressed'),'true');
    assert.equal(key('submit').textContent,'确认放置');assert.match(doc.querySelector('.cc-confirm').textContent,/随从：/);
    click(k);assert.equal(key('none').getAttribute('aria-pressed'),'true');
    click(k);
    const other=[...doc.querySelectorAll('.cc-regions button[data-cc-key^="region-"]')].find(b=>b.dataset.ccKey!==k);
    if(other){click(other.dataset.ccKey);assert.equal(key(k).getAttribute('aria-pressed'),'false');assert.equal(key(other.dataset.ccKey).getAttribute('aria-pressed'),'true');}
    click('none');assert.equal(key(k).getAttribute('aria-pressed'),'false');
   }
   click('none');click('submit');await flush();await flush();assert.equal(doc.querySelectorAll('.cc-tile').length,62);
   const latestRoom=backend({action:'state',room_id:room.room_id}).room;
   click('latest');const latestTransform=doc.querySelector('.cc-world').style.transform;
   show(cachedRoom);show(latestRoom);
   assert.equal(doc.querySelector('.cc-world').style.transform,latestTransform,'latest updates the room camera cache');
   // A refreshed terminal view removes action controls, preserves navigation.
   room=backend({action:'seed',fixture:'terminal',count,invited:count===5}).room;show(room);
   assert.ok(doc.querySelector('.cc-result'));assert.equal(key('submit'),null);assert.ok(key('latest'));assert.equal(doc.querySelectorAll('.cc-spot').length,0);
  }
  console.log(JSON.stringify({ok:true,players:[2,5],realEngine:true,checks:['sharedRosterOnce','rotation','negativeCoordinate','region','skipMeeple','atomicConfirm','conflictRetry','scoreReturn','reload','lateMap','anchoredWheel','pinch','pinchToPan','cancel','noGesturePlacement','zoomBounds','keyboardPan','progressiveControls','roomCamera','draftReset','latestCache','terminal'],visualVerification:false}));
 }finally{w.close();fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
