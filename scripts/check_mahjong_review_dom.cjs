// Real DOM regression. Uses only a temporary SQLite fixture and local static files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const assets = path.join(root, 'vendor/duel/app/static');
(async () => {
  const {JSDOM} = await import('jsdom');
  const generated = spawnSync(path.join(root,'vendor/duel/.venv/bin/python'), ['-c', `
import json, tempfile
from pathlib import Path
from scripts import create_mahjong_review_demo as demo
from app import invites,framework
with tempfile.TemporaryDirectory(prefix='mj-dom-') as tmp:
    demo.database.DB_PATH = Path(tmp) / 'fixture.db'
    demo.database.init_db()
    fixture=demo.create_at(demo.database.DB_PATH, 'MJDOM001')
    r=invites.create_invite('ka_wu_xing','human','human:1',target_player_count=3,display_name='南杉')
    for i in range(2,4): r=invites.join_invite(r['invite_code'],'human',f'human:{i}',display_name=f'牌友{i}')
    r=invites.start_invite(r['room_id'],'human','human:1')
    print(json.dumps(dict(fixture=fixture,kwx=framework.project_room_for_viewer(r,r['current_player_id']))))
`], {cwd:root,encoding:'utf8'});
  assert.equal(generated.status,0,generated.stderr);
  const {fixture,kwx}=JSON.parse(generated.stdout);
  const dom=new JSDOM(fs.readFileSync(path.join(assets,'index.html'),'utf8'), {url:'https://duel.test/',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window, doc=w.document;
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  for (const file of ['game_ui_registry.js','games/mahjong.js']) w.eval(fs.readFileSync(path.join(assets,file),'utf8'));
  w.eval(fs.readFileSync(path.join(assets,'app.js'),'utf8').replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')+'\nwindow.run = source => eval(source);');
  w.fixture=fixture;
  w.run(`identity = {human_player_id:'1',human_name:'南杉',machines:[],games:[]};renderGame(window.fixture);`);
  const $=id=>doc.getElementById(id);
  assert.ok($('resultModal').classList.contains('hidden'),'initial terminal must not pop up');
  assert.equal($('mahjongTerminalReview').tagName,'SECTION');
  assert.equal(doc.querySelectorAll('.mahjong-terminal-row').length,4);
  assert.equal(doc.querySelectorAll('.mahjong-terminal-meld-group').length,4);
  assert.equal(doc.querySelectorAll('.winning-tile').length,1);
  assert.equal(doc.querySelector('.winning-tile').dataset.cardId,fixture.board_state.game_result.winning_tile.id);
  assert.ok($('mahjongTerminalReview').textContent.includes('演示房'));
  assert.equal($('gameTitle').textContent,'麻将');
  assert.deepEqual([...doc.querySelectorAll('.mahjong-terminal-row')].map(e=>e.dataset.playerId),fixture.participants.map(p=>p.player_id));
  assert.equal(doc.querySelectorAll('.mahjong-fan-row').length,fixture.board_state.game_result.fans.length);
  assert.equal(($('mahjongTerminalReview').textContent.match(/总计/g)||[]).length,1);
  w.run(`room={...window.fixture,status:'playing'};renderGame(window.fixture);`);
  assert.ok(!$('resultModal').classList.contains('hidden'));
  assert.match($('resultModalText').textContent,/南杉.*获胜/);
  assert.equal(doc.activeElement.id,'resultPreserveCheckbox');
  for(const id of ['resultPreserveCheckbox','rematchButton','finishGameButton']) assert.ok($(id));
  $('finishGameButton').click();
  assert.ok($('resultModal').classList.contains('hidden'));
  // Original generic dialog remains unchanged; review is visible on the board.
  assert.equal(doc.querySelectorAll('.mahjong-terminal-row').length,4);
  // An already finished room stays visible after historical reentry.
  w.run(`room=null;renderGame(window.fixture);`);
  assert.ok($('resultModal').classList.contains('hidden'));
  assert.equal(doc.querySelectorAll('.mahjong-terminal-row').length,4);
  const games=[...new Set([...fs.readdirSync(path.join(assets,'games')).filter(f=>f.endsWith('.js')).map(f=>f.slice(0,-3)),
    'gomoku','xiangqi','jungle','liars_dice','tictactoe','othello','connect4','dots_boxes'])].filter(g=>g!=='mahjong');
  for (const game of games){
    w.run(`room={...window.fixture,game_type:${JSON.stringify(game)}};openResultModal('原结算摘要');`);
    assert.equal($('resultModalText').textContent,'原结算摘要',game);
    assert.equal(doc.activeElement.id,'resultPreserveCheckbox',game);
    assert.equal($('rematchButton').disabled,false,game);
  }
  w.run(`room={...window.fixture,game_type:'mahjong',room_kind:'invite'};openResultModal('ignored');`);
  assert.equal($('rematchButton').disabled,true);
  assert.equal(doc.activeElement.id,'resultPreserveCheckbox');
  $('finishGameButton').click();
  // Missing fan data, terminal without hu, and phase transitions use the actual DOM.
  for(const result of [null,{draw:true},{winner_player_id:'1',reason:'resignation'}]){
    w.fixture={...fixture,revision:fixture.revision+1,board_state:{...fixture.board_state,game_result:result}};
    w.run(`room=null;renderGame(window.fixture);`);
    assert.equal(doc.querySelectorAll('.mahjong-terminal-row.winner,.winning-tile,.mahjong-fan-row').length,0);
    assert.ok(!$('mahjongTerminalReview').textContent.includes('总计'));
  }
  for(const base of [fixture,kwx]) {
    const terminal=structuredClone(base),viewer=terminal.viewer.player_id;
    const hand=(terminal.private_state.hand||terminal.board_state.terminal_hands[viewer]).slice(-10);
    const meld={kind:'peng',tiles:Array.from({length:3},(_,i)=>({id:`peng-${i}`,code:'B1',label:'1筒'}))};
    terminal.status='finished';terminal.board_state.phase='finished';
    terminal.private_state.hand=hand;terminal.private_state.own_melds=[meld];
    terminal.board_state.melds[viewer]=[meld];
    terminal.board_state.terminal_hands=Object.fromEntries(terminal.participants.map(p=>[p.player_id,hand]));
    for(const source of ['private','public','result']) {
      const f=structuredClone(terminal);
      if(source!=='private')delete f.private_state.hand;
      if(source==='result') {
        f.board_state.game_result={...f.board_state.game_result,terminal_hands:f.board_state.terminal_hands};
        delete f.board_state.terminal_hands;
      }
      w.fixture=f;
      for(const reentry of [false,true]) {
        w.run('room=null;renderGame(fixture);');
        const tiles=[...doc.querySelectorAll('.mahjong-own-hand-scroll .mahjong-tile')];
        assert.deepEqual(tiles.map(t=>t.dataset.cardId),hand.map(t=>String(t.id)));
        assert.ok(tiles.every(t=>t.disabled&&t.getAttribute('aria-pressed')==='false'));
        tiles[0].click();assert.equal(doc.querySelector('.mahjong-own-hand-scroll .selected'),null);
        assert.equal(doc.querySelectorAll('.position-bottom .mahjong-meld .mahjong-tile').length,3);
        assert.equal(doc.querySelectorAll('.mahjong-controls button:not(:disabled)').length,0);
        const rows=[...doc.querySelectorAll('.mahjong-terminal-row')];assert.equal(rows.length,terminal.participants.length);
        for(const row of rows) {
          assert.equal(row.querySelectorAll('.mahjong-terminal-player .mahjong-terminal-hand-count').length,1);
          assert.equal((row.textContent.match(/手牌 \d+张/g)||[]).length,1);
          assert.equal(row.querySelector('.mahjong-terminal-hand-count').textContent,'手牌 10张');
        }
      }
    }
    const live=structuredClone(terminal);live.status='playing';live.board_state.phase='discard';
    if(live.board_state.flow)live.board_state.flow.phase='discard';
    delete live.private_state.hand;
    w.fixture=live;w.run('room=null;renderGame(fixture);');
    assert.equal(doc.querySelector('.mahjong-terminal-review'),null);
    assert.equal(doc.querySelectorAll('.mahjong-own-hand-scroll .mahjong-tile').length,0,'live room cannot fall back to injected public hands');
  }
  console.log(`Real DOM: terminal review, reentry, original popup unchanged, original actions, ${games.length} other game modals passed.`);
  dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
