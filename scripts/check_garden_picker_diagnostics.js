// No network, real tokens or saves. This checks diagnostics and DOM behavior,
// not rendered appearance (the diagnostic change has no visual surface).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM, VirtualConsole} = require('jsdom');

const source = fs.readFileSync('index.html', 'utf8');
for (const [, code] of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new vm.Script(code);

(async () => {
  let mode = 'success';
  const logs = [], requests = [], errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e.message));
  vc.on('info', (tag, body) => { if (tag === 'garden_picker') logs.push(JSON.parse(body)); });
  const dom = new JSDOM(source, {
    url: 'http://isolated.test/', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.localStorage.setItem('cedartoy_token', 'private-test-token');
      w.matchMedia = () => ({matches: false, addListener() {}, addEventListener() {}});
      w.fetch = async (address, options) => {
        const url = new URL(address, w.location.href);
        if (url.pathname !== '/api/garden-cat/gardens') return {
          ok: true, status: 200, json: async () => url.pathname === '/api/auth/me'
            ? {user: {id: 1, username: 'private-human'}, bindings: []} : {},
        };
        requests.push(options);
        if (mode === 'fetch_pending') return new Promise(() => {});
        if (mode === 'reject') throw new Error('private-exception');
        if (mode === 'unauthorized') return {ok: false, status: 401};
        if (mode === 'server_error') return {ok: false, status: 500};
        return {ok: true, status: 200, json: async () => {
          if (mode === 'body_pending') return new Promise(() => {});
          if (mode === 'bad_json') throw new SyntaxError('private-exception');
          return {gardens: mode === 'empty' ? [] : [{ai_user_id: 2,
            machine_name: 'private-machine-name', slot: 1, garden_name: 'private-garden-name',
            money: 12, encyclopedia_count: 0, has_cat: false}]};
        }};
      };
    },
  });
  try {
    await new Promise(resolve => setTimeout(resolve, 40));
    const w = dom.window;
    const ids = new Set();
    const cases = {
      success: 'rendered', empty: 'empty_rendered', unauthorized: 'error_rendered',
      server_error: 'error_rendered', reject: 'error_rendered', bad_json: 'error_rendered',
      fetch_pending: 'request_start', body_pending: 'response_headers',
    };
    for (const [name, lastEvent] of Object.entries(cases)) {
      mode = name; logs.length = 0; requests.length = 0;
      const settled = await Promise.race([
        w.openGardenCatPicker().then(() => true),
        new Promise(resolve => setTimeout(() => resolve(false), 30)),
      ]);
      assert.equal(requests.length, 1);
      const options = requests[0], id = options.headers['X-Garden-Request-Id'];
      assert.match(id, /^[0-9a-f]{32}$/);
      assert(!ids.has(id)); ids.add(id);
      assert.equal(options.headers.Authorization, 'Bearer private-test-token');
      assert.equal(options.cache, 'no-store');
      assert.equal(logs[0].event, 'request_start');
      assert.equal(logs.at(-1).event, lastEvent);
      assert(logs.every(l => l.request_id === id && l.elapsed_ms >= 0));
      assert(!JSON.stringify(logs).includes('private-'));
      const pending = name.endsWith('_pending');
      assert.equal(settled, !pending);
      const panel = w.document.getElementById('bankPickerOptions');
      assert.equal(panel.textContent.includes('正在读取花园'), pending);
      assert.equal(panel.querySelectorAll('[data-garden-machine-option]').length, name === 'success' ? 1 : 0);
      if (name === 'unauthorized') assert(panel.textContent.includes('登录已失效'));
      if (name === 'server_error') assert(panel.textContent.includes('花园列表读取失败'));
    }
    mode = 'success'; logs.length = 0;
    const render = w.renderGardenCatMachinePicker;
    w.renderGardenCatMachinePicker = () => { throw new Error('private-render-error'); };
    await w.openGardenCatPicker();
    assert.equal(logs.find(l => l.event === 'error').phase, 'body_read');
    assert.equal(logs.at(-1).event, 'error_rendered');
    assert(!JSON.stringify(logs).includes('private-'));
    w.renderGardenCatMachinePicker = render;
    w.console.info = () => { throw new Error('console unavailable'); };
    await w.openGardenCatPicker();
    assert.equal(w.document.querySelectorAll('[data-garden-machine-option]').length, 1);
    assert.deepEqual(errors, []);
    console.log('PASS: 10 garden picker cases; request correlation, pre-response/body stalls, error exits, privacy, console failure and JS syntax.');
  } finally {
    dom.window.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
