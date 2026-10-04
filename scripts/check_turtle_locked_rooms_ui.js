/* Functional DOM checks. Does not replace Chromium layout/visual review. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const frontend = path.resolve(__dirname, '../turtle-soup/frontend');
const esbuild = require(path.join(frontend, 'node_modules/esbuild'));
const pause = () => new Promise(r => setTimeout(r, 30));
(async () => {
  const compiled = await esbuild.build({entryPoints: [path.join(frontend, 'src/main.jsx')], bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: {'.css': 'empty'}, define: {'process.env.NODE_ENV': '"test"'}});
  const dom = new JSDOM('<div id="root"></div>', {url: 'http://soup.test/soup/', runScripts: 'dangerously', pretendToBeVisual: true});
  const w = dom.window;
  const errors = [];
  w.addEventListener('error', e => errors.push(e.message));
  const player = {id: 1, user_id: 101, username: '自己', is_guest: false};
  const puzzle = {id: 1, title: '电梯与雨伞', surface: '汤面', answer: '汤底', tags: '本格'};
  const creates = [];
  w.localStorage.setItem('cedartoy_user_id', '101');
  w.localStorage.setItem('cedartoy_token', 'platform-test-token');
  w.fetch = async (url, options = {}) => {
    let data = {}, status = 200;
    const endpoint = url.replace('/soup/api', '');
    if (endpoint === '/auth/guest') {
      assert.equal(options.headers.Authorization, 'Bearer platform-test-token');
      data = {token: 'soup-token', player};
    } else if (endpoint === '/auth/me') data = {player};
    else if (endpoint === '/rooms/') data = [true, false].map((is_locked, i) => ({...puzzle, id: `ROOM${i}`, is_locked, status: 'playing'}));
    else if (endpoint === '/puzzles/public') data = [puzzle];
    else if (endpoint === '/puzzles/random' || endpoint === '/game/generate') data = puzzle;
    else if (endpoint === '/rooms/create') {creates.push(JSON.parse(options.body)); status = 400; data = {detail: '测试创建失败'};}
    else if (endpoint.startsWith('/rooms/')) {status = 403; data = {detail: '这是锁房，仅限创建者本人和同一绑定关系的小机进入'};}
    return {ok: status === 200, status, json: async () => data};
  };
  try {
    w.eval(compiled.outputFiles[0].text);
    await pause(); await pause();
    const doc = w.document;
    const panel = () => doc.querySelector('.pixel-create');
    const button = text => [...panel().querySelectorAll('button')].find(el => el.textContent === text);
    const waitFor = async check => {
      for (let i = 0; i < 100 && !check(); i++) await pause();
      assert.ok(check(), 'UI state settled');
    };
    const click = async el => {
      assert.ok(el); el.click(); await pause();
      await waitFor(() => !doc.querySelector('.pixel-primary.loading'));
    };
    const input = (el, value) => {
      Object.getOwnPropertyDescriptor(w.HTMLTextAreaElement.prototype, 'value').set.call(el, value);
      el.dispatchEvent(new w.Event('input', {bubbles: true}));
    };
    const codes = doc.querySelectorAll('.room-code');
    assert.equal(codes[0].textContent, '🔒 房间 #ROOM0');
    assert.equal(codes[1].textContent, '房间 #ROOM1');
    assert.equal(panel().querySelector('[type="checkbox"]').checked, false);
    await click(button('随机抽题'));
    await click(button('创建'));
    assert.equal(creates.at(-1).is_locked, false);
    await click(panel().querySelector('[type="checkbox"]'));
    await click(button('创建'));
    assert.equal(creates.at(-1).is_locked, true);
    assert.equal(panel().querySelector('[type="checkbox"]').checked, true);
    assert.match(panel().textContent, /测试创建失败/);
    await click(button('自填'));
    assert.equal(panel().querySelector('[type="checkbox"]').checked, true);
    const textareas = panel().querySelectorAll('textarea');
    input(textareas[0], '测试汤面'); input(textareas[1], '测试汤底'); await pause();
    await click(button('创建'));
    assert.equal(creates.at(-1).mode, 'custom');
    assert.equal(creates.at(-1).is_locked, true);
    assert.equal(creates.at(-1).surface, '测试汤面');
    await click(button('AI 生成'));
    await click(button('生成'));
    await click(button('创建'));
    assert.equal(creates.at(-1).mode, 'generated');
    assert.equal(creates.at(-1).is_locked, true);
    await click(doc.querySelector('.pixel-room-card'));
    await pause();
    assert.equal(doc.querySelector('[role="alert"]').textContent, '仅限创建者本人和同一绑定关系的小机进入');
    const back = [...doc.querySelectorAll('a')].find(el => el.textContent === '返回大厅');
    await click(back);
    await waitFor(() => doc.querySelectorAll(".pixel-room-card").length === 2);
    assert.equal(doc.querySelectorAll('.pixel-room-card').length, 2);
    assert.equal(panel().querySelector('[type="checkbox"]').checked, false);
    assert.deepEqual(errors, []);
    console.log('turtle locked room DOM checks passed: default, all creation modes, error/retry, room markers, denied direct entry');
  } finally {w.close();}
})().catch(e => {console.error(e); process.exitCode = 1;});
