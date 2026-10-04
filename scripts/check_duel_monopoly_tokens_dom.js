// DOM-only companion to the browser geometry checks; all game data is synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const eventCases = require('./monopoly_event_cases');

(async () => {
  const {JSDOM} = await import('jsdom');
  const fixture = spawnSync('.venv/bin/python', ['-c', 'import json; from app.games.monopoly import new_tiles; print(json.dumps(new_tiles()))'], {cwd: path.join(root, 'vendor/duel'), encoding: 'utf8'});
  assert.equal(fixture.status, 0, fixture.stderr);
  const tiles = JSON.parse(fixture.stdout);
  const dom = new JSDOM('<div id="board"></div><div id="controls"></div>', {runScripts: 'outside-only', url: 'http://duel.test/'});
  const {window} = dom;
  let renderer;
  window.DuelGameUI = {register: (key, value) => {assert.equal(key, 'monopoly'); renderer = value;}};
  window.HTMLDialogElement.prototype.showModal = function () {this.setAttribute('open', '');};
  window.HTMLDialogElement.prototype.close = function () {this.removeAttribute('open'); this.dispatchEvent(new window.Event('close'));};
  window.eval(fs.readFileSync(path.join(root, 'vendor/duel/app/static/games/monopoly.js'), 'utf8'));
  const board = window.document.getElementById('board'), controls = window.document.getElementById('controls');
  assert.equal(renderer.usesEmbeddedActionFeedback, true);
  const seaStreet = tiles.find(t => t.name === '海街');
  assert.equal(seaStreet.rents[0], 26);
  let detailChecks = 0;
  function inspectTile(tile, expectedLabel, expectedValue) {
    const state = {players: [{player_id: 'test:0', position: 0, cash: 1500}], tiles: structuredClone(tiles), phase: 'manage'};
    Object.assign(state.tiles[tile.id], tile);
    const before = JSON.stringify(state);
    const c = {board, controls, state, participants: state.players, room: {room_id: 'rent-dom', current_player_id: 'test:0'}, viewer: {player_id: 'test:0'}, uiState: {}, canMove: false, legalActions: [], privateState: {}, helpers: {renderParticipantAvatar: () => {}, canMove: () => false}};
    board.replaceChildren(); renderer.renderBoard(c);
    board.querySelector(`[data-tile-id="${tile.id}"]`).click();
    const detail = board.querySelector('dialog[open]');
    const values = Object.fromEntries([...detail.querySelectorAll('.monopoly-values dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]));
    assert.equal(values[expectedLabel], expectedValue);
    if (expectedLabel !== '当前租金') assert.equal(values['当前租金'], undefined);
    const rows = [...detail.querySelectorAll('.monopoly-rents tbody tr')];
    if (tile.kind === 'property' && Array.isArray(tile.rents) && tile.rents.length === 6) {
      assert.equal(detail.querySelector('.monopoly-rents caption').textContent, '租金');
      assert.deepEqual(rows.map(row => row.querySelector('th').firstChild.textContent), ['空地', '1 级建筑', '2 级建筑', '3 级建筑', '4 级建筑', '旅馆']);
      assert.deepEqual(rows.map(row => row.querySelector('td').textContent), tile.rents.map(r => r.toLocaleString('zh-CN')));
      assert.deepEqual(rows.map((row, i) => row.classList.contains('current') ? i : -1).filter(i => i >= 0), [tile.level || 0]);
    } else if (tile.kind !== 'property') {
      assert.equal(rows.length, 0);
      assert.equal(values['每级建造'], undefined);
      assert.doesNotMatch(detail.textContent, /级建筑|旅馆/);
      assert.match(detail.querySelector('.monopoly-help').textContent, tile.kind === 'railroad' ? /1 \/ 2 \/ 3 \/ 4 座车站.*25 \/ 50 \/ 100 \/ 200/ : /一处设施.*4 倍.*两处.*10 倍/);
    }
    detail.querySelector('[aria-label="关闭对话框"]').click();
    assert.equal(JSON.stringify(state), before);
    detailChecks++;
  }
  inspectTile({...seaStreet, owner: null, rent: 0}, '购买后基础租金', '26');
  inspectTile({...seaStreet, owner: undefined, rent: 0}, '购买后基础租金', '26');
  inspectTile({...seaStreet, owner: 'test:0', rent: 52}, '当前租金', '52');
  inspectTile({...seaStreet, owner: 'test:0', level: 3, rent: 800}, '当前租金', '800');
  inspectTile({...seaStreet, owner: 'test:0', mortgaged: true, rent: 0}, '当前收租', '已抵押，暂停收租');
  for (const rents of [undefined, null, [], [null], [NaN], [-1], 'bad']) {
    inspectTile({...seaStreet, owner: null, rent: 0, rents}, '购买后基础租金', '—');
  }
  inspectTile({...seaStreet, owner: null, rent: 0, rents: [0]}, '购买后基础租金', '0');
  for (const kind of ['railroad', 'utility']) inspectTile({...tiles.find(t => t.kind === kind), rent: 0}, '当前租金', '0');
  let checks = 0;
  for (const count of [2, 4, 6]) {
    const participants = Array.from({length: count}, (_, i) => ({player_id: `test:${i}`, display_name: `玩家${i + 1}`, seat_index: i}));
    const positions = [...tiles.map(t => Array(count).fill(t.id)), [1, 11, 21, 31, 5, 15], [0, 10, 20, 30, 0, 10], [3, 3, 3, 3, 21, 31], [1, 1, 21, 31, 5, 15]];
    for (const locations of positions) {
      const state = {players: participants.map((p, i) => ({...p, position: locations[i], cash: 1500})), tiles: structuredClone(tiles), action_seq: 7, phase: 'manage'};
      state.tiles.filter(t => t.kind === 'property').forEach((t, i) => {t.owner = participants[i % count].player_id; t.level = i % 5 + 1;});
      const before = JSON.stringify(state), sent = [];
      const c = {board, controls, state, participants, room: {room_id: 'token-dom', current_player_id: 'test:0'}, viewer: {player_id: 'test:0'}, uiState: {}, canMove: true, legalActions: [{action: 'end_turn'}], privateState: {}, helpers: {renderParticipantAvatar: () => {}, canMove: () => true, submitMove: async move => {sent.push(move); return true;}}};
      board.replaceChildren(); controls.replaceChildren(); renderer.renderBoard(c); renderer.renderControls(c);
      assert.equal(board.querySelectorAll('.monopoly-tile').length, 40);
      assert.equal(board.querySelector('.monopoly-token-links'), null);
      assert.equal(board.querySelectorAll('.monopoly-seat-badge').length, count);
      assert.equal(board.querySelectorAll('.monopoly-tokens .is-viewer').length, 1);
      assert.equal(board.querySelector('.monopoly-tokens .is-viewer').dataset.playerId, 'test:0');
      assert.equal(Number(board.querySelector('.is-viewer-tile').dataset.tileId), locations[0]);
      assert.equal(board.querySelector('.monopoly-location').textContent, `你当前在：${state.tiles[locations[0]].name}${locations[0] === 10 ? '（只是探访，未入狱）' : ''}`);
      const tokens = [...board.querySelectorAll('.monopoly-tokens .monopoly-token')];
      assert.equal(tokens.length, count);
      for (const token of tokens) {
        const i = participants.findIndex(p => p.player_id === token.dataset.playerId);
        const tile = token.closest('.monopoly-tile');
        assert.equal(Number(tile.dataset.tileId), locations[i]);
        assert.equal(token.textContent, String(i + 1));
        assert.ok(tile.classList.contains('occupied'));
        assert.equal(token.parentElement.parentElement, tile);
        assert.equal(token.parentElement.style.translate, '');
        assert.equal(token.parentElement.style.transform, '');
      }
      for (const tile of board.querySelectorAll('.monopoly-tile')) {
        const expected = state.tiles[Number(tile.dataset.tileId)];
        assert.equal(tile.querySelector('.monopoly-tile-name').textContent, expected.name);
        if (expected.owner) assert.ok(tile.querySelector('.monopoly-tile-markers .monopoly-owner'));
        if (expected.level) assert.ok(tile.querySelector('.monopoly-tile-markers .monopoly-buildings'));
      }
      board.querySelector('[data-tile-id="3"]').click();
      assert.ok(board.querySelector('dialog[open]'));
      board.querySelector('[aria-label="关闭对话框"]').click();
      assert.equal(board.querySelector('dialog'), null);
      controls.querySelector('[data-action="end_turn"]').click();
      await Promise.resolve();
      assert.equal(sent.length, 1);
      assert.equal(sent[0].action, 'end_turn');
      assert.equal(sent[0].action_seq, 7);
      assert.equal(JSON.stringify(state), before, 'rendering and dialog inspection must not mutate game state');
      checks++;
    }
  }
  const base = {participants: [1,2].map(i=>({player_id:`human:${i}`,display_name:`玩家${i}`})), board_state: {players: [1,2].map(i=>({player_id:`human:${i}`})), tiles}};
  const cases = eventCases(base);
  const plan = (a,b) => JSON.parse(JSON.stringify(renderer.monopolyTransitionBeats(a,b)));
  for (const c of cases) {
    const original = JSON.stringify(c);
    const beats = plan(c.before,c.after);
    assert.deepEqual(beats.map(b=>b.kind), c.label==='roll'?[]:c.label==='multi'?['chance','rent']:[c.label], c.label);
    assert.ok(beats.every(b=>b.duration>=1200&&b.duration<=2000));
    assert.deepEqual(plan(c.after,c.after),[]);
    assert.deepEqual(plan(null,c.after),[]);
    assert.deepEqual(plan(c.before,{...c.after,room_id:'different'}),[]);
    assert.equal(JSON.stringify(c),original,'beat planning is pure');
  }
  const auction=structuredClone(cases.find(c=>c.label==='auction'));
  auction.after.board_state.last_action_note=auction.after.board_state.last_action_note.replace('220','245');
  assert.match(plan(auction.before,auction.after)[0].text,/245/,'final accepted bid wins over old auction bid');
  const rent=structuredClone(cases.find(c=>c.label==='rent'));
  rent.after.board_state.last_action_note='';
  assert.deepEqual(plan(rent.before,rent.after),[],'do not invent a payment from net cash');
  const trade=structuredClone(cases.find(c=>c.label==='trade'));
  const missed=structuredClone(trade);missed.before.board_state.trade=null;
  assert.equal(plan(missed.before,missed.after)[0].kind,'trade','acknowledge a confirmed trade even when the proposal was not polled');
  trade.after.board_state.last_action_note='交易已拒绝。';
  assert.deepEqual(plan(trade.before,trade.after),[],'a rejected trade is not a completed trade');
  dom.window.close();
  console.log(JSON.stringify({ok: true, checks, detailChecks, eventCases:cases.length, players: [2, 4, 6], geometry: 'requires check_duel_monopoly_browser.js'}));
})().catch(e => {console.error(e); process.exitCode = 1;});
