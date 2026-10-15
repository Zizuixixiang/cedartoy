/* Functional fallback using the actual React page. No network or real credentials.
 * Does not replace check_turtle_admin_api_ui.js or rendered visual review. */
const assert = require('node:assert/strict');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const frontend = path.resolve(__dirname, '../turtle-soup/frontend');
const esbuild = require(path.join(frontend, 'node_modules/esbuild'));
const pause = () => new Promise(resolve => setTimeout(resolve, 20));
const purposes = ['both', 'judge', 'hint', 'npc', 'npc_decision', 'npc_speech', 'tarot', 'all', 'future'];
const fixtures = purposes.map((purpose, index) => ({
  id: index + 1, name: `渠道${index + 1}`, purpose, enabled: index === 2 ? 0 : 1, priority: index === 1 ? 9 : index === 2 ? 0 : 1,
  api_url: 'https://example.invalid/v1', api_key: 'sk-f...MASK', model: `fixture-model-${index}`, created_at: '2026-10-14 12:00:00',
  runtime_status: index === 1 ? 'cooling' : index === 3 ? 'half_open' : index === 8 ? null : 'healthy', consecutive_failures: 3, cooldown_remaining_seconds: 45,
}));
(async () => {
  const compiled = await esbuild.build({ entryPoints: [path.join(frontend, 'src/main.jsx')], bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: { '.css': 'empty' }, define: { 'process.env.NODE_ENV': '"test"' } });
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://soup.test/soup/admin', runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window, doc = w.document, errors = [], writes = [], unexpected = [];
  let configs = structuredClone(fixtures), denied = false, testFail = false, saveFail = false, loadFail = false, malformed = false, confirmDelete = false;
  w.addEventListener('error', event => errors.push(event.message));
  w.localStorage.setItem('cedartoy_user_id', '201');
  w.localStorage.setItem('cedartoy_token', 'platform-fixture');
  w.confirm = () => confirmDelete;
  const player = { id: 7, user_id: 201, username: '南杉', is_admin: true, is_guest: false };
  w.fetch = async (url, options = {}) => {
    const endpoint = url.replace('/soup/api', ''), method = options.method || 'GET';
    let data = {}, status = 200;
    if (endpoint === '/auth/guest') data = { token: 'soup-fixture', player: { ...player, is_admin: !denied } };
    else if (endpoint === '/auth/me') data = { player: { ...player, is_admin: !denied } };
    else if (endpoint === '/admin/overview') data = { puzzles: 12, rooms: 2 };
    else if (endpoint === '/admin/api-configs' && method === 'GET') {
      await pause();
      if (loadFail) { status = 503; data = { detail: '读取失败，请稍后刷新' }; }
      else data = malformed ? {} : structuredClone(configs);
    } else if (endpoint.endsWith('/test') || endpoint.endsWith('/models')) {
      writes.push({ endpoint, method, body: JSON.parse(options.body), auth: options.headers.Authorization });
      await pause();
      data = testFail
        ? { success: false, message: 'private-credential', raw: { api_key: 'private-credential' }, data: { http_status: 429, llm_ms: 42, raw: { api_key: 'private-credential' } } }
        : { success: true, data: { config_name: '测试渠道', model: 'fixture-model', reply: '连接成功 sk-fake-secret api_key="hidden-secret"', http_status: 200, llm_ms: 42, api_key: 'private-credential' } };
    } else if (/^\/admin\/api-configs(?:\/\d+)?$/.test(endpoint) && method !== 'GET') {
      const body = options.body ? JSON.parse(options.body) : null;
      writes.push({ endpoint, method, body, auth: options.headers.Authorization });
      if (saveFail) { status = 400; data = { detail: '用途配置不完整，请检查后重试' }; }
      else if (method === 'PUT') configs = configs.map(row => row.id === Number(endpoint.split('/').at(-1)) ? { ...row, ...body, api_key: row.api_key } : row);
      else if (method === 'POST') configs.push({ ...body, id: 90, api_key: '****' });
      else if (method === 'DELETE') configs = configs.filter(row => row.id !== Number(endpoint.split('/').at(-1)));
    } else unexpected.push(`${method} ${endpoint}`);
    return { ok: status === 200, status, json: async () => data };
  };
  const waitFor = async check => { for (let i = 0; i < 100 && !check(); i++) await pause(); assert.ok(check(), 'UI settles'); };
  const button = (text, within = doc) => [...within.querySelectorAll('button')].find(el => el.textContent === text);
  const click = async el => { assert.ok(el, 'control exists'); el.focus(); el.click(); await pause(); };
  const change = async (el, value) => {
    assert.ok(el, 'input exists');
    Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? w.HTMLSelectElement.prototype : w.HTMLInputElement.prototype, 'value').set.call(el, value);
    el.dispatchEvent(new w.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); await pause();
  };
  const field = name => [...doc.querySelectorAll('.admin-field')].find(el => el.querySelector('span').textContent === name)?.querySelector('input, select');
  const row = name => [...doc.querySelectorAll('.api-config-row')].find(el => el.querySelector('strong').textContent === name);
  const group = id => doc.querySelector(`#api-group-${id}`).closest('section');
  const escape = async () => { doc.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await pause(); };
  try {
    w.eval(compiled.outputFiles[0].text);
    await waitFor(() => doc.querySelector('.stats'));
    await click(button('模型 API'));
    await waitFor(() => row('渠道1'));
    assert.equal(group('soup').querySelectorAll('li').length, 3);
    assert.equal(group('npc').querySelectorAll('li').length, 3);
    assert.equal(group('tarot').querySelectorAll('li').length, 1);
    assert.equal(group('shared').querySelectorAll('li').length, 1);
    assert.equal(group('other').querySelectorAll('li').length, 1);
    assert.equal([...doc.querySelectorAll('strong')].filter(el => el.textContent === '渠道8').length, 1);
    assert.deepEqual([...group('soup').querySelectorAll('strong')].map(el => el.textContent), ['渠道3', '渠道1', '渠道2']);
    assert.match(row('渠道4').textContent, /旧版备用/);
    assert.match(row('渠道2').textContent, /优先级 9.*冷却中 · 剩余 45 秒/);
    assert.match(row('渠道3').textContent, /已停用 · 不参与调用/);
    assert.match(row('渠道9').textContent, /状态未知/);
    assert.equal(doc.querySelector('.api-routing-notes').open, false);
    assert.doesNotMatch(doc.querySelector('.api-config-groups').textContent, /example.invalid|MASK|fixture-model|2026-10-14/);
    for (const [needle, count] of [['海龟汤', 4], ['both', 1], ['npc', 3], ['tarot', 1], ['all', 1], ['fixture-model-6', 1], ['已停用', 1], ['不存在', 0]]) {
      await change(doc.querySelector('.admin-search'), needle);
      assert.equal(doc.querySelectorAll('.api-config-row').length, count, `search ${needle}`);
    }
    assert.match(doc.querySelector('.api-config-groups').textContent, /没有匹配/);
    await change(doc.querySelector('.admin-search'), '');
    for (const [filter, purpose, count] of [['soup', 'both', 4], ['npc', 'npc_decision', 4], ['tarot', 'tarot', 1], ['shared', 'all', 1], ['all-groups', 'both', 9]]) {
      await change(doc.querySelector('.api-config-tools select'), filter);
      assert.equal(doc.querySelectorAll('.api-config-row').length, count);
      await click(button('新增'));
      assert.equal(field('用途').value, purpose);
      assert.deepEqual([...field('用途').options].map(o => o.value), ['judge', 'hint', 'both', 'npc_decision', 'npc_speech', 'npc', 'all', 'tarot']);
      // Keyboard focus stays inside the dialog and returns to the trigger.
      const first = doc.activeElement;
      first.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }));
      assert.equal(doc.activeElement.textContent, '取消');
      doc.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
      assert.equal(doc.activeElement, first);
      await escape();
      assert.equal(doc.activeElement, button('新增'));
    }
    await click(button('查看', row('渠道1')));
    assert.match(doc.querySelector('[role="dialog"]').textContent, /example.invalid.*MASK.*fixture-model-0/);
    await escape();
    assert.equal(doc.activeElement, button('查看', row('渠道1')));
    await click(button('编辑', row('渠道1')));
    assert.equal(field('密钥').value, '');
    assert.equal(field('密钥').type, 'password');
    await change(field('优先级'), '4');
    await click(field('启用'));
    saveFail = true;
    await click(button('保存'));
    await waitFor(() => doc.querySelector('.admin-modal .error'));
    assert.equal(field('优先级').value, '4');
    saveFail = false;
    await click(button('保存'));
    await waitFor(() => !doc.querySelector('.admin-modal') && row('渠道1').textContent.includes('优先级 4'));
    assert.deepEqual(writes.at(-1).body, { name: '渠道1', api_url: 'https://example.invalid/v1', api_key: '', model: 'fixture-model-0', purpose: 'both', enabled: 0, priority: 4 });
    assert.match(row('渠道1').textContent, /已停用/);
    await click(button('测试', row('渠道7')));
    await waitFor(() => doc.querySelector('.cfg-test-result'));
    assert.equal(writes.at(-1).endpoint, '/admin/api-configs/7/test');
    assert.match(doc.querySelector('.cfg-test-result').textContent, /测试成功.*HTTP：200/s);
    assert.match(doc.querySelector('.cfg-test-result').textContent, /耗时：42ms.*连接成功/s);
    assert.doesNotMatch(doc.querySelector('.cfg-test-result').textContent, /sk-fake-secret|hidden-secret|private-credential/);
    await click(button('编辑', row('渠道7')));
    testFail = true;
    await click(button('测试', doc.querySelector('.admin-modal')));
    await waitFor(() => doc.querySelector('.cfg-test-result--modal'));
    assert.match(doc.querySelector('.cfg-test-result--modal').textContent, /测试失败.*HTTP：429/s);
    assert.doesNotMatch(doc.querySelector('.cfg-test-result--modal').textContent, /private-credential/);
    assert.equal(writes.at(-1).body.config_id, 7);
    await click(button('拉取模型'));
    await waitFor(() => doc.querySelector('.cfg-test-result--modal')?.textContent.includes('拉取失败'));
    assert.doesNotMatch(doc.querySelector('.cfg-test-result--modal').textContent, /private-credential/);
    await escape();
    await change(doc.querySelector('.api-config-tools select'), 'tarot');
    await click(button('新增'));
    await change(field('名称'), '新增解读');
    await change(field('接口地址'), 'https://example.invalid/v1');
    await change(field('模型'), 'fixture-new');
    await click(button('保存'));
    await waitFor(() => row('新增解读'));
    assert.equal(writes.at(-1).body.purpose, 'tarot');
    const count = writes.length;
    await click(button('删除', row('新增解读')));
    assert.equal(writes.length, count);
    confirmDelete = true;
    await click(button('删除', row('新增解读')));
    await waitFor(() => !row('新增解读'));
    assert.equal(writes.at(-1).method, 'DELETE');
    loadFail = true;
    await click(button('刷新'));
    await waitFor(() => doc.querySelector('[role="alert"]'));
    assert.match(doc.querySelector('.api-config-tools').textContent, /上次记录/);
    loadFail = false; configs = [];
    await click(button('刷新'));
    await waitFor(() => doc.querySelector('.api-config-empty'));
    assert.match(doc.querySelector('.api-config-empty').textContent, /暂无配置/);
    malformed = true;
    await click(button('刷新'));
    await waitFor(() => doc.querySelector('[role="alert"]'));
    assert.match(doc.querySelector('[role="alert"]').textContent, /格式异常/);
    denied = true;
    await click(button('刷新'));
    await waitFor(() => doc.querySelector('.empty-state'));
    assert.match(doc.querySelector('.empty-state').textContent, /没有管理员权限/);
    assert.equal(doc.querySelector('.api-config-row, .admin-toolbar'), null);
    assert.ok(writes.every(write => write.auth === 'Bearer soup-fixture'));
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log('PASS: actual React DOM — all purposes/shared/legacy, search/filter/defaults, view/edit/add/delete/test, serialization, redaction, keyboard focus, priority/state, empty/error/auth. All requests mocked.');
  } finally { w.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
