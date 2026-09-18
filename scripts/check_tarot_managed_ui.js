const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { TextDecoder, TextEncoder } = require('node:util');
const { JSDOM } = require('jsdom');
const csstree = require('css-tree');

const root = path.resolve(__dirname, '..');
const uiPath = path.join(root, 'assets/tarot/managed-ui.v3.js');
const corePath = path.join(root, 'assets/tarot/managed-core.v1.js');
const cssPath = path.join(root, 'assets/tarot/managed-ui.v3.css');
const companionPath = path.join(root, 'assets/tarot/managed-companion.v3.js');
const upstreamCompanionPath = path.join(
  root, 'vendor/tarot-ritual/public/js/companion-adapter.js',
);
const FLASH_MODEL = 'gemini-3.5-flash';
const PRO_MODEL = 'gemini-3.1-pro-preview';

function modelStatuses(flashStatus = 'available', flashSeconds = 0,
  proStatus = 'available', proSeconds = 0) {
  return {
    models: [
      { model: FLASH_MODEL, status: flashStatus, remaining_seconds: flashSeconds },
      { model: PRO_MODEL, status: proStatus, remaining_seconds: proSeconds },
    ],
  };
}

function settle(window) {
  return new Promise(resolve => window.setTimeout(resolve, 0));
}

async function checkUiBehavior() {
  const dom = new JSDOM(`<!doctype html><html><body>
    <button id="providerOrb"><span id="providerLabel"></span></button>
    <aside id="settingsPanel" class="open"><div id="providerList"></div></aside>
  </body></html>`, {
    runScripts: 'outside-only',
    url: 'https://toy.example/tarot/session/test/',
  });
  const { window } = dom;
  window.fetch = async url => {
    assert.equal(url, '/api/tarot/models/status');
    return { ok: true, json: async () => modelStatuses() };
  };
  window.eval(fs.readFileSync(uiPath, 'utf8'));

  const item = window.document.createElement('div');
  item.className = 'provider-item';
  item.innerHTML = `<div class="p-head"><span>legacy</span></div>
    <select>
      <option value="gemini-3.5-flash" selected>gemini-3.5-flash</option>
      <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
      <option value="arbitrary-model">arbitrary-model</option>
    </select>
    <input placeholder="legacy manual model">`;
  window.document.getElementById('providerList').appendChild(item);
  await settle(window);
  await settle(window);

  const choices = [...window.document.querySelectorAll('.managed-model-option')];
  assert.equal(choices.length, 2);
  assert.deepEqual(choices.map(button => button.dataset.model), [
    'gemini-3.5-flash',
    'gemini-3.1-pro-preview',
  ]);
  assert.equal(window.document.querySelector('#providerList input'), null);
  assert.equal(window.document.getElementById('providerLabel').textContent, '本站 Flash');
  assert.equal(choices[0].getAttribute('aria-checked'), 'true');

  const select = item.querySelector('select');
  let changedTo = null;
  let changeEvents = 0;
  select.addEventListener('change', () => {
    changedTo = select.value;
    changeEvents += 1;
  });
  choices[1].click();
  await settle(window);
  assert.equal(changedTo, 'gemini-3.1-pro-preview');
  assert.equal(changeEvents, 1);
  assert.equal(window.document.getElementById('providerLabel').textContent, '本站 Pro');
  assert.equal(choices[1].getAttribute('aria-checked'), 'true');
  window.dispatchEvent(new window.Event('pagehide'));
}

