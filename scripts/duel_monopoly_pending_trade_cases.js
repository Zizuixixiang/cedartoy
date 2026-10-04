const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const path = require('node:path');

// Read-only engine use: all states are created in memory, never persisted.
const generated = spawnSync('.venv/bin/python', ['-c', `
import json
from copy import deepcopy
from app.games.monopoly import Monopoly
cases=[]
for count in (2,4,6):
 for kind in ('human','bound_machine','system_npc'):
  ps=[dict(player_id=f'human:{i+1}',display_name=('许知衡' if i==0 else 'Codex_杉星_0828' if i==1 else f'朋友{i+1}'),role='ai' if i==1 and kind!='human' else 'human',participant_kind=kind if i==1 else 'human',seat_index=i) for i in range(count)]
  g=Monopoly(); opening=g.initialize(ps); opening['phase']='manage'; opening['tiles'][19]['owner']='human:2'
  pending=g.apply_action(opening,dict(action='propose_trade',action_seq=opening['action_seq'],to='human:2',give_cash=400,take_cash=0,give_tiles=[],take_tiles=[19]),ps[0]).state
  gated=g.apply_action(pending,dict(action='end_turn',action_seq=pending['action_seq']),ps[0]).state
  accepted=g.apply_action(gated,dict(action='respond_trade',accept=True,action_seq=gated['action_seq']),ps[1]).state
  rejected=g.apply_action(gated,dict(action='respond_trade',accept=False,action_seq=gated['action_seq']),ps[1]).state
  poor=deepcopy(pending); poor['players'][0]['cash']=0
  expired=g.apply_action(poor,dict(action='end_turn',action_seq=poor['action_seq']),ps[0]).state
  states={key:dict(board_state=g.public_state(s,ps),private_states={p['player_id']:g.private_state(s,p,ps) for p in ps}) for key,s in dict(opening=opening,pending=pending,gated=gated,accepted=accepted,rejected=rejected,expired=expired).items()}
  cases.append(dict(count=count,kind=kind,participants=ps,states=states))
print(json.dumps(cases))
`], {cwd:path.resolve(__dirname,'../vendor/duel'),encoding:'utf8',maxBuffer:16*1024*1024});
assert.equal(generated.status,0,generated.stderr);
const cases=JSON.parse(generated.stdout);

