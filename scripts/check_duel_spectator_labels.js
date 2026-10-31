/* Focused synthetic projections; no server, DB, browser, or private production data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const base = 'vendor/duel/app/static/';
const dom = new JSDOM(fs.readFileSync(base + 'index.html','utf8'), {
  url:'http://duel.test/', runScripts:'outside-only', pretendToBeVisual:true,
});
const w = dom.window, doc = w.document;
w.scrollTo = () => {};
w.HTMLElement.prototype.scrollIntoView = () => {};
w.HTMLDialogElement.prototype.showModal = function(){this.open=true;};
w.HTMLDialogElement.prototype.close = function(){this.open=false;};
w.ResizeObserver = class {observe(){} disconnect(){}};
w.MutationObserver = undefined; // Tutorial overlays are unrelated to acting-player labels.
w.eval(fs.readFileSync(base+'game_ui_registry.js','utf8'));
w.eval(fs.readFileSync(base+'games/gem_merchant.js','utf8'));
w.eval(fs.readFileSync(base+'app.js','utf8').replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')+'\nwindow.run=source=>eval(source);');
w.run(`identity={human_player_id:'human:owner',machines:[{id:'101',name:'Codex_杉星_0828'},{id:'102',name:'另一位小机'}]}; startRoomPolling=()=>{};`);
const players = Object.fromEntries(['101','102'].map(id=>[id,{
  tokens:{},bonuses:{},color_points:{},points:0,crowns:0,best_color_points:0,privileges:0,token_total:0,
}]));
const fixture = {
  room_id:'SPECTATOR_TEST',game_type:'gem_merchant',game_name:'宝石商人',room_kind:'invite',status:'playing',
  spectating:true,revision:17,current_player_id:'101',turn:'ai',
  current_actor:{player_id:'101',display_name:'Codex_杉星_0828',role:'ai'},
  participants:['101','102'].map((id,seat_index)=>({player_id:id,seat_index,role:'ai',participant_kind:'bound_machine',
    display_name:id==='101'?'Codex_杉星_0828':'另一位小机',active:true,activity_state:'active'})),
  spectator_choices:[{id:'101',name:'Codex_杉星_0828'},{id:'102',name:'另一位小机'}],
  viewer:{player_id:'101',seat:0,role:'ai'},
  board_state:{participant_order:['101','102'],players,board:[['blue']],flow:{turn_number:0},royals_available:[]},
  private_state:{legal_actions:[],reserved:[]},
};
const original = JSON.stringify(fixture), calls=[];
w.fetch = async (url,options={}) => {
  calls.push({url,method:options.method||'GET'});
  const perspective = new URL(url,'http://duel.test').searchParams.get('perspective') || '101';
  return {ok:true,json:async()=>({room:{...structuredClone(fixture),viewer:{player_id:perspective,seat:perspective==='101'?0:1,role:'ai'}},timeline:[]})};
};
const flush = () => new Promise(resolve=>setImmediate(resolve));
const json = value => JSON.parse(JSON.stringify(value));
(async()=>{
  try {
    await w.run('openRoom("SPECTATOR_TEST",{perspective:"101",historyMode:"none"})');
    for (const perspective of ['101','102','101']) {
      if (doc.getElementById('spectatorSelect').value !== perspective) {
        const select=doc.getElementById('spectatorSelect'); select.value=perspective;
        select.dispatchEvent(new w.Event('change')); await flush();
      }
      assert.equal(w.run('room.viewer.player_id'),perspective);
      assert.equal(doc.getElementById('turn').textContent,'轮到 Codex_杉星_0828');
      assert.equal(doc.querySelector('.gm-status strong').textContent,'第 1 回合 · Codex_杉星_0828');
      assert.equal(doc.querySelectorAll('.gm-player.is-acting').length,1);
      assert.equal(doc.querySelector('.gm-player.is-acting').dataset.playerId,'101');
      assert.ok(!doc.querySelector('.gm-player-name').textContent.includes('（你）'));
      assert.deepEqual(json(w.run('room.current_actor')),fixture.current_actor);
      assert.equal(w.run('room.current_player_id'),'101');
      assert.equal(w.run('room.revision'),17);
      assert.equal(w.run('canHumanMove()'),false);
      assert.equal(await w.run('submitMove({action:"take"})'),false);
      assert.ok([...doc.querySelectorAll('#gameControls button, .gm-cell')].every(button=>button.disabled));
      assert.equal(doc.getElementById('spectatorPerspective').parentElement,doc.getElementById('turn').parentElement.parentElement,'perspective and actor share one row');
      assert.equal(doc.getElementById('spectatorSelect').title,fixture.spectator_choices.find(p=>p.id===perspective).name);
      doc.getElementById('turn').click();
      assert.equal(doc.querySelector('.pixel-toast').textContent,'轮到 Codex_杉星_0828','tap reveals full actor name');
      doc.getElementById('turn').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
    }
    assert.ok(calls.every(call=>call.method==='GET'),'switches and name reveal are read-only');
    assert.equal(calls.filter(call=>call.url.startsWith('/api/rooms/')).length,3);
    assert.equal(JSON.stringify(fixture),original,'source projection unchanged');

    // A list summary must never borrow the currently spectated room or perspective.
    w.summary={...fixture,room_id:'OTHER',current_actor:{player_id:'101',display_name:'其他房间的行动者',role:'ai'}};
    w.run('renderRooms([summary]);');
    assert.equal(doc.querySelector('#roomList .turn').textContent,'轮到 其他房间的行动者');
    w.summary={...w.summary,spectating:false,viewer:undefined};
    w.run('renderRooms([summary]);');
    assert.equal(doc.querySelector('#roomList .turn').textContent,'轮到 其他房间的行动者','mine list also ignores the currently spectated viewer');
    w.summary={room_id:'LEGACY',game_type:'tictactoe',game_name:'井字棋',status:'playing',turn:'ai',ai_player_id:'999',ai_name:'独立旧房间小机',spectating:true};
    w.run('renderRooms([summary]);');
    assert.equal(doc.querySelector('#roomList .turn').textContent,'轮到 独立旧房间小机');
    w.summary={...w.summary,turn:'human',human_name:'实际人类行动者'};
    assert.equal(w.run('roomTurnText(summary)'),'轮到 实际人类行动者');
    w.summary={...fixture,current_actor:null};
    assert.equal(w.run('roomTurnText(summary)'),'轮到 Codex_杉星_0828','actor fallback uses authoritative current_player_id');
    w.summary={...fixture,game_type:'liars_dice',board_state:{flow:{phase:'awaiting_round_acknowledgement'}}};
    assert.equal(w.run('roomTurnText(summary)'),'本轮已结算 · 等待玩家确认下一轮');

    // Ordinary participants retain their existing second-person labels and controls.
    w.normal={...structuredClone(fixture),spectating:false,viewer:{player_id:'101',seat:0,role:'human'}};
    w.normal.participants[0].role='human'; w.normal.current_actor.role='human';
    w.run('renderGame(normal,"",[]);');
    assert.equal(doc.getElementById('turn').textContent,'轮到你');
    assert.equal(doc.querySelector('.gm-status strong').textContent,'第 1 回合 · 你');
    assert.equal(w.run('canHumanMove()'),true);
    assert.equal(doc.getElementById('turn').hasAttribute('role'),false);
    assert.equal(doc.getElementById('turn').hasAttribute('tabindex'),false);
    assert.ok(doc.getElementById('spectatorPerspective').classList.contains('hidden'));
    w.normal={...w.normal,viewer:{player_id:'102',seat:1,role:'human'}};
    w.run('renderGame(normal,"",[]);');
    assert.equal(doc.getElementById('turn').textContent,'轮到 Codex_杉星_0828');
    assert.equal(doc.querySelector('.gm-status strong').textContent,'第 1 回合 · Codex_杉星_0828');
    console.log('PASS spectator text: two perspectives, actual actor/revision unchanged, read-only GETs, full-name access, isolated list labels, ordinary participant labels. Synthetic DOM only; no screenshots.');
  } finally {w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