async function createCompanionHarness(session, {
  viewportWidth = 375,
  finishRace = false,
  finishBeforeStop = false,
} = {}) {
  const dom = new JSDOM(`<!doctype html><html><body>
    <button id="providerOrb"><span id="providerLabel"></span></button>
    <aside id="settingsPanel">
      <div class="settings-head"><h2>神谕议会</h2>
        <button id="settingsClose">闭 议</button>
      </div>
      <div id="providerList"></div>
    </aside>
    <main id="ui"></main>
    <div id="toasts"></div>
    <script type="application/json" id="companion-config">{"protocol":"cove-tarot-companion-v1","sessionId":"test_session","apiBase":"/companion/v1"}</script>
  </body></html>`, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'https://toy.example/tarot/session/test/',
  });
  const { window } = dom;
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: viewportWidth,
  });
  let current = JSON.parse(JSON.stringify(session));
  let fetchCalls = 0;
  let statusCalls = 0;
  window.fetch = async (url, options) => {
    if (url === '/api/tarot/models/status') {
      statusCalls += 1;
      assert.equal(options.credentials, 'same-origin');
      assert.equal(options.cache, 'no-store');
      return { ok: true, json: async () => modelStatuses() };
    }
    fetchCalls += 1;
    assert.equal(url, '/companion/v1/sessions/test_session');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    return {
      ok: true,
      json: async () => ({ session: JSON.parse(JSON.stringify(current)) }),
    };
  };
  window.eval(fs.readFileSync(uiPath, 'utf8'));

  const settings = window.document.getElementById('settingsPanel');
  window.document.getElementById('providerOrb').addEventListener('click', () => {
    settings.classList.toggle('open');
  });
  window.document.getElementById('settingsClose').addEventListener('click', () => {
    settings.classList.remove('open');
  });

  const box = window.document.createElement('div');
  box.className = 'panel';
  box.style.cssText = 'position:fixed;top:88px;left:16px;z-index:60';
  const status = window.document.createElement('p');
  status.id = 'companionStatus';
  const finish = window.document.createElement('button');
  finish.textContent = '返回聊天';
  const stop = window.document.createElement('button');
  stop.textContent = '停止本次';
  let finishCalls = 0;
  let stopCalls = 0;
  finish.addEventListener('click', async () => {
    finishCalls += 1;
    finish.disabled = true;
    try {
      await Promise.resolve();
      if (finishRace && finishCalls === 1) {
        current.reading = { state: 'running' };
        status.textContent = '暂未交回：解读仍在进行。解读进行中可等待或停止。';
        return;
      }
      if (current.reading?.state === 'running') {
        status.textContent = '暂未交回：解读仍在进行。解读进行中可等待或停止。';
        return;
      }
      current.phase = 'returned';
      status.textContent = '结果已交回，等待聊天端确认；可返回聊天查看。';
    } finally {
      finish.disabled = false;
    }
  });
  stop.addEventListener('click', async () => {
    stopCalls += 1;
    stop.disabled = true;
    try {
      await Promise.resolve();
      if (finishBeforeStop && current.reading?.state === 'running') {
        current.reading = { state: 'succeeded', text: '已保存解读' };
      } else if (current.reading?.state === 'running') {
        current.reading = { state: 'cancelled', text: '' };
      }
      current.phase = 'stopped';
      status.textContent = '本次已停止。已有记录保留；不会自动重试解读。';
    } finally {
      stop.disabled = false;
    }
  });
  box.append(status, finish, stop);
  window.document.getElementById('ui').appendChild(box);
  await settle(window);
  await settle(window);

  return {
    window,
    settings,
    box,
    status,
    finish,
    stop,
    end: box.querySelector('[data-managed-end="1"]'),
    calls: () => ({
      finish: finishCalls, stop: stopCalls, fetch: fetchCalls, status: statusCalls,
    }),
    session: () => JSON.parse(JSON.stringify(current)),
  };
}

function assertSingleManagedAction(harness) {
  const { box, finish, stop, end } = harness;
  assert.equal(finish.hidden, true);
  assert.equal(finish.tabIndex, -1);
  assert.equal(finish.getAttribute('aria-hidden'), 'true');
  assert.equal(stop.hidden, true);
  assert.equal(stop.tabIndex, -1);
  assert.equal(stop.getAttribute('aria-hidden'), 'true');
  assert.deepEqual(
    [...box.querySelectorAll('button:not([hidden])')],
    [end],
  );
  assert.equal(end.title, '结束本次操作，已有记录保留');
  assert.equal(end.getAttribute('aria-label'), '结束本次操作，已有记录保留');
}

async function clickEndAndSettle(harness, { rapid = false, waitMs = 30 } = {}) {
  harness.end.click();
  if (rapid) {
    harness.end.click();
    harness.end.click();
  }
  await new Promise(resolve => harness.window.setTimeout(resolve, waitMs));
  await settle(harness.window);
}

async function assertEnded(harness, expectedCalls) {
  assert.equal(harness.status.textContent, '本次已结束，记录已保留。');
  assert.equal(harness.end.textContent, '已结束');
  assert.equal(harness.end.disabled, true);
  assert.equal(harness.end.getAttribute('aria-disabled'), 'true');
  const calls = harness.calls();
  assert.equal(calls.finish, expectedCalls.finish);
  assert.equal(calls.stop, expectedCalls.stop);
  assert.ok(calls.fetch >= 1);
  harness.status.textContent = '本次会话已结束，已有结果仅供查看。';
  await settle(harness.window);
  assert.equal(harness.status.textContent, '本次已结束，记录已保留。');
}

