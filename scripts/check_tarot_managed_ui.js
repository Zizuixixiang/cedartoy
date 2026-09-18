const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { TextDecoder, TextEncoder } = require('node:util');
const { JSDOM } = require('jsdom');
const csstree = require('css-tree');

const root = path.resolve(__dirname, '..');
const uiPath = path.join(root, 'assets/tarot/managed-ui.v2.js');
const corePath = path.join(root, 'assets/tarot/managed-core.v1.js');
const cssPath = path.join(root, 'assets/tarot/managed-ui.v2.css');

function settle(window) {
  return new Promise(resolve => window.setTimeout(resolve, 0));
}

async function checkUiBehavior() {
  const dom = new JSDOM(`<!doctype html><html><body>
    <button id="providerOrb"><span id="providerLabel"></span></button>
    <div id="providerList"></div>
  </body></html>`, {
    runScripts: 'outside-only',
    url: 'https://toy.example/tarot/session/test/',
  });
  const { window } = dom;
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
  select.addEventListener('change', () => { changedTo = select.value; });
  choices[1].click();
  await settle(window);
  assert.equal(changedTo, 'gemini-3.1-pro-preview');
  assert.equal(window.document.getElementById('providerLabel').textContent, '本站 Pro');
  assert.equal(choices[1].getAttribute('aria-checked'), 'true');
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
  window.fetch = async (url, options) => {
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
    calls: () => ({ finish: finishCalls, stop: stopCalls, fetch: fetchCalls }),
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
  let companionHidden = false;
  csstree.walk(ast, node => {
    if (node.type === 'Atrule' && node.name === 'media') {
      const query = csstree.generate(node.prelude);
      if (query.includes('max-width:600px')) mobile = true;
    }
    if (node.type === 'Rule') {
      const selector = csstree.generate(node.prelude);
      const block = csstree.generate(node.block);
      if (selector === '#settingsPanel' && block.includes('width:100vw')) {
        fullWidthPanel = true;
      }
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
  assert.equal(companionHidden, true);
}

Promise.resolve()
  .then(checkUiBehavior)
  .then(checkEndScenarios)
  .then(() => checkSettingsVisibility(360))
  .then(() => checkSettingsVisibility(375))
  .then(() => checkSettingsVisibility(390))
  .then(checkManagedCoreBoundary)
  .then(checkMobileRules)
  .then(() => console.log('tarot managed UI behavior (jsdom 360/375/390): ok'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
