'use strict';
// Isolated admin fixture: DOM by default, --browser for actual rendered checks.
// No requests reach a live service or production database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../admin.html'), 'utf8');
const zero = {requests: 0, success: 0, failed: 0, input_tokens: 0, output_tokens: 0,
  cache_tokens: 0, reasoning_tokens: 0, unknown: 0, estimated: 0, missing_usage: 0, cost: '0'};
function fixture() {
  const price = {mode: 'tokens', input: '0.10', output: '0.60', cache: '0.01'};
  const used = {...zero, requests: 12, success: 11, failed: 1, input_tokens: 8100, output_tokens: 1400,
    cache_tokens: 2100, reasoning_tokens: 700, unknown: 1, cost: '0.001461'};
  const byPurpose = (soup, npc, tarot) => [
    {...zero, purpose: 'soup', label: '海龟汤', ...soup},
    {...zero, purpose: 'npc', label: 'NPC', ...npc},
    {...zero, purpose: 'tarot', label: '塔罗', ...tarot},
  ];
  const channels = [
    {...used, config_id: 31, name: '巧克力', model: 'gemini-3.1-flash-lite', current: true, enabled: true, price,
      purposes: byPurpose({requests: 5, success: 5, input_tokens: 3500, output_tokens: 600, cost: '0.0004'},
        {requests: 6, success: 5, failed: 1, unknown: 1, input_tokens: 3600, output_tokens: 600, cost: '0.000877'},
        {requests: 1, success: 1, input_tokens: 1000, output_tokens: 200, cost: '0.000184'})},
    {...zero, requests: 1, unknown: 1, failed: 1, config_id: 11, name: '小鸡农场', model: 'new-model-without-price',
      current: true, enabled: true, price: null, purposes: byPurpose({}, {requests: 1, unknown: 1, failed: 1}, {})},
    {...zero, requests: 1, success: 1, estimated: 1, config_id: 28, name: '沉默', model: '[正向量3]deepseek-v4.1-flash',
      current: true, enabled: true, cost: '0.003', price: {mode: 'tokens', input: '0.100', output: '0.400', cache: '0.0020', reference_only: true},
      purposes: byPurpose({}, {}, {requests: 1, success: 1, estimated: 1, cost: '0.003'})},
  ];
  const prices = channels.map(({config_id, name, model, price}) => ({config_id, name, model, price}));
  prices.push({config_id: 31, name: '巧克力', model: 'historical-model-outside-30-days', price: null},
    {config_id: 90, name: '旧渠道', model: 'deleted-channel-model', price: null, deleted: true},
    {config_id: 31, name: '巧克力', model: 'manual-only-model', price: {mode: 'request', per_request: '0'}});
  const configs = channels.map(c => ({id: c.config_id, name: c.name, model: c.model, enabled: true}));
  configs.push({id: 80, name: '新渠道（尚未调用）', model: 'fresh-model', enabled: true});
  // Same merchant, a second exact model; raw member IDs remain distinct.
  const glm = {...zero, config_id: 28, name: '沉默', model: '[按量1]glm-5.3-flash',
    current: false, enabled: true, requests: 2, success: 2, unknown: 2, input_tokens: 300, output_tokens: 60,
    purposes: byPurpose({requests: 2, success: 2, unknown: 2, input_tokens: 300, output_tokens: 60}, {}, {})};
  channels.push(glm);
  prices.push({...glm, price: null, price_conflict: true});
  prices.push({config_id: 80, name: '新渠道（尚未调用）', model: 'fresh-model', price: null});
  const members = id => ({31: [31,32,33], 11: [11,17,24], 28: [28,29,30]}[id] || [id]);
  for (const cfg of configs) cfg.config_ids = members(cfg.id);
  for (const row of [...channels, ...prices]) {
    row.config_ids = members(row.config_id);
    row.price_versions = Object.fromEntries(row.config_ids.map(id => [id, row.price ? 1 : 0]));
    row.price_variants = row.config_ids.map((id, i) => ({config_id: id, label: ['海龟汤','NPC','塔罗'][i], price: row.price || null}));
  }
  const conflicting = prices[6];
  conflicting.price_variants[0].price = {mode: 'tokens', input: '0.064', output: '0.224', cache: '0.0184'};
  conflicting.price_variants[1].price = {mode: 'tokens', input: '0.080', output: '0.224', cache: '0.0184'};
  conflicting.price_versions = {'28': 1, '29': 2, '30': 0};
  const data = {range: 'today', total: {...used, requests: 16, success: 14, failed: 2, unknown: 4, estimated: 1, input_tokens: 8400, output_tokens: 1460, cost: '0.004461'},
    purposes: byPurpose({requests: 7, success: 7, unknown: 2, input_tokens: 3800, output_tokens: 660, cost: '0.0004'},
      {requests: 7, success: 5, failed: 2, unknown: 2, input_tokens: 3600, output_tokens: 600, cost: '0.000877'},
      {requests: 2, success: 2, estimated: 1, input_tokens: 1000, output_tokens: 200, cost: '0.003184'}), channels, prices, configs};
  const state = {data, requests: [], fail: false, failSave: false};
  state.fetch = async (url, options = {}) => {
    state.requests.push({url, options});
    let status = 200, body = {};
    if (url.startsWith('/api/admin/api-costs/prices')) {
      status = state.failSave ? 400 : 200;
      body = state.failSave ? {error: '价格校验失败'} : {ok: true};
      if (!state.failSave) {
        const {config_id, config_ids, price_versions, model, ...rule} = JSON.parse(options.body);
        let row = state.data.prices.find(r => r.config_id === config_id && r.model === model);
        const ids = row?.config_ids || state.data.configs.find(c => c.id === config_id)?.config_ids;
        const versions = row?.price_versions || Object.fromEntries(ids.map(id => [id, 0]));
        assert.deepEqual(config_ids, ids, 'one atomic save must contain all merchant member IDs');
        if (JSON.stringify(price_versions) !== JSON.stringify(versions)) {
          status = 400; body = {error: '价格已变化，请刷新查看最新价格后再保存'};
        } else {
          if (!row) {
            row = {config_id, config_ids, model, name: state.data.configs.find(c => c.id === config_id)?.name};
            state.data.prices.push(row);
          }
          row.price = rule; row.price_conflict = false;
          row.price_versions = Object.fromEntries(ids.map(id => [id, versions[id]+1]));
          row.price_variants = ids.map((id,i) => ({config_id:id, label:['海龟汤','NPC','塔罗'][i], price:rule}));
        }
      }
    } else if (url.startsWith('/api/admin/api-costs')) {
      status = state.fail ? 503 : 200;
      body = state.fail ? {error: '费用统计暂时不可用'} : structuredClone(state.data);
      // Exercise range selection with visibly different statistics, same catalog.
      const range = new URL(url, 'http://costs.test').searchParams.get('range');
      if (!state.fail && range !== 'today') {
        body.range = range;
        body.total.cost = range === '7d' ? '0.1' : '0.2';
      }
    } else if (url.startsWith('/api/admin/users')) body = {users: [], total: 0, page: 1};
    return {ok: status === 200, status, json: async () => structuredClone(body)};
  };
  return state;
}
async function checkDOM() {
  const {JSDOM, VirtualConsole} = require('jsdom');
  const state = fixture(), errors = [], vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(html, {url: 'http://costs.test/admin#costs', runScripts: 'dangerously',
    pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
      w.localStorage.setItem('cedartoy_token', 'fixture'); w.fetch = state.fetch;
    }});
  const w = dom.window, d = w.document, $ = id => d.getElementById(id);
  const settle = () => new Promise(resolve => setTimeout(resolve, 10));
  const change = async (el, value) => { el.value = value; el.dispatchEvent(new w.Event('change', {bubbles: true})); await settle(); };
  const submit = async form => { form.dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true})); await settle(); };
  const savedGroup = () => JSON.parse(state.requests.filter(r => r.url.endsWith('/prices')).at(-1).options.body);
  const savedBody = () => {const {config_ids, price_versions, ...rule} = savedGroup(); return rule;};
  try {
    await settle();
    assert.equal(d.querySelectorAll('.admin-tabs [role="tab"]').length, 4);
    assert.equal($('costsTab').getAttribute('aria-selected'), 'true');
    assert.equal($('dashboardPanel').hidden, true);
    assert.equal($('costContent').hidden, false);
    assert.equal($('costPrices').hidden, true);
    assert.equal($('costTotal').textContent, '¥0.004461');
    assert.equal($('costTotalDetail').textContent, '含参考估算 · 16 次调用 · Token 输入 8,400 / 输出 1,460');
    assert.equal($('costPurposes').children.length, 3);
    assert.match($('costPurposes').children[0].textContent, /海龟汤.*¥0.0004.*7 次调用.*输入 3,800.*输出 660/s);
    assert.match($('costPurposes').children[1].textContent, /NPC.*¥0.000877.*7 次调用.*输入 3,600.*输出 600/s);
    assert.match($('costPurposes').children[2].textContent, /塔罗.*¥0.003184.*2 次调用.*输入 1,000.*输出 200/s);
    assert.ok($('costStats').querySelector('.cost-eyebrow'));
    assert.ok($('costStats').querySelector('.cost-section-title'));
    assert.doesNotMatch($('costPurposes').textContent, /请求|未知/);
    assert.equal(w.money('0.0000000000001'), '¥0.0000000000001');
    assert.equal(w.money('0.001000'), '¥0.001');
    assert.equal(d.querySelectorAll('#costChannels > details').length, 4);
    assert.deepEqual([...d.querySelectorAll('#costChannels .cost-identity strong')].map(el => el.textContent), ['巧克力', '小鸡农场', '沉默', '沉默']);
    assert.match($('costStats').textContent, /渠道明细/);
    const stats = d.querySelector('#costChannels .cost-facts');
    assert.equal(stats.querySelectorAll('dt').length, 2);
    assert.match(stats.textContent, /调用次数12 次/);
    assert.match(stats.textContent, /Token · 输入 \/ 输出8,100 \/ 1,400/);
    assert.doesNotMatch(stats.textContent, /缓存|推理|待核算|估算|缺少用量|成功|失败/);
    const business = d.querySelector('#costChannels .cost-details .cost-breakdown');
    assert.ok(business);
    assert.equal(business.querySelectorAll('.cost-breakdown-line').length, 3);
    assert.deepEqual([...business.querySelectorAll('.cost-breakdown-line')].map(el => el.textContent.trim()),
      ['海龟汤5 次 · 输入 3,500 / 输出 600¥0.0004', 'NPC6 次 · 输入 3,600 / 输出 600¥0.000877', '塔罗1 次 · 输入 1,000 / 输出 200¥0.000184']);
    const unpricedBusiness = d.querySelectorAll('#costChannels .cost-breakdown')[1];
    assert.match(unpricedBusiness.textContent, /NPC1 次 · 输入 0 \/ 输出 0¥0/);
    assert.match(d.querySelectorAll('#costChannels .cost-breakdown')[2].textContent, /塔罗1 次 · 输入 0 \/ 输出 0 · 含参考估算¥0.003/);
    assert.equal(business.querySelectorAll('.cost-breakdown-name small').length, 3);
    assert.deepEqual([...d.querySelectorAll('#costChannels .cost-breakdown')].map(el => el.children.length), [3, 3, 3, 3]);
    assert.equal(d.querySelectorAll('#costChannels details[open], .cost-editor[open]').length, 0);
    assert.equal($('costChannels').querySelectorAll('form').length, 0);
    assert.doesNotMatch($('costChannels').textContent, /尚未调用|historical-model/);
    assert.equal(w.knownCost({...zero, requests: 3, unknown: 3}), '¥0');
    assert.equal($('costSince'), null);
    assert.doesNotMatch($('costStats').textContent, /待核算|部分待核算/);
    assert.match($('costStats').textContent, /未计价部分按 ¥0 展示.*不代表实际扣费/);
    for (const [range, amount] of [['7d', '¥0.1'], ['30d', '¥0.2'], ['today', '¥0.004461']]) {
      await change($('costRange'), range);
      assert.equal($('costTotal').textContent, amount);
      assert.equal($('costPriceList').children.length, 8);
      assert.match($('costPriceList').textContent, /historical-model-outside-30-days/);
      assert.match($('costPriceList').textContent, /manual-only-model/);
    }
    d.querySelector('[data-cost-view="prices"]').click();
    assert.equal($('costPrices').hidden, false);
    assert.equal($('costStats').hidden, true);
    assert.equal(d.querySelector('[data-cost-view="prices"]').getAttribute('aria-pressed'), 'true');
    assert.match(d.querySelector('[data-price-index="3"] summary').textContent, /未设置/);
    assert.doesNotMatch(d.querySelector('[data-price-index="3"] summary').textContent, /¥/);
    assert.equal(d.querySelector('[data-cost-form="new"] [name="config_id"]').options.length, 4);
    assert.match(d.querySelector('[data-price-index="5"] summary').textContent, /¥0\/次/);
    assert.doesNotMatch(d.querySelector('[data-price-index="5"] summary').textContent, /未设置/);
    let form = d.querySelector('[data-cost-form="0"]');
    assert.equal(form.elements.model, undefined); // Editing a tariff cannot change the model.
    assert.equal(form.elements.per_request.disabled, true);
    assert.equal(form.elements.input.value, '0.10');
    form.elements.input.value = '0.25';
    state.failSave = true;
    await submit(form);
    assert.match(form.querySelector('.status').textContent, /价格校验失败/);
    assert.equal(form.elements.input.value, '0.25');
    assert.equal(form.querySelector('button').disabled, false);
    state.failSave = false;
    await submit(form);
    assert.match($('costStatus').textContent, /价格已保存/);
    assert.deepEqual(savedBody(), {config_id: 31, model: 'gemini-3.1-flash-lite', mode: 'tokens', input: '0.25', output: '0.60', cache: '0.01'});
    assert.deepEqual(savedGroup().config_ids, [31,32,33]);
    assert.deepEqual(savedGroup().price_versions, {'31': 1, '32': 1, '33': 1});
    assert.equal(d.activeElement, d.querySelector('[data-price-index="0"] summary'));
    // Dynamic base tariffs remain reference estimates through edit/save/reload.
    assert.match($('costTotalDetail').textContent, /含参考估算/);
    assert.match($('costPurposes').textContent, /含参考估算/);
    assert.match(d.querySelectorAll('#costChannels summary')[2].textContent, /含参考估算/);
    assert.match(d.querySelector('[data-price-index="2"] summary').textContent, /参考估算/);
    form = d.querySelector('[data-cost-form="2"]');
    assert.equal(form.elements.mode.value, 'tokens_reference');
    assert.equal(form.elements.cache.value, '0.0020');
    assert.equal(form.elements.cache.disabled, false);
    await submit(form);
    assert.deepEqual(savedBody(), {config_id: 28, model: '[正向量3]deepseek-v4.1-flash', mode: 'tokens', reference_only: true,
      input: '0.100', output: '0.400', cache: '0.0020'});
    form = d.querySelector('[data-cost-form="new"]');
    await change(form.elements.config_id, '28');
    assert.equal(form.elements.mode.value, 'tokens_reference');
    await change(form.elements.model, '[按量1]glm-5.3-flash');
    assert.equal(form.elements.mode.value, 'tokens');
    assert.equal(form.elements.cache.value, '');
    assert.match(form.querySelector('[data-price-scope]').textContent, /价格不一致.*0.064.*0.080.*未设置/);
    // Conflict is explicit, no arbitrary member's tariff prefilled. Stale
    // versions reject the whole save; refresh then deliberately unify all IDs.
    form = d.querySelector('[data-cost-form="6"]');
    assert.match(d.querySelector('[data-price-index="6"] summary').textContent, /价格不一致/);
    assert.equal(form.elements.input.value, '');
    assert.equal(form.querySelector('button').textContent, '统一保存价格');
    for (const [key, value] of Object.entries({input: '0.064', output: '0.224', cache: '0.0184'})) form.elements[key].value = value;
    state.data.prices[6].price_versions['29'] += 1;
    const beforeConflict = structuredClone(state.data.prices[6]);
    await submit(form);
    assert.match(form.querySelector('.status').textContent, /价格已变化.*刷新/);
    assert.deepEqual(state.data.prices[6], beforeConflict);
    assert.equal(form.elements.input.value, '0.064');
    await w.loadCosts();
    form = d.querySelector('[data-cost-form="6"]');
    for (const [key, value] of Object.entries({input: '0.064', output: '0.224', cache: '0.0184'})) form.elements[key].value = value;
    await submit(form);
    assert.deepEqual(savedGroup().config_ids, [28,29,30]);
    assert.equal(state.data.prices[6].price_conflict, false);
    assert.ok(state.data.prices[6].price_variants.every(v => v.price.input === '0.064' && v.price.cache === '0.0184'));
    assert.equal(state.data.prices[2].price.reference_only, true);
    assert.equal($('costTotal').textContent, '¥0.004461');
    // Both historical and deleted-channel prices are directly editable.
    for (const index of [3, 4]) {
      form = d.querySelector(`[data-cost-form="${index}"]`);
      await change(form.elements.mode, 'request');
      assert.equal(form.elements.input.disabled, true);
      form.elements.per_request.value = '0.000001';
      await submit(form);
      assert.equal(savedBody().model, state.data.prices[index].model);
      assert.equal(savedBody().config_id, state.data.prices[index].config_id);
    }
    // New, uncalled channel; exact model can differ from its current setting.
    form = d.querySelector('[data-cost-form="new"]');
    await change(form.elements.config_id, '80');
    assert.equal(form.elements.model.value, 'fresh-model');
    await change(form.elements.model, 'new-exact-model');
    assert.equal(form.elements.input.value, '');
    await change(form.elements.mode, 'request');
    form.elements.per_request.value = '0';
    await submit(form);
    assert.deepEqual(savedBody(), {config_id: 80, model: 'new-exact-model', mode: 'request', per_request: '0'});
    assert.equal($('costPriceList').children.length, 9);
    assert.match($('costPriceList').textContent, /new-exact-model/);
    assert.match($('costPriceList').textContent, /manual-only-model/);
    assert.equal(state.data.configs.find(c => c.id === 80).model, 'fresh-model');
    assert.equal($('costTotal').textContent, '¥0.004461');
    // Adding a second model cannot remove the first; selecting an existing one loads its price.
    form = d.querySelector('[data-cost-form="new"]');
    await change(form.elements.config_id, '80');
    await change(form.elements.model, 'new-exact-model');
    assert.equal(form.elements.per_request.value, '0');
    await change(form.elements.model, 'second-model');
    assert.equal(form.elements.per_request.value, '');
    await change(form.elements.mode, 'request');
    form.elements.per_request.value = '0.02';
    await submit(form);
    assert.equal(state.data.prices.filter(r => r.config_id === 80).length, 3);
    assert.equal($('costPriceList').children.length, 10);
    // Busy submission sends one request and locks refresh/range until completion.
    let releaseSave;
    w.fetch = (url, options) => url.endsWith('/prices') ? new Promise(resolve => { releaseSave = () => resolve(state.fetch(url, options)); }) : state.fetch(url, options);
    form = d.querySelector('[data-cost-form="0"]');
    await submit(form); await submit(form);
    assert.equal($('costRange').disabled, true);
    assert.equal($('costRefresh').disabled, true);
    const beforeSave = state.requests.filter(r => r.url.endsWith('/prices')).length;
    releaseSave(); await settle();
    assert.equal(state.requests.filter(r => r.url.endsWith('/prices')).length, beforeSave + 1);
    w.fetch = state.fetch;
    state.fail = true; await w.loadCosts();
    assert.equal($('costContent').hidden, true);
    assert.match($('costStatus').textContent, /刷新重试/);
    state.fail = false;
    state.data.channels[0].name = '<img src=x onerror="window.bad=1">';
    state.data.prices[0].model = '" autofocus onfocus="window.bad=1';
    await w.loadCosts();
    assert.equal($('costContent').querySelectorAll('img,script').length, 0);
    assert.equal(w.bad, undefined);
    state.data.total = {...zero}; state.data.channels = [];
    await w.loadCosts();
    assert.match($('costChannels').textContent, /暂无调用/);
    assert.equal($('costPriceList').children.length, 10);
    state.data.prices = []; state.data.configs = []; await w.loadCosts();
    assert.match($('costPriceList').textContent, /暂无模型价格/);
    assert.match($('costNewPriceForm').textContent, /暂无渠道配置/);
    let release;
    w.fetch = () => new Promise(resolve => { release = resolve; });
    const old = w.loadCosts();
    w.fetch = state.fetch; await w.loadCosts();
    release({ok: true, json: async () => fixture().data}); await old;
    assert.match($('costPriceList').textContent, /暂无模型价格/);
    assert.deepEqual(errors, []);
    console.log('PASS DOM: merchant/model groups, business tokens/totals, unknown displays zero, unset tariffs, atomic group saves/conflicts/stale edits, reference prices, history/ranges, busy/empty/error/escaping. Rendered layout NOT verified.');
  } finally { w.close(); }
}
async function checkBrowser() {
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'], executablePath: process.env.CHROMIUM_PATH});
  const out = process.env.SCREENSHOT_DIR || '/tmp/cedartoy-api-costs-screenshots';
  fs.mkdirSync(out, {recursive: true});
  try {
    const context = await browser.newContext();
    let state;
    await context.addInitScript(() => localStorage.setItem('cedartoy_token', 'fixture'));
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/admin') return route.fulfill({contentType: 'text/html', body: html});
      if (url.pathname.startsWith('/api/')) {
        const res = await state.fetch(url.pathname + url.search, {body: route.request().postData()});
        return route.fulfill({status: res.status, json: await res.json()});
      }
      return route.abort();
    });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const noOverflow = async () => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'page overflow');
    const shot = async name => { await noOverflow(); await page.screenshot({path: path.join(out, name + '.png'), fullPage: true}); };
    for (const width of [360, 430, 1280]) {
      state = fixture();
      await page.setViewportSize({width, height: 900});
      await page.goto(`http://costs.test/admin?viewport=${width}#costs`);
      await page.locator('#costContent').waitFor({state: 'visible'});
      await page.locator('#costChannels > details').first().waitFor({state: 'visible'});
      await page.locator('#costsTab').scrollIntoViewIfNeeded();
      assert.equal(await page.locator('#costChannels details[open]').count(), 0);
      assert.equal(await page.locator('#costChannels .cost-facts').first().isVisible(), false);
      const rows = await page.locator('#costChannels').boundingBox();
      assert.ok(rows.height < 300, 'four grouped rows must stay compact');
      await shot(`api-costs-${width}`);
      await page.locator('#costChannels summary').first().focus(); await page.keyboard.press('Enter');
      assert.equal(await page.locator('#costChannels .cost-facts').first().isVisible(), true);
      await shot(`api-costs-detail-${width}`);
      await page.locator('[data-cost-view="prices"]').click();
      assert.equal(await page.locator('#costStats').isVisible(), false);
      await shot(`api-costs-prices-${width}`);
      await page.locator('[data-price-index="2"] summary').click();
      const referenceForm = page.locator('[data-cost-form="2"]');
      assert.equal(await referenceForm.locator('[name="mode"]').inputValue(), 'tokens_reference');
      assert.equal(await referenceForm.locator('[name="cache"]').inputValue(), '0.0020');
      await shot(`api-costs-reference-${width}`);
      await referenceForm.locator('button').click();
      await page.getByText('价格已保存', {exact: true}).waitFor();
      assert.equal(state.data.prices[2].price.reference_only, true);
      await page.locator('[data-price-index="6"] summary').click();
      const conflictForm = page.locator('[data-cost-form="6"]');
      assert.match(await conflictForm.locator('[data-price-scope]').textContent(), /价格不一致.*0.064.*0.080.*未设置/);
      assert.equal(await conflictForm.locator('[name="input"]').inputValue(), '');
      await shot(`api-costs-conflict-${width}`);
      await conflictForm.locator('[name="input"]').fill('0.064');
      await conflictForm.locator('[name="output"]').fill('0.224');
      await conflictForm.locator('[name="cache"]').fill('0.0184');
      await conflictForm.getByRole('button', {name: '统一保存价格'}).click();
      await page.getByText('价格已保存', {exact: true}).waitFor();
      assert.ok(state.data.prices[6].price_variants.every(v => v.price.input === '0.064'));
      await page.locator('[data-price-index="3"] summary').focus(); await page.keyboard.press('Enter');
      const form = page.locator('[data-cost-form="3"]');
      await form.locator('[name="mode"]').selectOption('request');
      await form.locator('[name="per_request"]').fill('0.001');
      await shot(`api-costs-editor-${width}`);
      await form.locator('button').click();
      await page.getByText('价格已保存', {exact: true}).waitFor();
      await page.locator('#costNewPrice > summary').click();
      const newForm = page.locator('[data-cost-form="new"]');
      await newForm.locator('[name="config_id"]').selectOption('80');
      await newForm.locator('[name="model"]').fill('new-exact-model');
      await newForm.locator('[name="model"]').blur();
      await newForm.locator('[name="mode"]').selectOption('request');
      await newForm.locator('[name="per_request"]').fill('0.02');
      await shot(`api-costs-new-price-${width}`);
      await newForm.locator('button').click();
      await page.locator('#costPriceList').getByText('new-exact-model', {exact: true}).waitFor();
      // Long exact names, enlarged text and reduced motion must still reflow.
      state.data.prices[0].model = 'long-exact-model-'.repeat(12);
      await page.locator('#costRefresh').click();
      await page.locator('[data-price-index="0"] summary').click();
      await page.emulateMedia({reducedMotion: 'reduce'});
      await page.addStyleTag({content: '#costsPanel {font-size:28px} #costsPanel :is(strong,span,small,label,input,select,summary,dt,dd) {font-size:inherit}'});
      await shot(`api-costs-long-large-${width}`);
    }
    state.fail = true; await page.locator('#costRefresh').click();
    await page.getByText('费用统计暂时不可用，可点击刷新重试。', {exact: true}).waitFor();
    await shot('api-costs-error');
    assert.deepEqual(errors, []);
    console.log('PASS browser: 360/430/1280, grouped/expanded rows, conflict/unified saves, reference prices, keyboard, long text/reflow, errors. Screenshots require manual visual review:', out);
  } finally { await browser.close(); }
}
(process.argv.includes('--browser') ? checkBrowser() : checkDOM()).catch(e => {console.error(e); process.exitCode = 1;});