module.exports=async function checkPendingTrades(page,bases,width,output,render){
 const reports=[];let scenario=0;
 await page.clock.install();await page.clock.pauseAt(new Date(await page.evaluate(() => Date.now()) + 1000));
 const show=async r=>{
  await page.evaluate(id=>window.run(`identity.human_player_id=${JSON.stringify(id)};`),r.viewer.player_id);
  await render(r);
 };
 const transition=async (a,b,expected=[])=>{
  a=structuredClone(a);b=structuredClone(b);a.room_id+=`-${++scenario}`;b.room_id=a.room_id;
  await show(a);await page.locator('.monopoly-ring').scrollIntoViewIfNeeded();
  await page.evaluate(next=>{
   const center=document.querySelector('.monopoly-center'),box=center.getBoundingClientRect(),y=scrollY;
   window.tradeProbe={kinds:[],shift:false,scroll:false,overflow:false,taskHidden:true};
   const seen=new WeakSet();
   const inspect=()=>{
    if(!center.isConnected)return;
    const r=center.getBoundingClientRect(),p=tradeProbe,layer=center.querySelector('.monopoly-event-layer');
    p.shift ||= Math.abs(r.x-box.x)>1||Math.abs(r.y-box.y)>1||Math.abs(r.height-box.height)>1;
    p.scroll ||= Math.abs(scrollY-y)>1;
    if(layer&&!seen.has(layer)){seen.add(layer);p.kinds.push(layer.dataset.kind);}
    if(layer){p.overflow ||= layer.scrollWidth>layer.clientWidth+1||layer.scrollHeight>layer.clientHeight+1;const task=center.querySelector('.monopoly-trade-task');if(task)p.taskHidden &&= getComputedStyle(task).visibility==='hidden';}
   };
   const observer=new MutationObserver(inspect);observer.observe(center,{subtree:true,attributes:true,childList:true});
   window.tradeNext=next;window.tradeDone=false;
   window.run('window.tradePromise=(async()=>{await showRoomTransitionFeedback(room,[],tradeNext,[]);renderGame(tradeNext,"",[]);tradeDone=true;})()');
   tradePromise.finally(()=>{inspect();observer.disconnect();});
  },b);
  for(let elapsed=0;elapsed<12000&&!await page.evaluate(()=>tradeDone);elapsed+=100)await page.clock.runFor(100);
  assert.equal(await page.evaluate(()=>tradeDone),true);
  const probe=await page.evaluate(()=>tradeProbe);assert.deepEqual(probe.kinds,expected);assert.equal(probe.shift,false);assert.equal(probe.scroll,false);assert.equal(probe.overflow,false);assert.equal(probe.taskHidden,true);
  assert.equal(await page.locator('.monopoly-event-layer').count(),0);
  await page.evaluate(async ({a,b})=>window.DuelGameUI.get('monopoly').transitionFeedback({previousRoom:a,nextRoom:b}),{a,b});
  assert.equal(await page.locator('.monopoly-event-layer').count(),0,'poll never repeats event');
 };
 for(const fixture of cases){
  const {count,kind,states}=fixture,base=bases.find(b=>b.participants.length===count);
  const room=(key,viewer='human:1')=>{
   const s=states[key],r=structuredClone(base);r.room_id=`pending-${count}-${kind}`;r.participants=fixture.participants;
   r.board_state=structuredClone(s.board_state);r.revision=1000+r.board_state.action_seq;
   r.current_player_id=r.board_state.turn_player_id;r.current_actor=r.participants.find(p=>p.player_id===r.current_player_id);r.current_turn=r.current_actor.role;r.turn=r.current_actor.role;
   r.viewer={player_id:viewer,role:viewer==='spectator'?'spectator':r.participants.find(p=>p.player_id===viewer).role};
   r.private_state=structuredClone(s.private_states[viewer]||{legal_actions:[]});return r;
  };
  await transition(room('opening'),room('pending'));
  assert.equal(await page.locator('.monopoly-trade-task').count(),0);
  assert.match(await page.locator('.monopoly-turn').textContent(),/许知衡的回合/);
  assert.equal(await page.locator('[data-action="end_turn"]').isEnabled(),true);
  assert.equal(await page.locator('[data-action="respond_trade"]').count(),0);
  const card=page.locator('.monopoly-pending-trade');assert.match(await card.textContent(),/等待 Codex_杉星_0828 在自己的回合处理/);
  assert.equal(await card.locator('.monopoly-offer-side > p').allTextContents().then(a=>a.join('/')),'现金 400/星街');
  await page.locator('[data-action="end_turn"]').click();assert.equal(await page.evaluate(()=>sent.at(-1).action),'end_turn');
  for(const viewer of ['human:1','human:2','spectator']){
   await show(room('pending',viewer));assert.equal(await page.locator('.monopoly-trade-task').count(),0);assert.equal(await page.locator('[data-action="respond_trade"]').count(),0);
   await show(room('gated',viewer));
   assert.match(await page.locator('.monopoly-trade-task').textContent(),viewer==='human:2'?/许知衡向你提出交易/:/等待 Codex_杉星_0828 决定/);
   assert.match(await page.locator('.monopoly-trade-task').textContent(),/400 ↔ 星街/);
   assert.equal(await page.locator('.monopoly-turn').count(),0);
   assert.equal(await page.locator('[data-action="respond_trade"]').count(),viewer==='human:2'?2:0);
   const geometry=await page.evaluate(()=>{
    const center=document.querySelector('.monopoly-center'),task=document.querySelector('.monopoly-trade-task');const a=center.getBoundingClientRect(),b=task.getBoundingClientRect();
    return {overflow:document.documentElement.scrollWidth>innerWidth||b.top<a.top||b.bottom>a.bottom||task.scrollWidth>task.clientWidth+1,buttons:[...document.querySelectorAll('.monopoly-trade-response button')].map(e=>e.getBoundingClientRect().height)};
   });assert.equal(geometry.overflow,false);assert.ok(geometry.buttons.every(h=>h>=44));
   if(viewer==='human:2'&&kind==='human'){
    assert.equal(await page.getByRole('button',{name:'接受交易',exact:true}).evaluate(e=>e.classList.contains('primary')),true);
    for(const accept of [false,true]){
     const label=accept?'接受交易':'拒绝交易';await page.getByRole('button',{name:label,exact:true}).focus();await page.keyboard.press('Enter');
     const sent=await page.evaluate(()=>window.sent.at(-1));assert.equal(sent.action,'respond_trade');assert.equal(sent.accept,accept);assert.equal(sent.action_seq,states.gated.board_state.action_seq);
    }
   }
  }
  if(kind==='human'){
   for(const key of ['pending','gated']){
    await show(room(key,key==='gated'?'human:2':'human:1'));
    await page.locator('.monopoly-controls').scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/pending-trade-${key}-${count}-${width}.png`,fullPage:true});
   }
  }
  for(const key of ['accepted','rejected','expired']){
   await transition(room(key==='expired'?'pending':'gated','human:2'),room(key,'human:2'),key==='accepted'?['trade']:key==='expired'?['trade-expired']:[]);
   assert.equal(await page.locator('.monopoly-trade-task,.monopoly-pending-trade').count(),0);
   assert.match(await page.locator('.monopoly-turn').textContent(),/Codex_杉星_0828的回合/);
   if(kind==='human')assert.equal(await page.locator('[data-action="roll"]').isEnabled(),true);
  }
  // A transient event covers a current task and restores it on the next render.
  const a=room('gated'),b=room('gated');b.revision++;b.board_state.action_seq++;b.board_state.last_action_note='许知衡支付200（所得税）。';b.board_state.players[0].cash-=200;
  await transition(a,b,['tax']);assert.equal(await page.locator('.monopoly-trade-task').isVisible(),true);
  // The phase name, recipient's identity and a nonempty trade cannot infer a gate.
  const legacy=room('pending');legacy.board_state.phase='trade';legacy.board_state.turn_player_id='human:2';await show(legacy);
  assert.equal(await page.locator('.monopoly-trade-task').count(),0);assert.equal(await page.locator('[data-action="respond_trade"]').count(),0);
  reports.push({width,count,kind,pending:true,response:true,resumed:true,expired:true,overlay:true});
 }
 await page.clock.resume();
 await page.evaluate(()=>window.run("identity.human_player_id='human:1';"));
 return reports;
};