async function checkEndScenarios() {
  const unstarted = await createCompanionHarness({
    phase: 'accepted', draws: [], reading: null,
  });
  assertSingleManagedAction(unstarted);
  await clickEndAndSettle(unstarted);
  await assertEnded(unstarted, { finish: 0, stop: 1 });

  const incomplete = await createCompanionHarness({
    phase: 'drawn', draws: [{ revealed: false }], reading: null,
  });
  assertSingleManagedAction(incomplete);
  await clickEndAndSettle(incomplete);
  await assertEnded(incomplete, { finish: 0, stop: 1 });

  const running = await createCompanionHarness({
    phase: 'revealed', draws: [{ revealed: true }], reading: { state: 'running' },
  }, { finishBeforeStop: true });
  assertSingleManagedAction(running);
  await clickEndAndSettle(running);
  await assertEnded(running, { finish: 0, stop: 1 });
  assert.equal(running.session().reading.state, 'succeeded');

  const completed = await createCompanionHarness({
    phase: 'revealed', draws: [{ revealed: true }], reading: { state: 'succeeded' },
  });
  assertSingleManagedAction(completed);
  await clickEndAndSettle(completed, { rapid: true });
  await assertEnded(completed, { finish: 1, stop: 0 });

  const race = await createCompanionHarness({
    phase: 'revealed', draws: [{ revealed: true }], reading: { state: 'succeeded' },
  }, { finishRace: true });
  assertSingleManagedAction(race);
  await clickEndAndSettle(race);
  await assertEnded(race, { finish: 1, stop: 1 });

  const restored = await createCompanionHarness({
    phase: 'returned', draws: [{ revealed: true }], reading: { state: 'succeeded' },
  });
  assertSingleManagedAction(restored);
  await assertEnded(restored, { finish: 0, stop: 0 });
}

async function checkSettingsVisibility(viewportWidth) {
  const harness = await createCompanionHarness({
    phase: 'accepted', draws: [], reading: null,
  }, { viewportWidth });
  const { window, settings, box } = harness;
  assertSingleManagedAction(harness);

  window.document.getElementById('providerOrb').click();
  await settle(window);
  assert.equal(settings.classList.contains('open'), true);
  assert.equal(box.classList.contains('managed-companion-settings-hidden'), true);
  assert.equal(box.getAttribute('aria-hidden'), 'true');
  window.document.getElementById('settingsClose').click();
  await settle(window);
  assert.equal(settings.classList.contains('open'), false);
  assert.equal(box.classList.contains('managed-companion-settings-hidden'), false);
  assert.equal(box.hasAttribute('aria-hidden'), false);

  const toast = window.document.createElement('div');
  toast.className = 'toast';
  toast.textContent = '本次记录已锁定；请返回聊天开始新的占问。';
  window.document.getElementById('toasts').appendChild(toast);
  await settle(window);
  assert.equal(
    toast.textContent,
    '本次记录已锁定；请先结束本次，再开始新的占问。',
  );
  window.dispatchEvent(new window.Event('pagehide'));
}

