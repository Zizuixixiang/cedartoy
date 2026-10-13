'use strict';
// Run the full homepage with mocked APIs: no real accounts, databases or saves.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const html = fs.readFileSync(path.resolve(__dirname, '../index.html'), 'utf8');
const user = {id: 1, username: '测试人类', is_ai: false};
const machine = {id: 101, username: '测试小机', is_ai: true};
const reply = (data, status = 200) => ({ok: status === 200, status, json: async () => data});
const gate = () => { let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve}; };
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
async function until(predicate) {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await tick(); }
  assert.fail('state did not settle');
}

async function fixture(options = {}) {
  const calls = [], errors = [], navigations = [];
  let failure = options.failure;
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => {
    if (error.type === 'not-implemented' && /navigation/.test(error.message)) return;
    errors.push(error.message);
  });
  const dom = new JSDOM(html, {url: 'https://login.test/' + (options.query || ''),
    runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole,
    beforeParse(w) {
      if (options.authenticated) w.localStorage.setItem('cedartoy_token', 'initial-token');
      w.fetch = async (url, init = {}) => {
        calls.push({url, init});
        if (url === '/api/auth/login' || url === '/api/auth/register') {
          return reply({token: 'fixture-token', user: {...user, ...options.user}, pending_deletion: !!options.pendingDeletion});
        }
        if (url === '/api/auth/me') {
          if (options.meGate) await options.meGate.promise;
          if (failure === 'network') throw new Error('network offline');
          if (typeof failure === 'number') return reply({}, failure);
          if (failure === 'json') return {ok: true, json: async () => {throw new Error('invalid JSON');}};
          return reply(failure === 'invalid' ? {user} : {user, bindings: options.empty ? [] : [machine]});
        }
        if (url === '/api/announcements') {
          const authenticated = !!init.headers?.Authorization;
          return reply({authenticated, unread_count: authenticated ? 1 : 0,
            announcements: authenticated ? [{id: 'fixture', title: '测试通知', content: '只弹一次', unread: true}] : []});
        }
        if (url === '/api/auth/saves?game=camping_plaza') return reply({machines: [{user: machine,
          saves: {camping_plaza: {slots: [{slot: 1, player_name: '营地一'}, {slot: 2, player_name: '营地二'}]}}}]});
        if (url === '/soup/api/rooms/') return reply([]);
        return reply({});
      };
    }});
  const w = dom.window, $ = id => w.document.getElementById(id);
  let popups = 0;
  const originalShow = w.showAnnouncements;
  w.showAnnouncements = (...args) => {popups++; return originalShow(...args);};
  // Observe the unchanged final routing helper after exercising the real slot picker.
  w.watchCampingPlaza = (...args) => navigations.push(args);
  await until(() => w.eval('announcementState.loaded'));
  const count = url => calls.filter(c => c.url === url).length;
  async function login(register = false) {
    w.openModal('loginModal');
    if (register) w.setLoginMode('register');
    $('loginUser').value = 'TestHuman'; $('loginPass').value = 'fixture-password';
    if (register) $('loginConfirmPass').value = 'fixture-password';
    await w.login();
  }
  async function enter() {w.closeModals(); w.eval("selected = 'camping_plaza'"); await w.enterWatch();}
  async function ready() {await until(() => w.eval('me && Array.isArray(me.bindings)'));}
  const notice = () => $('pixelToast').textContent;
  const done = async () => {await tick(); assert.deepEqual(errors, []); w.close();};
  return {w, $, calls, count, login, enter, ready, notice, done, navigations,
    popups: () => popups, recover: () => {failure = null;}};
}

