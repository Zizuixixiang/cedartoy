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
  const channels = [
    {...used, config_id: 31, name: '巧克力 · NPC', model: 'gemini-3.1-flash-lite', current: true, enabled: true, price},
    {...zero, requests: 1, unknown: 1, failed: 1, config_id: 11, name: '小鸡农场', model: 'new-model-without-price',
      current: true, enabled: true, price: null},
    {...zero, requests: 1, success: 1, config_id: 28, name: '沉默 · 塔罗', model: '[anti量]gemini-3.8-flash',
      current: true, enabled: true, cost: '0.003', price},
  ];
  const prices = channels.map(({config_id, name, model, price}) => ({config_id, name, model, price}));
  prices.push({config_id: 31, name: '巧克力 · NPC', model: 'historical-model-outside-30-days', price: null},
    {config_id: 90, name: '旧渠道', model: 'deleted-channel-model', price: null, deleted: true},
    {config_id: 31, name: '巧克力 · NPC', model: 'manual-only-model', price: {mode: 'request', per_request: '0'}});
  const configs = channels.map(c => ({id: c.config_id, name: c.name, model: c.model, enabled: true}));
  configs.push({id: 80, name: '新渠道（尚未调用）', model: 'fresh-model', enabled: true});
  const data = {range: 'today', total: {...used, requests: 14, success: 12, failed: 2, unknown: 2, cost: '0.004461'},
    purposes: [{...zero, purpose: 'soup', label: '海龟汤'}, {...used, purpose: 'npc', label: 'NPC'},
      {...zero, requests: 1, success: 1, cost: '0.003', purpose: 'tarot', label: '塔罗'}], channels, prices, configs};
  const state = {data, requests: [], fail: false, failSave: false};
  state.fetch = async (url, options = {}) => {
    state.requests.push({url, options});
    let status = 200, body = {};
    if (url.startsWith('/api/admin/api-costs/prices')) {
      status = state.failSave ? 400 : 200;
      body = state.failSave ? {error: '价格校验失败'} : {ok: true};
      if (!state.failSave) {
        const {config_id, model, ...rule} = JSON.parse(options.body);
        let row = state.data.prices.find(r => r.config_id === config_id && r.model === model);
        if (!row) {
          row = {config_id, model, name: state.data.configs.find(c => c.id === config_id)?.name};
          state.data.prices.push(row);
        }
        row.price = rule;
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
  const savedBody = () => JSON.parse(state.requests.filter(r => r.url.endsWith('/prices')).at(-1).options.body);
  try {
    await settle();
    assert.equal(d.querySelectorAll('.admin-tabs [role="tab"]').length, 4);
    assert.equal($('costsTab').getAttribute('aria-selected'), 'true');
    assert.equal($('dashboardPanel').hidden, true);
    assert.equal($('costContent').hidden, false);
    assert.equal($('costPrices').hidden, true);
    assert.equal($('costTotal').textContent, '¥0.004461');
    assert.equal($('costTotalDetail').textContent, '部分待核算');
    assert.equal($('costPurposes').children.length, 3);
    assert.doesNotMatch($('costPurposes').textContent, /请求|估算|未知/);
    assert.equal(w.money('0.0000000000001'), '¥0.0000000000001');
    assert.equal(w.money('0.001000'), '¥0.001');
    assert.equal(d.querySelectorAll('#costChannels > details').length, 3);
    assert.equal(d.querySelectorAll('#costChannels details[open], .cost-editor[open]').length, 0);
    assert.equal($('costChannels').querySelectorAll('form').length, 0);
    assert.doesNotMatch($('costChannels').textContent, /尚未调用|historical-model/);
    assert.equal(w.knownCost({...zero, requests: 3, unknown: 3}), '待核算');
    assert.equal($('costSince'), null);
    assert.doesNotMatch($('costStats').textContent, /真实扣款|上线后|人民币（|32次请求/);
    for (const [range, amount] of [['7d', '¥0.1'], ['30d', '¥0.2'], ['today', '¥0.004461']]) {
      await change($('costRange'), range);
      assert.equal($('costTotal').textContent, amount);
      assert.equal($('costPriceList').children.length, 6);
      assert.match($('costPriceList').textContent, /historical-model-outside-30-days/);
      assert.match($('costPriceList').textContent, /manual-only-model/);
    }
    d.querySelector('[data-cost-view="prices"]').click();
    assert.equal($('costPrices').hidden, false);
    assert.equal($('costStats').hidden, true);
    assert.equal(d.querySelector('[data-cost-view="prices"]').getAttribute('aria-pressed'), 'true');
    assert.match(d.querySelector('[data-price-index="3"] summary').textContent, /¥0未设置/);
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
    assert.equal(d.activeElement, d.querySelector('[data-price-index="0"] summary'));
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
    assert.equal($('costPriceList').children.length, 7);
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
    assert.equal(state.data.prices.filter(r => r.config_id === 80).length, 2);
    assert.equal($('costPriceList').children.length, 8);
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
    assert.equal($('costPriceList').children.length, 8);
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
    console.log('PASS DOM: compact/collapsed statistics, independent historical prices, range totals, existing/new/deleted models, multi-model coexistence, unset versus explicit zero, save/error/busy/empty/retry states, escaping, stale responses. Rendered layout NOT verified.');
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
      await page.goto('http://costs.test/admin#costs');
      await page.locator('#costContent').waitFor({state: 'visible'});
      await page.locator('#costsTab').scrollIntoViewIfNeeded();
      assert.equal(await page.locator('#costChannels details[open]').count(), 0);
      assert.equal(await page.locator('#costChannels .cost-facts').first().isVisible(), false);
      const rows = await page.locator('#costChannels').boundingBox();
      assert.ok(rows.height < 230, 'three collapsed rows must stay compact');
      await shot(`api-costs-${width}`);
      await page.locator('#costChannels summary').first().focus(); await page.keyboard.press('Enter');
      assert.equal(await page.locator('#costChannels .cost-facts').first().isVisible(), true);
      await shot(`api-costs-detail-${width}`);
      await page.locator('[data-cost-view="prices"]').click();
      assert.equal(await page.locator('#costStats').isVisible(), false);
      await shot(`api-costs-prices-${width}`);
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
    console.log('PASS browser: 360/430/1280, compact rows, full-page screenshots, keyboard disclosure, existing/new price saves, long text/reflow, errors. Screenshots require manual visual review:', out);
  } finally { await browser.close(); }
}
(process.argv.includes('--browser') ? checkBrowser() : checkDOM()).catch(e => {console.error(e); process.exitCode = 1;});
