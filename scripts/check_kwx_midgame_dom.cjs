// Actual production renderer + isolated engine projection; DOM is not visual proof.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
(async () => {
  const {JSDOM} = await import('jsdom');
  const root = path.resolve(__dirname, '..');
  const assets = path.join(root, 'vendor/duel/app/static');
  const generated = spawnSync(path.join(root, 'vendor/duel/.venv/bin/python'), ['-c', `
import json, tempfile
from pathlib import Path
from scripts import create_kwx_midgame_demo as demo
with tempfile.TemporaryDirectory(prefix='kwx-dom-') as tmp:
    demo.database.DB_PATH = Path(tmp)/'fixture.db'
    demo.database.init_db()
    print(json.dumps(demo.create_at(demo.database.DB_PATH)[1]))
`], {cwd:root, encoding:'utf8'});
  assert.equal(generated.status, 0, generated.stderr);
  const fixture = JSON.parse(generated.stdout);
  const dom = new JSDOM(fs.readFileSync(path.join(assets, 'index.html'), 'utf8'),
    {url:'https://duel.test/', runScripts:'outside-only', pretendToBeVisual:true});
  try {
    const w = dom.window, doc = w.document;
    w.matchMedia = () => ({matches:false,addEventListener(){},removeEventListener(){}});
    for (const file of ['game_ui_registry.js','games/mahjong.js']) w.eval(fs.readFileSync(path.join(assets,file),'utf8'));
    w.eval(fs.readFileSync(path.join(assets,'app.js'),'utf8').replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')+'\nwindow.run = source => eval(source);');
    w.fixture = fixture;
    w.run(`identity={human_player_id:'1',human_name:'南杉',machines:[],games:[]}; renderGame(window.fixture);`);
    assert.equal(doc.querySelectorAll('.three-player .mahjong-discards').length, 3);
    assert.deepEqual([...doc.querySelectorAll('.mahjong-discard-grid')].map(e=>e.children.length).sort(),[10,10,11]);
    assert.equal(doc.querySelectorAll('.mahjong-own-hand-scroll .mahjong-tile').length,13);
    assert.equal(doc.querySelectorAll('.mahjong-own-hand-scroll button:not(:disabled)').length,0);
    assert.equal(doc.querySelectorAll('.mahjong-controls button:not(:disabled)').length,0);
    assert.match(doc.getElementById('gameTitle').textContent,/演示\/只读/);
    assert.match(doc.getElementById('turn').textContent,/只读中盘/);
    assert.ok(doc.getElementById('spectatorPerspective').classList.contains('hidden'));
    assert.ok(doc.getElementById('sendMessageButton').disabled);
    assert.ok(doc.getElementById('resultModal').classList.contains('hidden'));
    // The ordinary room still has working discard actions after leaving the demo.
    const ordinary = {...fixture, demo:false,read_only:false,spectating:false,
      current_player_id:'1', current_player:{...fixture.participants[0]}, current_actor:{...fixture.participants[0]},
      private_state:{...fixture.private_state,legal_actions:[{kind:'discard', action_id:'discard:'+fixture.private_state.hand[0].id,tile_id:fixture.private_state.hand[0].id}]}};
    w.fixture = ordinary;
    w.run('room=null;renderGame(window.fixture);');
    assert.ok(doc.querySelectorAll('.mahjong-own-hand-scroll button:not(:disabled)').length>0);
    console.log('KaWuXing real midgame DOM, human hand isolation, read-only controls, ordinary reentry: passed');
  } finally {dom.window.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
