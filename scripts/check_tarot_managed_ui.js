const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { TextDecoder, TextEncoder } = require('node:util');
const { JSDOM } = require('jsdom');
const csstree = require('css-tree');

const root = path.resolve(__dirname, '..');
const uiPath = path.join(root, 'assets/tarot/managed-ui.v1.js');
const corePath = path.join(root, 'assets/tarot/managed-core.v1.js');
const cssPath = path.join(root, 'assets/tarot/managed-ui.v1.css');

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
    }
  });
  assert.equal(mobile, true);
  assert.equal(fullWidthPanel, true);
}

Promise.resolve()
  .then(checkUiBehavior)
  .then(checkManagedCoreBoundary)
  .then(checkMobileRules)
  .then(() => console.log('tarot managed UI behavior: ok'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