async function checkSecureUuidAndAckReplay() {
  const dom = new JSDOM('<!doctype html><html><body><div id="toasts"></div></body></html>', {
    runScripts: 'outside-only',
    url: 'https://toy.example/tarot/session/test/',
  });
  const { window } = dom;
  let byte = 0;
  window.Math.random = () => { throw new Error('Math.random must not be used'); };
  Object.defineProperty(window.crypto, 'randomUUID', {
    configurable: true, value: undefined,
  });
  Object.defineProperty(window.crypto, 'getRandomValues', {
    configurable: true,
    value: array => {
      for (let index = 0; index < array.length; index += 1) {
        array[index] = byte % 256;
        byte += 1;
      }
      return array;
    },
  });
  window.eval(fs.readFileSync(uiPath, 'utf8'));
  const generated = window.crypto.randomUUID();
  assert.match(
    generated,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  assert.doesNotMatch(fs.readFileSync(uiPath, 'utf8'), /Math\.random\s*\(/);

  const values = new Map();
  const localStorage = {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  };
  const context = vm.createContext({
    console,
    crypto: window.crypto,
    localStorage,
  });
  const module = new vm.SourceTextModule(
    fs.readFileSync(upstreamCompanionPath, 'utf8'),
    { context, identifier: upstreamCompanionPath },
  );
  await module.link(async specifier => {
    throw new Error(`unexpected import: ${specifier}`);
  });
  await module.evaluate();

  const sessionId = 'uuid_ack_session';
  const endpoint = `/companion/v1/sessions/${sessionId}`;
  const events = new Map();
  const posted = [];
  let loseFirstDrawAck = true;
  const session = {
    id: sessionId,
    revision: 0,
    phase: 'accepted',
    draws: [],
    reading: null,
  };
  const response = payload => ({ ok: true, json: async () => payload });
  const fetchImpl = async (url, options = {}) => {
    if (url === endpoint && (!options.method || options.method === 'GET')) {
      return response({ session: JSON.parse(JSON.stringify(session)), csrf_token: 'csrf' });
    }
    if (url === `${endpoint}/draw`) {
      const body = JSON.parse(options.body);
      posted.push({ route: 'draw', event_id: body.event_id });
      if (!events.has(body.event_id)) {
        events.set(body.event_id, 'draw');
        session.draws = body.draws.map(draw => ({ ...draw, revealed: false }));
        session.phase = 'drawn';
        session.revision += 1;
      }
      if (loseFirstDrawAck) {
        loseFirstDrawAck = false;
        throw new Error('simulated ACK loss');
      }
      return response({
        session_id: sessionId,
        event_id: body.event_id,
        revision: session.revision,
      });
    }
    if (url === `${endpoint}/reveal`) {
      const body = JSON.parse(options.body);
      posted.push({ route: 'reveal', event_id: body.event_id });
      if (!events.has(body.event_id)) {
        events.set(body.event_id, 'reveal');
        session.draws.forEach(draw => { draw.revealed = true; });
        session.phase = 'revealed';
        session.revision += 1;
      }
      return response({
        session_id: sessionId,
        event_id: body.event_id,
        revision: session.revision,
      });
    }
    if (url === `${endpoint}/reading`) {
      const body = JSON.parse(options.body);
      posted.push({ route: 'reading', action_id: body.action_id });
      return { ok: true, body: {} };
    }
    throw new Error(`unexpected request: ${url}`);
  };
  const config = {
    protocol: 'cove-tarot-companion-v1', apiBase: '/companion/v1', sessionId,
  };
  const first = module.namespace.createCompanionAdapter(config, { fetchImpl });
  await assert.rejects(
    first.commitDraw({
      question: '问题', spread_id: 'single',
      draws: [{ position: 0, card_id: 'M00', reversed: false }],
    }),
    /ACK loss/,
  );
  const pending = JSON.parse(values.get(`cove-tarot-companion-v1.outbox.${sessionId}`));
  assert.equal(pending.length, 1);
  const drawEventId = JSON.parse(pending[0].body).event_id;
  assert.match(drawEventId, /^[0-9a-f-]{36}$/);

  const restored = module.namespace.createCompanionAdapter(config, { fetchImpl });
  await restored.restore();
  assert.equal(values.has(`cove-tarot-companion-v1.outbox.${sessionId}`), false);
  assert.deepEqual(
    posted.filter(item => item.route === 'draw').map(item => item.event_id),
    [drawEventId, drawEventId],
  );
  assert.equal([...events.values()].filter(kind => kind === 'draw').length, 1);

  await restored.reveal({ positions: [0] });
  const revealId = posted.find(item => item.route === 'reveal').event_id;
  assert.notEqual(revealId, drawEventId);
  const actionId = window.crypto.randomUUID();
  await restored.read({
    action_id: actionId,
    model: FLASH_MODEL,
  });
  assert.equal(posted.find(item => item.route === 'reading').action_id, actionId);
  window.dispatchEvent(new window.Event('pagehide'));

  const unavailable = new JSDOM('<!doctype html><html><body></body></html>', {
    runScripts: 'outside-only',
    url: 'https://toy.example/tarot/session/test/',
  });
  Object.defineProperty(unavailable.window.crypto, 'randomUUID', {
    configurable: true, value: undefined,
  });
  Object.defineProperty(unavailable.window.crypto, 'getRandomValues', {
    configurable: true, value: undefined,
  });
  unavailable.window.eval(fs.readFileSync(uiPath, 'utf8'));
  assert.throws(
    () => unavailable.window.crypto.randomUUID(),
    /缺少安全随机源.*尚未保存/,
  );
  unavailable.window.dispatchEvent(new unavailable.window.Event('pagehide'));

  const broken = new JSDOM('<!doctype html><html><body></body></html>', {
    runScripts: 'outside-only',
    url: 'https://toy.example/tarot/session/test/',
  });
  Object.defineProperty(broken.window.crypto, 'randomUUID', {
    configurable: true, value: undefined,
  });
  Object.defineProperty(broken.window.crypto, 'getRandomValues', {
    configurable: true,
    value: () => { throw new Error('native random failure'); },
  });
  broken.window.eval(fs.readFileSync(uiPath, 'utf8'));
  assert.throws(
    () => broken.window.crypto.randomUUID(),
    /安全随机源不可用.*尚未保存/,
  );
  broken.window.dispatchEvent(new broken.window.Event('pagehide'));
}

async function checkSaveFailureMessages() {
  const pending = await createCompanionHarness({
    phase: 'accepted', draws: [], reading: null,
  });
  pending.window.localStorage.setItem(
    'cove-tarot-companion-v1.outbox.test_session',
    JSON.stringify([{ route: 'draw', body: '{}' }]),
  );
  pending.status.textContent = '同步尚未确认；已保留本次记录，请刷新重试。network';
  await settle(pending.window);
  assert.match(pending.status.textContent, /本机确有待同步记录/);
  assert.doesNotMatch(pending.status.textContent, /已保留本次记录|刷新重试/);
  pending.window.dispatchEvent(new pending.window.Event('pagehide'));

  const unsaved = await createCompanionHarness({
    phase: 'accepted', draws: [], reading: null,
  });
  unsaved.status.textContent = '同步尚未确认；已保留本次记录，请刷新重试。当前浏览器缺少安全随机源，本次记录尚未保存。';
  await settle(unsaved.window);
  await settle(unsaved.window);
  assert.match(unsaved.status.textContent, /缺少安全随机源/);
  assert.match(unsaved.status.textContent, /尚未保存/);
  assert.doesNotMatch(unsaved.status.textContent, /已保留本次记录|刷新重试/);
  unsaved.window.dispatchEvent(new unsaved.window.Event('pagehide'));

  const saved = await createCompanionHarness({
    phase: 'drawn',
    draws: [{ position: 0, card_id: 'M00', reversed: false, revealed: false }],
    reading: null,
  });
  saved.status.textContent = '同步尚未确认；已保留本次记录，请刷新重试。network';
  await settle(saved.window);
  await settle(saved.window);
  assert.match(saved.status.textContent, /服务器已确认保存本次牌面/);
  assert.doesNotMatch(saved.status.textContent, /已保留本次记录|刷新重试/);
  saved.window.dispatchEvent(new saved.window.Event('pagehide'));
}

async function checkHistoryUi() {
  const currentSessionId = 'current_history_session';
  const oldSessionId = 'saved_history_session';
  const dom = new JSDOM(`<!doctype html><html><body>
    <header id="topbar"><div class="brand"></div><div class="top-right">
      <button id="providerOrb"><span id="providerLabel"></span></button>
    </div></header>
    <aside id="settingsPanel"><button id="settingsClose">关闭</button><div id="providerList"></div></aside>
    <main id="ui"><div id="toasts"></div>
      <div class="panel" id="companionBox"><p id="companionStatus"></p>
        <button>返回聊天</button><button>停止本次</button>
      </div>
    </main>
    <script type="application/json" id="companion-config">{"protocol":"cove-tarot-companion-v1","sessionId":"${currentSessionId}","apiBase":"/companion/v1"}</script>
  </body></html>`, {
    runScripts: 'outside-only', pretendToBeVisual: true,
    url: 'https://toy.example/tarot/session/current_history_session/',
  });
  const { window } = dom;
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 });
  let deleted = false;
  const calls = [];
  window.confirm = message => {
    assert.match(message, /永久删除/);
    return true;
  };
  window.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    if (url === `/companion/v1/sessions/${currentSessionId}`) {
      return {
        ok: true,
        json: async () => ({
          csrf_token: 'history-csrf',
          session: { id: currentSessionId, phase: 'accepted', draws: [], reading: null },
        }),
      };
    }
    if (url.startsWith('/api/tarot/history?')) {
      return {
        ok: true,
        json: async () => ({
          items: deleted ? [] : [{
            session_id: oldSessionId,
            updated_at: 1800000000,
            question_summary: '<img src=x onerror=alert(1)> 私密问题',
            spread_name: '每日一牌',
          }],
          next_offset: null,
        }),
      };
    }
    if (url === `/api/tarot/history/${oldSessionId}`) {
      return {
        ok: true,
        json: async () => ({
          session_id: oldSessionId,
          updated_at: 1800000000,
          question: '<script>危险问题</script>',
          spread: { zh: '每日一牌' },
          cards: [{ card_id: 'M00', zh: '愚者', slot: '神谕', reversed: false }],
          reading: { state: 'succeeded', text: '<b>只显示文本</b>' },
        }),
      };
    }
    if (url === `/api/tarot/history/${oldSessionId}/delete`) {
      assert.equal(options.method, 'POST');
      assert.equal(options.headers['X-Companion-CSRF'], 'history-csrf');
      assert.deepEqual(JSON.parse(options.body), {
        confirm: true, csrf_session_id: currentSessionId,
      });
      deleted = true;
      return {
        ok: true,
        json: async () => ({ deleted: true, session_id: oldSessionId }),
      };
    }
    throw new Error(`unexpected request: ${url}`);
  };
  window.eval(fs.readFileSync(uiPath, 'utf8'));
  await settle(window);
  await settle(window);
  const trigger = window.document.getElementById('managedHistoryTrigger');
  assert.ok(trigger);
  trigger.click();
  await settle(window);
  await settle(window);
  const panel = window.document.getElementById('managedHistoryPanel');
  assert.equal(panel.classList.contains('open'), true);
  assert.equal(panel.getAttribute('aria-hidden'), 'false');
  assert.equal(window.document.getElementById('companionBox').getAttribute('aria-hidden'), 'true');
  assert.equal(panel.querySelectorAll('img, script').length, 0);
  assert.match(panel.textContent, /<img src=x onerror=alert\(1\)>/);

  panel.querySelector('.managed-history-view').click();
  await settle(window);
  await settle(window);
  assert.equal(panel.querySelectorAll('b, script').length, 0);
  assert.match(panel.textContent, /<script>危险问题<\/script>/);
  assert.match(panel.textContent, /<b>只显示文本<\/b>/);
  panel.querySelector('.managed-history-detail .managed-history-delete').click();
  await settle(window);
  await settle(window);
  await settle(window);
  assert.equal(deleted, true);
  assert.match(panel.textContent, /暂无已保存牌面的记录/);

  panel.classList.add('open');
  window.document.getElementById('settingsPanel').classList.add('open');
  await settle(window);
  assert.equal(panel.classList.contains('open'), false);
  assert.equal(window.document.getElementById('companionBox').getAttribute('aria-hidden'), 'true');
  window.document.getElementById('settingsPanel').classList.remove('open');
  await settle(window);
  assert.equal(window.document.getElementById('companionBox').hasAttribute('aria-hidden'), false);
  assert.ok(calls.some(call => call.url === `/api/tarot/history/${oldSessionId}/delete`));
  window.dispatchEvent(new window.Event('pagehide'));
}

