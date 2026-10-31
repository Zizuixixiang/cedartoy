/* Real isolated-db web projections + DOM/race checks. No screenshot claim. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const duel = path.join(root, 'vendor/duel');
const generated = spawnSync(process.env.DUEL_TEST_PYTHON || path.join(duel, '.venv/bin/python'),
  ['-m', 'tests.spectator_ui_fixture'], {cwd: duel, encoding: 'utf8', maxBuffer: 20 * 1024 * 1024, timeout: 60000});
assert.equal(generated.status, 0, generated.stderr);
const fixtures = JSON.parse(generated.stdout);
const staticDir = path.join(duel, 'app/static');
const dom = new JSDOM(fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8'),
  {url: 'http://duel.test/', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, doc = w.document;
w.HTMLElement.prototype.scrollIntoView = () => {};
w.scrollTo = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
w.ResizeObserver = class { observe() {} disconnect() {} };
w.fetch = async () => ({ok: true, json: async () => ({ok: true})});
const app = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
w.eval(fs.readFileSync(path.join(staticDir, 'game_ui_registry.js'), 'utf8'));
for (const name of fs.readdirSync(path.join(staticDir, 'games')).filter(n => n.endsWith('.js'))) {
  w.eval(fs.readFileSync(path.join(staticDir, 'games', name), 'utf8'));
}
w.eval(app.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '') + '\nwindow.run = source => eval(source);');
w.catalog = fixtures.catalog;
w.run(`identity={human_player_id:'human:owner',human_name:'主人',machines:[{id:'101',name:'小紫'},{id:'102',name:'小蓝'}],games:catalog};
  startRoomPolling=()=>{};loadIdentity=async()=>{};`);
const show = payload => { w.payload = structuredClone(payload); w.run('renderGame(payload.room,"",payload.timeline);'); };
const json = value => JSON.parse(JSON.stringify(value));
const pending = [];
const posts = [];
const fetchPending = () => {
  w.fetch = (url, options = {}) => {
    if (options.method === 'POST') posts.push(url);
    return new Promise(resolve => pending.push({url, resolve: (data, status = 200) => resolve({ok: status === 200, status, json: async () => data})}));
  };
};
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  try {
    for (const entry of fixtures.games) {
      show(entry.waiting);
      assert.deepEqual([...doc.querySelectorAll('#inviteRoomPanel button')].map(b => b.textContent), ['返回大厅'], `${entry.game}: waiting offers navigation only`);
      assert.equal(doc.querySelector('#roomId').textContent, entry.waiting.room.room_id);
      for (const view of entry.views) {
        show(view);
        assert.equal(w.run('canHumanMove()'), false, entry.game);
        assert.equal(w.run('viewerPlayerIdFor(room)'), view.room.viewer.player_id, entry.game);
        assert.equal(doc.querySelector('#spectatorSelect').value, view.room.viewer.player_id, entry.game);
        assert.equal(doc.querySelector('#spectatorPerspective').classList.contains('hidden'), false);
        assert.ok(doc.querySelector('#board').childNodes.length, `${entry.game} rendered`);
        assert.ok([...doc.querySelectorAll('#gameControls button, #gameControls input, #gameControls select')].every(n => n.disabled));
        assert.equal(await w.run('submitMove({action:"bet"},{sideAction:true})'), false);
      }
    }
    show(fixtures.terminal);
    assert.equal(w.run('shouldLongPollRoom()'), true, 'terminal spectators still revalidate binding');
    assert.equal(doc.querySelector('#togglePreserveButton').disabled, true);
    assert.equal(doc.querySelector('#resultModal').classList.contains('hidden'), true);
    const single = structuredClone(fixtures.uno[0]);
    single.room.spectator_choices = single.room.spectator_choices.filter(p => p.id === '101');
    show(single);
    assert.equal(doc.querySelector('#spectatorSelect').classList.contains('hidden'), true);
    assert.equal(doc.querySelector('#spectatorName').textContent, single.room.spectator_choices[0].name);
    w.run('setRoomListScope("machines")');
    w.summaries = fixtures.rooms;
    w.run('renderRooms(summaries);');
    assert.match(doc.querySelector('.room-enter').textContent, /围观/);
    assert.equal(doc.querySelectorAll('.room-record-button').length, 0);
    w.summaries = [{...fixtures.rooms[0],status:'finished'}];
    w.run('renderRooms(summaries);');
    assert.equal(doc.querySelectorAll('.room-record-button').length, 0);

    // Stale fetches deliberately ignore AbortController to simulate buffered replies.
    fetchPending();
    show(fixtures.uno[0]);
    const oldContext = w.run('createGameUIContext($("board"),$("gameControls"))');
    const first = w.run('openRoom(room.room_id,{historyMode:"none",perspective:"101"})');
    const second = w.run('openRoom(room.room_id,{historyMode:"none",perspective:"102"})');
    pending[1].resolve(fixtures.uno[1]); await second;
    pending[0].resolve(fixtures.uno[0]); await first;
    pending.length = 0;
    assert.equal(w.run('room.viewer.player_id'), '102', 'late open cannot replace selected perspective');
    assert.equal(oldContext.helpers.rerender(), false, 'old renderer callback cannot render a new perspective');
    assert.deepEqual(json(w.run('room.private_state')), fixtures.uno[1].room.private_state);
    const refresh = w.run('refreshRoom({quiet:true,generation:roomSyncGeneration})');
    const switchBack = w.run('openRoom(room.room_id,{historyMode:"none",perspective:"101"})');
    assert.match(pending[0].url, /perspective=102/);
    pending[1].resolve(fixtures.uno[0]); await switchBack;
    pending[0].resolve(fixtures.uno[1]); await refresh;
    pending.length = 0;
    assert.equal(w.run('room.viewer.player_id'), '101', 'late poll cannot replace new perspective');
    const poll = w.run('refreshRoom({quiet:true,generation:roomSyncGeneration})');
    pending[0].resolve(fixtures.uno[0]);
    assert.equal((await poll).retryAfterMs, 3000, 'short polls do not hot-loop');
    pending.length = 0;
    const revoked = w.run('refreshRoom({quiet:true,generation:roomSyncGeneration})');
    pending[0].resolve({message:'绑定已失效'}, 403);
    await flush();
    assert.match(pending[1].url, /spectating\/rooms/);
    pending[1].resolve({rooms:[]}); await revoked;
    pending.length = 0;
    assert.equal(w.run('room'), null, 'revocation closes the private view');
    assert.equal(doc.querySelector('#lobbyView').classList.contains('hidden'), false);
    show(fixtures.uno[0]);
    const back = w.run('backToLobby({fromHistory:true})');
    assert.match(pending[0].url, /spectating\/rooms/);
    pending[0].resolve({rooms:[]}); await back;
    pending.length = 0;
    assert.equal(posts.length, 0, 'spectator exit/polls never POST, ack, leave or move');

    const list1 = w.run('loadSpectatorRooms()');
    const list2 = w.run('loadSpectatorRooms()');
    pending[1].resolve({rooms:[]}); await list2;
    pending[0].resolve({rooms:fixtures.rooms}); await list1;
    assert.equal(doc.querySelectorAll('.room-card').length, 0, 'stale list cannot overwrite refresh');
    console.log(`PASS: ${fixtures.games.length} games × 2 perspectives, waiting/terminal, read-only UI, list/open/poll races and binding revocation. DOM only; no visual verification.`);
  } finally {
    dom.window.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
