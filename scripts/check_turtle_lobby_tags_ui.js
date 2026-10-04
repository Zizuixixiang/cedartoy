/* DOM regression only; layout and computed colors need the companion browser check. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const frontend = path.resolve(__dirname, '../turtle-soup/frontend');
const esbuild = require(path.join(frontend, 'node_modules/esbuild'));
const tags = ['本格', '变格', '清汤', '黑汤', '红汤', '规则怪谈'];
const base = {surface: '测试汤面', ask_count: 1, active_players: 1, created_at: '2026-10-01 12:00:00'};
const fixtures = [
  {...base, id: 'RED', title: '红色房间', status: 'playing', tags: '本格， 红汤 '},
  {...base, id: 'BLACK', title: '普通房间', status: 'finished', tags: ['变格', '黑汤']},
  {...base, id: 'RULE', title: '规则房间', status: 'waiting', tags: '清汤、规则怪谈'},
];
const pause = () => new Promise(resolve => setTimeout(resolve, 20));

(async () => {
  const compiled = await esbuild.build({entryPoints: [path.join(frontend, 'src/main.jsx')], bundle: true,
    write: false, format: 'iife', jsx: 'automatic', loader: {'.css': 'empty'},
    define: {'process.env.NODE_ENV': '"test"'}});
  const dom = new JSDOM('<div id="root"></div>', {url: 'http://soup.test/soup/', runScripts: 'dangerously', pretendToBeVisual: true});
  const w = dom.window;
  const doc = w.document;
  const errors = [];
  w.addEventListener('error', event => errors.push(event.message));
  w.fetch = async url => {
    const endpoint = url.replace('/soup/api', '');
    const player = {id: 1, username: '测试游客', is_guest: true};
    const data = endpoint === '/auth/guest' ? {token: 'fixture-token', player}
      : endpoint === '/auth/me' ? {player} : endpoint === '/rooms/' ? fixtures
      : endpoint === '/puzzles/public' ? [] : {};
    return {ok: true, status: 200, json: async () => data};
  };
  const checkCards = async expected => {
    const ids = () => [...doc.querySelectorAll('.pixel-room-card')].map(el => el.getAttribute('href').split('/').at(-1)).sort();
    for (let i = 0; i < 100 && JSON.stringify(ids()) !== JSON.stringify(expected); i++) await pause();
    assert.deepEqual(ids(), expected);
  };
  try {
    w.eval(compiled.outputFiles[0].text);
    await checkCards(['BLACK', 'RED', 'RULE']);
    const buttons = [...doc.querySelectorAll('.room-tag-filter')];
    assert.deepEqual(buttons.map(el => el.textContent), tags);
    const badges = [...doc.querySelectorAll('.pixel-room-card .soup-badge')];
    for (const tag of tags) {
      const badge = badges.find(el => el.textContent === tag);
      assert.ok(badge, `${tag} is rendered in full`);
      assert.equal(badge.className, tag === '红汤' ? 'soup-badge red' : 'soup-badge');
    }
    for (const room of fixtures) {
      const status = doc.querySelector(`a[href="/soup/room/${room.id}"] .room-meta`).lastElementChild;
      assert.equal(status.className, `soup-badge ${room.status === 'finished' ? 'pale' : 'playing'}`);
      assert.equal(status.textContent, room.status === 'finished' ? '已结束' : '进行中');
    }
    const red = buttons.find(el => el.textContent === '红汤');
    red.click();
    await checkCards(['RED']);
    assert.equal(red.getAttribute('aria-pressed'), 'true');
    red.click();
    await checkCards(['BLACK', 'RED', 'RULE']);
    buttons.find(el => el.textContent === '规则怪谈').click();
    await checkCards(['RULE']);
    assert.deepEqual(errors, []);
    console.log('turtle lobby tag DOM checks passed');
  } finally {w.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