async function createModelStatusHarness(responses, { selected = FLASH_MODEL } = {}) {
  const dom = new JSDOM(`<!doctype html><html><body>
    <button id="providerOrb"><span id="providerLabel"></span></button>
    <aside id="settingsPanel" class="open">
      <button id="settingsClose">关闭</button><div id="providerList"></div>
    </aside>
    <div id="toasts"></div>
  </body></html>`, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'https://toy.example/tarot/session/test/',
  });
  const { window } = dom;
  let calls = 0;
  window.fetch = async (url, options) => {
    assert.equal(url, '/api/tarot/models/status');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    const response = responses[Math.min(calls, responses.length - 1)];
    calls += 1;
    if (response instanceof Error) throw response;
    return {
      ok: response.ok !== false,
      json: async () => response.payload,
    };
  };
  window.eval(fs.readFileSync(uiPath, 'utf8'));
  const item = window.document.createElement('div');
  item.className = 'provider-item';
  item.innerHTML = `<div class="p-head"></div><select>
    <option value="${FLASH_MODEL}"${selected === FLASH_MODEL ? ' selected' : ''}>Flash</option>
    <option value="${PRO_MODEL}"${selected === PRO_MODEL ? ' selected' : ''}>Pro</option>
  </select>`;
  window.document.getElementById('providerList').appendChild(item);
  await settle(window);
  await settle(window);
  return {
    window,
    item,
    calls: () => calls,
    choices: () => [...window.document.querySelectorAll('.managed-model-option')],
    summary: () => window.document.getElementById('managedModelSummary').textContent,
    refresh: () => window.document.querySelector('.managed-model-refresh'),
  };
}

