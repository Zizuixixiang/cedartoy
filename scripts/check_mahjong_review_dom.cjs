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
with tempfile.TemporaryDirectory(prefix='mj-dom-') as tmp:
    demo.database.DB_PATH = Path(tmp) / 'fixture.db'
    demo.database.init_db()
    print(json.dumps(demo.create_at(demo.database.DB_PATH, 'MJDOM001')))
`], {cwd:root,encoding:'utf8'});
  assert.equal(generated.status,0,generated.stderr);
  const fixture=JSON.parse(generated.stdout);
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
  console.log(`Real DOM: terminal review, reentry, original popup unchanged, original actions, ${games.length} other game modals passed.`);
  dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
