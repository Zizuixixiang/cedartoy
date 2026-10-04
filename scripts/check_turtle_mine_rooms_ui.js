/* Functional DOM regression; does not replace browser visual verification. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const frontend = path.resolve(__dirname, '../turtle-soup/frontend');
const esbuild = require(path.join(frontend, 'node_modules/esbuild'));
const pause = () => new Promise(resolve => setTimeout(resolve, 20));
const base = {surface: '下雨天，他终于能直接回家。为什么？', status: 'playing', ask_count: 1, active_players: 1, created_at: '2026-10-01 10:00:00', title: '电梯与雨伞', tags: '本格'};
// Deliberately mixed order, tiers, timestamps, ownership encodings and locks.
const fixtures = [
  {...base, id: 'OF', is_mine: 0, status: 'finished', last_active_at: '2026-10-01 14:00:00'},
  {...base, id: 'MF', is_mine: true, status: 'finished', last_active_at: '2026-10-01 08:00:00', title: '自己的旧汤', tags: '黑汤'},
  {...base, id: 'OQ', is_mine: false, ask_count: 0, last_active_at: '2026-10-01 13:00:00'},
  {...base, id: 'MA', is_mine: 1, last_active_at: '2026-10-01 09:00:00', is_locked: 1},
  {...base, id: 'OA', is_mine: 0, last_active_at: '2026-10-01 12:00:00', is_locked: 1},
  {...base, id: 'MQ', is_mine: true, status: 'waiting', ask_count: 0, last_active_at: '2026-10-01 14:00:00'},
  {...base, id: 'MB', is_mine: 1}, // falls back to created_at, newer than MA
  {...base, id: 'OB'}, // missing flag behaves as non-owned
  {...base, id: 'OC', is_mine: false}, // equal timestamp retains input order
];
const mineOrder = ['MB', 'MA', 'MQ', 'OA', 'OB', 'OC', 'OQ', 'OF', 'MF'];
const legacyOrder = ['OA', 'MB', 'OB', 'OC', 'MA', 'MQ', 'OQ', 'OF', 'MF'];

(async () => {
  const compiled = await esbuild.build({entryPoints: [path.join(frontend, 'src/main.jsx')], bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: {'.css': 'empty'}, define: {'process.env.NODE_ENV': '"test"'}});
  const dom = new JSDOM('<div id="root"></div>', {url: 'http://soup.test/soup/', runScripts: 'dangerously', pretendToBeVisual: true});
  const w = dom.window;
  const doc = w.document;
  const errors = [];
  let rows = fixtures;
  w.addEventListener('error', e => errors.push(e.message));
  w.fetch = async url => {
    const endpoint = url.replace('/soup/api', '');
    // Unrelated profile ID ensures the client relies only on is_mine.
    const player = {id: 999, username: '自己', is_guest: false};
    let data = {};
    if (endpoint === '/auth/guest') data = {token: 'test-token', player};
    else if (endpoint === '/auth/me') data = {player};
    else if (endpoint === '/rooms/') data = rows;
    else if (endpoint === '/puzzles/public') data = [];
    return {ok: true, status: 200, json: async () => data};
  };
  const order = () => [...doc.querySelectorAll('.pixel-room-card')].map(card => card.getAttribute('href').split('/').at(-1));
  const checkOrder = async expected => {
    for (let i = 0; i < 100 && JSON.stringify(order()) !== JSON.stringify(expected); i++) await pause();
    assert.deepEqual(order(), expected);
  };
  const fill = async value => {
    const el = doc.querySelector('[aria-label="按汤名搜索房间"]');
    Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype, 'value').set.call(el, value);
    el.dispatchEvent(new w.Event('input', {bubbles: true}));
    await pause();
  };
  const tag = async text => {
    [...doc.querySelectorAll('.room-tag-filter')].find(el => el.textContent === text).click();
    await pause();
  };
  try {
    w.eval(compiled.outputFiles[0].text);
    await checkOrder(mineOrder);
    const checkLabels = () => {
      for (const room of rows) {
        const code = doc.querySelector(`a[href="/soup/room/${room.id}"] .room-code`);
        assert.equal(code.textContent, `${room.is_locked ? '🔒 ' : ''}${room.is_mine ? '我的房间' : '房间'} #${room.id}`);
        assert.equal(code.querySelector('.room-id-mine')?.textContent, room.is_mine ? '我的房间' : undefined);
      }
    };
    checkLabels();
    await fill('自己的旧汤');
    await checkOrder(['MF']);
    await tag('本格');
    await checkOrder([]); // own room cannot bypass combined filters
    await fill('');
    await checkOrder(mineOrder.filter(id => id !== 'MF'));
    await tag('黑汤');
    await checkOrder(mineOrder); // existing OR semantics for tags
    await tag('本格');
    await checkOrder(['MF']);
    await tag('黑汤');
    await fill('无匹配');
    await checkOrder([]);
    await fill('');
    await checkOrder(mineOrder);
    // Legacy API responses preserve the exact old order.
    rows = fixtures.map(({is_mine, ...room}) => room);
    doc.querySelector('[aria-label="刷新"]').click();
    await checkOrder(legacyOrder);
    checkLabels();
    assert.deepEqual(legacyOrder.filter(id => id.startsWith('O')), mineOrder.filter(id => id.startsWith('O')));
    // Refreshed ownership must replace the previous identity's flags.
    rows = fixtures.map(room => ({...room, is_mine: room.id === 'OQ' || room.id === 'OF'}));
    doc.querySelector('[aria-label="刷新"]').click();
    await checkOrder(['OQ', ...legacyOrder.filter(id => id !== 'OQ')]);
    checkLabels();
    // Owning either finished room must not change its timestamp-based order.
    for (const ownedId of ['OF', 'MF']) {
      rows = fixtures.map(room => ({...room, is_mine: room.id === ownedId}));
      doc.querySelector('[aria-label="刷新"]').click();
      await checkOrder(legacyOrder);
      await pause(); // order may already match while refreshed labels settle
      checkLabels();
    }
    assert.deepEqual(errors, []);
    console.log('turtle own-room DOM checks passed: unfinished own-first, finished-last regardless of ownership, tiers/time/fallback/ties, unchanged others, search/tags, legacy flags, refresh, lock marker');
  } finally {w.close();}
})().catch(e => {console.error(e); process.exitCode = 1;});