async function checkModelRuntimeStates() {
  const countdown = await createModelStatusHarness([
    { payload: modelStatuses('cooling', 1, 'available', 0) },
    { payload: modelStatuses('retry_ready', 0, 'available', 0) },
  ]);
  let [flash, pro] = countdown.choices();
  assert.equal(flash.disabled, true);
  assert.equal(flash.dataset.runtimeStatus, 'cooling');
  assert.equal(flash.querySelector('.managed-model-status').textContent, '暂不可用');
  assert.doesNotMatch(flash.textContent, /\d+:\d+|倒计时|剩余/);
  assert.equal(pro.disabled, false);
  assert.match(countdown.summary(), /当前选择暂不可用/);
  assert.equal(countdown.calls(), 1);
  await new Promise(resolve => countdown.window.setTimeout(resolve, 1150));
  await settle(countdown.window);
  [flash, pro] = countdown.choices();
  assert.equal(countdown.calls(), 2);
  assert.equal(flash.disabled, false);
  assert.equal(flash.dataset.runtimeStatus, 'retry_ready');
  assert.match(flash.querySelector('.managed-model-status').textContent, /可重试/);
  assert.match(flash.querySelector('.managed-model-status').textContent, /尚未确认恢复/);
  assert.doesNotMatch(flash.textContent, /额度已恢复/);
  const beforeManual = countdown.calls();
  countdown.refresh().click();
  await settle(countdown.window);
  await settle(countdown.window);
  assert.equal(countdown.calls(), beforeManual + 1);
  await new Promise(resolve => countdown.window.setTimeout(resolve, 30));
  assert.equal(countdown.calls(), beforeManual + 1);
  countdown.window.dispatchEvent(new countdown.window.Event('pagehide'));

  const failed = await createModelStatusHarness([
    { ok: false, payload: { error: 'temporary' } },
  ]);
  assert.equal(failed.choices().every(button => button.disabled), true);
  assert.match(failed.summary(), /暂时无法确认模型状态/);
  failed.window.dispatchEvent(new failed.window.Event('pagehide'));

  const selectedPro = await createModelStatusHarness([
    { payload: modelStatuses('available', 0, 'cooling', 120) },
  ], { selected: PRO_MODEL });
  const [availableFlash, coolingPro] = selectedPro.choices();
  assert.equal(availableFlash.disabled, false);
  assert.equal(coolingPro.disabled, true);
  assert.equal(selectedPro.item.querySelector('select').value, PRO_MODEL);
  assert.equal(
    selectedPro.window.document.getElementById('providerLabel').textContent,
    '本站 Pro',
  );
  assert.match(selectedPro.summary(), /当前选择暂不可用/);
  selectedPro.window.dispatchEvent(new selectedPro.window.Event('pagehide'));

  const unavailable = await createModelStatusHarness([
    { payload: modelStatuses('probing', 0, 'unconfigured', 0) },
  ]);
  const [probingFlash, unconfiguredPro] = unavailable.choices();
  assert.equal(probingFlash.disabled, true);
  assert.match(probingFlash.textContent, /探测中/);
  assert.equal(unconfiguredPro.disabled, true);
  assert.match(unconfiguredPro.textContent, /未配置/);
  unavailable.window.dispatchEvent(new unavailable.window.Event('pagehide'));

  const visible = await createModelStatusHarness([
    { payload: modelStatuses() },
  ]);
  const initialCalls = visible.calls();
  Object.defineProperty(visible.window.document, 'hidden', {
    configurable: true, value: true,
  });
  visible.window.document.dispatchEvent(new visible.window.Event('visibilitychange'));
  await settle(visible.window);
  assert.equal(visible.calls(), initialCalls);
  Object.defineProperty(visible.window.document, 'hidden', {
    configurable: true, value: false,
  });
  visible.window.document.dispatchEvent(new visible.window.Event('visibilitychange'));
  await settle(visible.window);
  await settle(visible.window);
  assert.equal(visible.calls(), initialCalls + 1);
  visible.window.dispatchEvent(new visible.window.Event('pagehide'));
}

