const assert = require('node:assert/strict');
const eventCases = require('./monopoly_event_cases');

module.exports = async function checkEvents(page, bases, cardCases, width, output, render) {
  await page.clock.install();
  await page.clock.pauseAt(new Date(await page.evaluate(() => Date.now()) + 1000));
  const reports = [];
  const run = async (before,after,label,capture=false) => {
    await render(before);
    assert.equal(await page.locator('.monopoly-event-layer, .monopoly-card-event').count(),0,'entering a room never replays history');
    await page.locator('.monopoly-ring').scrollIntoViewIfNeeded();
    await page.evaluate(next=>{
      const wrap=document.querySelector('.monopoly-game'), center=wrap.querySelector('.monopoly-center');
      const box=center.getBoundingClientRect(), scroll=scrollY;
      window.eventNext=next; window.eventDone=false;
      window.eventProbe={kinds:[],events:[],shifted:false,scrolled:false,overflow:false,overlap:false,normalCards:0};
      const seen=new WeakSet();
      const inspect=()=>{
        if(!center.isConnected)return;
        const r=center.getBoundingClientRect(), probe=eventProbe;
        probe.shifted ||= Math.abs(r.x-box.x)>1||Math.abs(r.y-box.y)>1||Math.abs(r.width-box.width)>1||Math.abs(r.height-box.height)>1;
        probe.scrolled ||= Math.abs(scrollY-scroll)>1;
        const layer=center.querySelector('.monopoly-event-layer');
        if(!layer)return;
        probe.overflow ||= layer.scrollWidth>layer.clientWidth+1||layer.scrollHeight>layer.clientHeight+1;
        probe.overlap ||= !!wrap.querySelector('.monopoly-moving-token');
        probe.normalCards += document.querySelectorAll('.monopoly-card-event, dialog[open]').length;
        if(!seen.has(layer)){
          seen.add(layer);probe.kinds.push(layer.dataset.kind);
          probe.events.push({kind:layer.dataset.kind,text:layer.textContent,at:performance.now(),highlights:[...wrap.querySelectorAll('.is-event-target')].map(e=>Number(e.dataset.tileId)),resultPending:!!layer.querySelector('.pending-result')});
        }
      };
      const observer=new MutationObserver(inspect);
      observer.observe(wrap,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
      window.run('window.eventPromise=(async()=>{await showRoomTransitionFeedback(room,[],eventNext,[]);renderGame(eventNext,"",[]);eventDone=true;})()');
      window.eventPromise.finally(()=>{inspect();observer.disconnect();});
    },after);
    let screenshot=false, repeatChecked=false;
    for(let elapsed=0;elapsed<20000&&!await page.evaluate(()=>eventDone);elapsed+=100){
      await page.clock.runFor(100);
      if(!repeatChecked&&await page.locator('.monopoly-event-layer').count()){
        await page.evaluate(async ({a,b})=>{await window.DuelGameUI.get('monopoly').transitionFeedback({previousRoom:a,nextRoom:b});},{a:before,b:after});
        repeatChecked=true;
      }
      if(capture&&!screenshot&&await page.locator('.monopoly-event-layer').count()){
        const ready=await page.locator('.monopoly-event-layer .pending-result').count()===0;
        if(ready){await page.screenshot({path:`${output}/event-${label}-${width}.png`,clip:await page.locator('.monopoly-ring').boundingBox()});screenshot=true;}
      }
    }
    assert.equal(await page.evaluate(()=>eventDone),true,`${label}: completes`);
    const probe=await page.evaluate(()=>eventProbe);
    assert.equal(probe.shifted,false,`${label}: center geometry is stable`);
    assert.equal(probe.scrolled,false,`${label}: no scroll jump`);
    assert.equal(probe.overflow,false,`${label}/${width}: readable event fits center`);
    assert.equal(probe.overlap,false,'movement finishes before central events');
    assert.equal(probe.normalCards,0,'no competing modal');
    assert.equal(await page.locator('.monopoly-event-layer,.is-event-target,.monopoly-moving-token').count(),0);
    assert.equal(await page.locator('.monopoly-center-title').evaluate(e=>getComputedStyle(e).visibility),'visible');
    assert.equal(await page.locator('.monopoly-controls').evaluate(e=>!!e.inert),false);
    assert.equal(await page.locator('.monopoly-tile:disabled').count(),0);
    // Replaying the exact same response against its old snapshot is also inert.
    await page.evaluate(async ({a,b})=>{await window.DuelGameUI.get('monopoly').transitionFeedback({previousRoom:a,nextRoom:b});},{a:before,b:after});
    assert.equal(await page.locator('.monopoly-event-layer').count(),0);
    reports.push({width,label,...probe});return probe;
  };
  for(const base of bases){
    for(const c of eventCases(base)){
      const label=`${c.label}-${base.participants.length}`;
      const probe=await run(c.before,c.after,label,base.participants.length===4||['trade','chance','bankrupt'].includes(c.label));
      assert.deepEqual(probe.kinds,c.label==='roll'?[]:c.label==='multi'?['chance','rent']:[c.label]);
      if(['chance','chest','multi'].includes(c.label))assert.equal(probe.events[0].resultPending,true);
      if(c.label==='multi')assert.ok(probe.events[1].at-probe.events[0].at>=1900,'ordered dwell before next event');
      if(c.label==='jail')assert.deepEqual(probe.events[0].highlights,[10]);
      if(c.label==='build')assert.deepEqual(probe.events[0].highlights,[3]);
    }
  }
  for(const [label,snapshots] of Object.entries(cardCases)){
    const before={...structuredClone(bases[2]),room_id:`engine-card-${label}`,revision:800,board_state:snapshots.before};
    const after={...structuredClone(before),revision:801,board_state:snapshots.after};
    const probe=await run(before,after,label,true);
    assert.equal(probe.events.filter(e=>['chance','chest'].includes(e.kind)).length,snapshots.after.last_card_events.length);
    for(const e of snapshots.after.last_card_events)assert.ok(probe.events.some(b=>b.text.includes(e.text)&&b.text.includes(e.summary)));
    // A fresh room/viewer establishes a baseline, even with existing cards.
    await render({...after,room_id:`reentry-${label}`});
    assert.equal(await page.locator('.monopoly-event-layer,.monopoly-card-event').count(),0);
  }
  // Spectators see public events too; switching a viewer or room cancels a queue.
  const long=eventCases(bases[2]).find(c=>c.label==='trade');
  for(const r of [long.before,long.after])r.participants.forEach(p=>{p.display_name='一位名字特别长的朋友许知衡和远道而来的朋友乔麦';});
  await run(long.before,long.after,'long-trade',true);
  const queued=eventCases(bases[0]).find(c=>c.label==='chance');
  queued.before.viewer={is_participant:false};queued.after.viewer={is_participant:false};
  assert.deepEqual((await run(queued.before,queued.after,'spectator')).kinds,['chance']);
  await render(queued.before);
  const fresh=structuredClone(queued.after);fresh.board_state.action_seq++;fresh.revision++;
  fresh.board_state.last_card_events[0].action_seq++;fresh.board_state.last_card_events[0].event_id='72:1';
  await page.evaluate(({a,b})=>{window.cancelEvent=window.DuelGameUI.get('monopoly').transitionFeedback({previousRoom:a,nextRoom:b});},{a:queued.before,b:fresh});
  await page.clock.runFor(100);assert.equal(await page.locator('.monopoly-event-layer').count(),1);
  await render({...fresh,room_id:'other-event-room',viewer:{player_id:'human:1'}});
  await page.clock.runFor(200);await page.evaluate(()=>window.cancelEvent);
  assert.equal(await page.locator('.monopoly-event-layer,.is-event-target').count(),0);
  await page.evaluate(async ({a,b})=>{await window.DuelGameUI.get('monopoly').transitionFeedback({previousRoom:a,nextRoom:b});},{a:{...fresh,room_id:'other-event-room',viewer:{player_id:'human:1'}},b:{...fresh,room_id:'other-event-room',revision:fresh.revision+1,viewer:{player_id:'human:2'}}});
  assert.equal(await page.locator('.monopoly-event-layer').count(),0,'viewer switch never replays another viewer queue');
  await page.emulateMedia({reducedMotion:'reduce'});
  const reduced=eventCases(bases[0]).find(c=>c.label==='buy');reduced.before.room_id+='-reduce';reduced.after.room_id=reduced.before.room_id;
  assert.deepEqual((await run(reduced.before,reduced.after,'reduced')).kinds,['buy']);
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.clock.resume();
  return reports;
};