(async () => {
  let passed = 0;
  {
    const pending = gate(), f = await fixture({meGate: pending});
    await f.login();
    await f.enter();
    assert.match(f.notice(), /正在读取/);
    await f.enter();
    assert.equal(f.count('/api/auth/me'), 1, 'loading clicks must not duplicate the request');
    assert.equal(f.count('/api/auth/saves?game=camping_plaza'), 0);
    assert.match(f.w.eval("selected = 'arcade'; arcadeBankHtml()"), /正在读取/);
    f.w.renderMine(); f.$('mineDrawer').classList.add('show');
    assert.doesNotMatch(f.$('mineContent').textContent, /暂无绑定/);
    f.w.eval("selected = 'camping_plaza'");
    pending.resolve(); await f.ready();
    assert.match(f.$('mineContent').textContent, /测试小机/);
    assert.equal(f.count('/api/announcements'), 2, 'one guest load and one login load');
    assert.equal(f.count('/api/announcements/read'), 1);
    assert.equal(f.popups(), 1);
    await f.enter();
    f.w.document.querySelector('[data-camping-slot="2"]').click();
    assert.deepEqual(f.navigations, [['101', 2]], 'bound account can enter its saved slot without refresh');
    assert.equal(f.calls.find(c => c.url === '/api/auth/me').init.headers.Authorization, 'Bearer fixture-token');
    await f.done(); passed++;
  }
  for (const failure of [500, 401, 'network', 'invalid', 'json']) {
    const f = await fixture({failure});
    await f.login();
    await until(() => f.w.eval('me?.bindings === null && !me.bindingsLoading'));
    await f.enter();
    assert.match(f.notice(), /读取失败.*重试/);
    assert.doesNotMatch(f.notice(), /没有绑定|未绑定/);
    assert.equal(f.count('/api/auth/saves?game=camping_plaza'), 0);
    assert.equal(f.w.token(), 'fixture-token', 'binding failure must not clear the successful login');
    f.recover(); await f.enter();
    assert.equal(f.w.document.querySelectorAll('[data-camping-slot]').length, 2);
    assert.equal(f.count('/api/auth/me'), 3, 'login read plus two explicit retries');
    assert.equal(f.count('/api/announcements'), 2);
    assert.equal(f.popups(), 1);
    await f.done(); passed++;
  }
  for (const game of ['soup', 'arcade']) {
    const pending = gate(), f = await fixture({meGate: pending});
    f.w.eval(`selected = '${game}'`);
    await f.login();
    const requestsBeforeSync = f.calls.length;
    pending.resolve(); await f.ready(); await tick();
    const added = f.calls.slice(requestsBeforeSync).map(c => c.url);
    assert.deepEqual(added, game === 'arcade' ? ['/api/arcade/chips?ai_user_id=101'] : [],
      'binding sync only refreshes relevant UI and must not reload global data');
    await f.done(); passed++;
  }
  for (const options of [{empty: true}, {register: true}]) {
    const f = await fixture(options);
    await f.login(options.register); await f.ready(); await f.enter();
    assert.match(f.notice(), /没有绑定小机/);
    assert.equal(f.count('/api/auth/me'), options.register ? 0 : 1);
    assert.equal(f.count('/api/auth/saves?game=camping_plaza'), 0);
    assert.equal(f.popups(), 1);
    await f.done(); passed++;
  }
  {
    const f = await fixture({authenticated: true});
    await f.ready(); await f.enter();
    assert.equal(f.w.document.querySelectorAll('[data-camping-slot]').length, 2);
    assert.equal(f.count('/api/auth/me'), 1);
    assert.equal(f.count('/api/announcements'), 1);
    assert.equal(f.count('/api/announcements/read'), 1);
    assert.equal(f.popups(), 1);
    await f.done(); passed++;
  }
  for (const options of [{pendingDeletion: true}, {user: {deletion: {scheduled_delete_at: '2099-01-01'}}}]) {
    const f = await fixture(options); await f.login();
    assert.equal(f.count('/api/auth/me'), 0);
    assert.equal(f.count('/api/announcements'), 1);
    assert.equal(f.popups(), 0);
    assert.equal(f.w.eval('me.pending_deletion'), true);
    assert(f.$('mineDrawer').classList.contains('show'));
    await f.done(); passed++;
  }
  {
    const f = await fixture({query: '?duel_invite=abcdef123456'}); await f.login();
    assert.equal(f.count('/api/auth/me'), 0, 'invite redirect skips binding refresh');
    assert.equal(f.count('/api/announcements'), 1);
    assert.equal(f.w.token(), 'fixture-token');
    assert.equal(f.w.eval('me'), null, 'redirect returns before local login rendering');
    await f.done(); passed++;
  }
  for (const replaceSession of [false, true]) {
    const pending = gate(), f = await fixture({meGate: pending}); await f.login();
    const oldSession = f.w.eval('me');
    f.w.logout();
    if (replaceSession) f.w.eval("localStorage.setItem(TOKEN_KEY, 'another-token'); me = {user: {id: 2}, bindings: []}");
    pending.resolve(); await until(() => !oldSession.bindingsLoading);
    assert.equal(f.w.eval('me?.user?.id || null'), replaceSession ? 2 : null);
    assert.equal(f.w.token(), replaceSession ? 'another-token' : '');
    await f.done(); passed++;
  }
  for (const invalidJson of [false, true]) {
    const f = await fixture({authenticated: true}), body = gate();
    const fetch = f.w.fetch;
    let reading = false;
    f.w.fetch = async (url, init) => {
      if (url !== '/api/auth/me') return fetch(url, init);
      f.w.fetch = fetch;
      return {ok: true, status: 200, json: async () => {
        reading = true;
        await body.promise;
        if (invalidJson) throw new Error('stale JSON failed');
        return {user: {id: 99}, bindings: []};
      }};
    };
    const oldLoad = f.w.loadMe();
    await until(() => reading);
    await f.login(); await f.ready();
    body.resolve(); await oldLoad;
    assert.equal(f.w.eval('me.user.id'), 1, 'aborted initial body must not replace the new login');
    assert.equal(f.w.aiBindings()[0].id, 101);
    assert.equal(f.w.token(), 'fixture-token', 'aborted JSON failure must not log out the new session');
    assert.equal(f.popups(), 2, 'initial login and new login each popup once; stale response never pops up');
    await f.done(); passed++;
  }
  console.log(`PASS: ${passed} login/bindings DOM scenarios (mocked APIs; no rendered-browser claim)`);
})().catch(error => {console.error(error); process.exit(1);});