async function checkManagedCompanionBoundary() {
  const context = vm.createContext({ console });
  let upstreamReads = 0;
  const upstreamFactory = () => ({
    read: async body => {
      upstreamReads += 1;
      return { ok: true, body };
    },
    restore: async () => ({}),
  });
  const module = new vm.SourceTextModule(fs.readFileSync(companionPath, 'utf8'), {
    context,
    identifier: companionPath,
  });
  await module.link(async specifier => {
    assert.equal(
      specifier,
      '/tarot/static/platform/upstream-companion-adapter.v1.js',
    );
    return new vm.SyntheticModule(['createCompanionAdapter'], function init() {
      this.setExport('createCompanionAdapter', upstreamFactory);
    }, { context, identifier: specifier });
  });
  await module.evaluate();

  let statusCalls = 0;
  let current = modelStatuses('cooling', 120, 'available', 0);
  let statusFailure = false;
  const fetchImpl = async url => {
    statusCalls += 1;
    assert.equal(url, '/api/tarot/models/status');
    if (statusFailure) throw new Error('offline');
    return { ok: true, json: async () => current };
  };
  const adapter = module.namespace.createCompanionAdapter({}, { fetchImpl });
  await assert.rejects(
    adapter.read({ model: FLASH_MODEL, action_id: 'new_reading' }),
    /暂不可用/,
  );
  assert.equal(upstreamReads, 0);

  current = modelStatuses('retry_ready', 0, 'available', 0);
  await adapter.read({ model: FLASH_MODEL, action_id: 'manual_retry' });
  assert.equal(upstreamReads, 1);

  statusFailure = true;
  await assert.rejects(
    adapter.read({ model: PRO_MODEL, action_id: 'status_failed' }),
    /暂时无法确认模型状态/,
  );
  assert.equal(upstreamReads, 1);
  const beforeRestore = statusCalls;
  await adapter.read({ attempt_id: 'existing_attempt' });
  assert.equal(statusCalls, beforeRestore);
  assert.equal(upstreamReads, 2);
}

