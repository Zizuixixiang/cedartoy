/* Functional DOM fallback; does not replace the matching Chromium visual check. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const frontend = path.resolve(__dirname, '../turtle-soup/frontend');
const esbuild = require(path.join(frontend, 'node_modules/esbuild'));
const pause = () => new Promise(resolve => setTimeout(resolve, 20));
(async () => {
  const compiled = await esbuild.build({entryPoints: [path.join(frontend, 'src/main.jsx')], bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: {'.css': 'empty'}, define: {'process.env.NODE_ENV': '"test"'}});
  const dom = new JSDOM('<div id="root"></div>', {url: 'http://soup.test/soup/room/LOCK1234', runScripts: 'dangerously', pretendToBeVisual: true});
  const w = dom.window, errors = [], streams = [], writes = [];
  const player = {id: 7, user_id: 201, username: '南杉', is_guest: false, is_admin: true};
  let denied = false;
  w.addEventListener('error', event => errors.push(event.message));
  w.matchMedia = () => ({matches: true, addEventListener() {}, removeEventListener() {}});
  w.HTMLElement.prototype.scrollTo = function() {};
  w.EventSource = class {
    constructor(url) {this.url = url; this.closed = false; streams.push(this);}
    addEventListener() {}
    close() {this.closed = true;}
  };
  w.localStorage.setItem('turtle_soup_token', 'fixture-token');
  w.localStorage.setItem('hint_decisions_LOCK1234', '{"2":"reject"}');
  w.fetch = async (url, options = {}) => {
    let data = {}, status = 200;
    const endpoint = url.replace('/soup/api', '');
    if (options.method && options.method !== 'GET') writes.push(endpoint);
    if (endpoint === '/auth/me') data = {player};
    else if (endpoint === '/game/public-settings') data = {answer_reveal_prompt_count: 100};
    else if (endpoint.startsWith('/rooms/')) {
      const id = decodeURIComponent(endpoint.slice('/rooms/'.length));
      if (denied) {status = 403; data = {detail: '这是锁房，仅限创建者本人和同一绑定关系的小机进入'};}
      else data = {id: id.replace('#', ''), title: '电梯与雨伞', surface: '测试汤面', is_locked: 1,
        status: id === 'FINISHED' ? 'finished' : 'playing', created_by: 7,
        admin_readonly: id !== '#MEMBER', ask_count: id === '#MEMBER' ? 0 : 100, active_players: 0,
        logs: id === '#MEMBER' ? [] : [
          {id: 1, type: 'ask', content: '已有问题', judgment: 'yes', username: '房主'},
          {id: 2, type: 'auto_hint', content: '已有自动提示'},
          {id: 3, type: 'hint_offer', hint_text: '已有手动提示', player_id: 7, resolved: 0},
        ], notes: []};
    }
    return {ok: status === 200, status, json: async () => data};
  };
  try {
    const doc = w.document;
    const waitFor = async check => {for (let i = 0; i < 150 && !check(); i++) await pause(); assert.ok(check(), 'UI settled');};
    const go = async id => {w.history.pushState({}, '', '/soup/room/' + id); w.dispatchEvent(new w.PopStateEvent('popstate')); await pause();};
    const checkReadonly = async () => {
      await waitFor(() => doc.querySelector('.room-readonly-notice'));
      assert.equal(doc.querySelector('.room-readonly-notice').textContent, '管理员只读查看');
      assert.equal(doc.querySelector('.room-composer, .notepad-drawer, .notepad-drawer-tab, .hint-actions, .log-hint-label-note, .answer-reveal-prompt, .room-close-backdrop, .close-room-btn, textarea'), null);
      assert.match(doc.querySelector('.session-log-stream').textContent, /已有问题/);
      assert.match(doc.querySelector('.session-log-stream').textContent, /已有自动提示/);
      assert.match(doc.querySelector('.surface-head-meta').textContent, /在房 0/);
      assert.equal(w.localStorage.getItem('answer_reveal_prompt_last_LOCK1234'), null);
      assert.equal(w.localStorage.getItem('hint_decisions_LOCK1234'), '{"2":"reject"}');
      assert.ok(streams.every(stream => stream.closed));
    };
    w.eval(compiled.outputFiles[0].text);
    await checkReadonly();
    assert.equal(streams.length, 0);
    await go('FINISHED');
    await checkReadonly();
    assert.equal(w.localStorage.getItem('hint_decisions_FINISHED'), null);
    await go('%23MEMBER');
    await waitFor(() => doc.querySelector('.room-composer') && streams.some(stream => !stream.closed));
    assert.ok(doc.querySelector('.notepad-drawer-tab'));
    assert.ok(doc.querySelector('.close-room-btn'));
    await go('LOCK1234');
    await checkReadonly();
    assert.ok(streams.every(stream => stream.url.includes('/#MEMBER?')));
    denied = true;
    await go('DENIED');
    await waitFor(() => doc.querySelector('[role="alert"]'));
    assert.equal(doc.querySelector('.room-play'), null);
    assert.deepEqual(writes, []);
    assert.deepEqual(errors, []);
    console.log('turtle admin readonly DOM checks passed: no SSE/writes/actions/prompts, logs, finished, member transition, denied');
  } finally {w.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