async function checkManagedCoreBoundary() {
  const values = new Map();
  const localStorage = {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  };
  const context = vm.createContext({
    localStorage,
    TextDecoder,
    console,
  });
  const module = new vm.SourceTextModule(fs.readFileSync(corePath, 'utf8'), {
    context,
    identifier: corePath,
  });
  await module.link(async specifier => new vm.SyntheticModule([], () => {}, {
    context,
    identifier: specifier,
  }));
  await module.evaluate();
  const api = module.namespace;
  api.initProviderState();
  assert.equal(api.currentModel('managed:cedartoy-tarot'), 'gemini-3.5-flash');
  api.setModel('managed:cedartoy-tarot', 'gemini-3.1-pro-preview');
  assert.equal(api.currentModel('managed:cedartoy-tarot'), 'gemini-3.1-pro-preview');
  assert.throws(
    () => api.setModel('managed:cedartoy-tarot', 'arbitrary-model'),
    /Flash 或 Pro/,
  );

  const encoder = new TextEncoder();
  const chunks = [encoder.encode(
    'data: {"t":"delta","v":"mock reading"}\n\n' +
    'data: {"t":"done"}\n\n',
  )];
  let captured;
  let delta = '';
  let done = null;
  await api.chat({
    model: 'gemini-3.1-pro-preview',
    providerId: 'untrusted-provider',
    provider: { apiKey: 'browser-secret', baseURL: 'https://untrusted.test' },
    messages: [{ role: 'user', content: 'untrusted prompt' }],
    transport: async body => {
      captured = body;
      return {
        ok: true,
        body: {
          getReader: () => ({
            read: async () => chunks.length
              ? { done: false, value: chunks.shift() }
              : { done: true },
            cancel: async () => {},
            releaseLock: () => {},
          }),
        },
      };
    },
    onDelta: value => { delta += value; },
    onDone: value => { done = value; },
  });
  assert.deepEqual({ ...captured }, { model: 'gemini-3.1-pro-preview' });
  assert.equal(delta, 'mock reading');
  assert.equal(done, true);
}

function checkMobileRules() {
  const ast = csstree.parse(fs.readFileSync(cssPath, 'utf8'));
  let mobile = false;
  let fullWidthPanel = false;
  let fullWidthHistory = false;
  let companionHidden = false;
  csstree.walk(ast, node => {
    if (node.type === 'Atrule' && node.name === 'media') {
      const query = csstree.generate(node.prelude);
      if (query.includes('max-width:600px')) {
        mobile = true;
        csstree.walk(node.block, child => {
          if (child.type !== 'Rule') return;
          const selector = csstree.generate(child.prelude);
          const block = csstree.generate(child.block);
          if (selector === '#settingsPanel' && block.includes('width:100vw')) {
            fullWidthPanel = true;
          }
          if (selector === '#managedHistoryPanel' && block.includes('width:100vw')) {
            fullWidthHistory = true;
          }
        });
      }
    }
    if (node.type === 'Rule') {
      const selector = csstree.generate(node.prelude);
      const block = csstree.generate(node.block);
      if (
        selector === '.managed-companion-settings-hidden'
        && block.includes('display:none!important')
      ) {
        companionHidden = true;
      }
    }
  });
  assert.equal(mobile, true);
  assert.equal(fullWidthPanel, true);
  assert.equal(fullWidthHistory, true);
  assert.equal(companionHidden, true);
}

Promise.resolve()
  .then(checkUiBehavior)
  .then(checkEndScenarios)
  .then(() => checkSettingsVisibility(360))
  .then(() => checkSettingsVisibility(375))
  .then(() => checkSettingsVisibility(390))
  .then(checkSecureUuidAndAckReplay)
  .then(checkSaveFailureMessages)
  .then(checkHistoryUi)
  .then(checkModelRuntimeStates)
  .then(checkManagedCompanionBoundary)
  .then(checkManagedCoreBoundary)
  .then(checkMobileRules)
  .then(() => console.log('tarot managed UI behavior (jsdom 360/375/390): ok'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
