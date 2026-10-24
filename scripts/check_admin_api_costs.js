'use strict';
// Isolated admin fixture: DOM by default, --browser for real rendered checks.
// PLAYWRIGHT_MODULE / CHROMIUM_PATH may point at existing local installations.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../admin.html'), 'utf8');
const zero = {requests: 0, success: 0, failed: 0, input_tokens: 0, output_tokens: 0,
  cache_tokens: 0, reasoning_tokens: 0, unknown: 0, estimated: 0, missing_usage: 0, cost: '0'};
function fixture() {
  const price = {mode: 'tokens', input: '0.10', output: '0.60', cache: '0.01'};
  const total = {...zero, requests: 13, success: 11, failed: 2, input_tokens: 8100, output_tokens: 1400,
    cache_tokens: 2100, reasoning_tokens: 700, unknown: 2, cost: '0.001461'};
  const channels = [
    {...total, config_id: 31, name: '巧克力 · NPC', model: 'gemini-3.1-flash-lite', current: true, enabled: true,
      price, rules: [{model: 'gemini-3.1-flash-lite', ...price}]},
    {...zero, config_id: 28, name: '沉默', model: '[anti量]gemini-3.8-flash', current: true, enabled: true,
      price: {mode: 'tokens', input: '0.225', output: '1.125', cache: '0.0225'}, rules: []},
    {...zero, requests: 1, unknown: 1, failed: 1, config_id: 11, name: '小鸡农场', model: 'new-model-without-price',
      current: true, enabled: true, price: null, rules: []},
  ];
  const data = {range: 'today', since: 1792800000, total,
    purposes: [{...zero, purpose: 'soup', label: '海龟汤'}, {...total, purpose: 'npc', label: 'NPC'}, {...zero, purpose: 'tarot', label: '塔罗'}], channels};
  const state = {data, requests: [], fail: false, failSave: false};
  state.fetch = async (url, options = {}) => {
    state.requests.push({url, options});
    let status = 200, body = {};
    if (url.startsWith('/api/admin/api-costs/prices')) {
      status = state.failSave ? 400 : 200;
      body = state.failSave ? {error: '价格校验失败'} : {ok: true};
    } else if (url.startsWith('/api/admin/api-costs')) {
      status = state.fail ? 503 : 200;
      body = state.fail ? {error: '费用统计暂时不可用'} : state.data;
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
  const settle = () => new Promise(resolve => setTimeout(resolve, 5));
  try {
    await settle();
    assert.equal(d.querySelectorAll('.admin-tabs [role="tab"]').length, 4);
    assert.equal($('costsTab').getAttribute('aria-selected'), 'true');
    assert.equal($('dashboardPanel').hidden, true);
    assert.equal($('costContent').hidden, false);
    assert.equal($('costTotal').textContent, '¥0.001461');
    assert.equal(w.money('0.0000000000001'), '¥0.0000000000001');
    assert.equal(w.money('0.001000'), '¥0.001');
    assert.equal(d.querySelectorAll('.cost-editor[open]').length, 0);
    assert.match($('costChannels').textContent, /待定价/);
    assert.equal(d.querySelector('[data-cost-index="2"] dd').textContent, '待核算');
    assert.equal(w.knownCost({...zero, requests: 3, unknown: 3}), '待核算');
    for (const range of ['7d', '30d', 'today']) {
      $('costRange').value = range;
      $('costRange').dispatchEvent(new w.Event('change')); await settle();
      assert.ok(state.requests.some(r => r.url.endsWith('range=' + range)));
    }
    let form = d.querySelector('[data-cost-form="0"]');
    assert.equal(form.elements.per_request.disabled, true);
    assert.equal(form.elements.input.value, '0.10');
    form.elements.model.value = 'a-new-exact-model';
    form.elements.model.dispatchEvent(new w.Event('change', {bubbles: true}));
    assert.equal(form.elements.input.value, '');
    form.elements.mode.value = 'request';
    form.elements.mode.dispatchEvent(new w.Event('change', {bubbles: true}));
    assert.equal(form.elements.input.disabled, true);
    assert.equal(form.elements.per_request.disabled, false);
    form.elements.per_request.value = '0.000001';
    state.failSave = true;
    form.dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true})); await settle();
    assert.match(form.querySelector('.status').textContent, /价格校验失败/);
    assert.equal(form.elements.model.value, 'a-new-exact-model');
    assert.equal(form.querySelector('button').disabled, false);
    state.failSave = false;
    form.dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true})); await settle();
    assert.match($('costStatus').textContent, /价格已保存/);
    const saved = state.requests.filter(r => r.url.endsWith('/prices')).at(-1);
    assert.deepEqual(JSON.parse(saved.options.body), {config_id: 31, model: 'a-new-exact-model', mode: 'request', per_request: '0.000001'});
    assert.equal(d.activeElement, $('costRefresh'));
    state.fail = true; await w.loadCosts();
    assert.equal($('costContent').hidden, true);
    assert.match($('costStatus').textContent, /刷新重试/);
    state.fail = false;
    state.data.total = {...zero};
    state.data.channels[0].name = '<img src=x onerror="window.bad=1">';
    state.data.channels[0].model = '" autofocus onfocus="window.bad=1';
    await w.loadCosts();
    assert.match($('costChannels').textContent, /暂无调用/);
    assert.equal($('costChannels').querySelectorAll('img,script').length, 0);
    assert.equal(w.bad, undefined);
    state.data.channels = []; await w.loadCosts();
    assert.match($('costChannels').textContent, /暂无渠道/);
    // A slow old response must not replace the newly selected range.
    let release;
    w.fetch = () => new Promise(resolve => { release = resolve; });
    const old = w.loadCosts();
    w.fetch = state.fetch; await w.loadCosts();
    release({ok: true, json: async () => fixture().data}); await old;
    assert.match($('costChannels').textContent, /暂无渠道/);
    assert.deepEqual(errors, []);
    console.log('PASS DOM: fourth tab, ranges, exact-model editing, small fees, save/error/retry/empty states, escaping, stale responses. Rendered layout NOT verified.');
  } finally { w.close(); }
}
async function checkBrowser() {
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'], executablePath: process.env.CHROMIUM_PATH});
  const out = process.env.SCREENSHOT_DIR || '/tmp/cedartoy-api-costs-screenshots';
  fs.mkdirSync(out, {recursive: true});
  try {
    const state = fixture();
    const context = await browser.newContext();
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
    for (const width of [360, 430, 1280]) {
      await page.setViewportSize({width, height: 900});
      await page.goto('http://costs.test/admin#costs');
      await page.locator('#costContent').waitFor({state: 'visible'});
      await page.locator('#costsTab').scrollIntoViewIfNeeded();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const nav = await page.locator('.admin-tabs').evaluate(el => ({scroll: el.scrollWidth, width: el.clientWidth, overflow: getComputedStyle(el).overflowX}));
      assert.equal(nav.overflow, 'auto');
      if (width === 360) assert.ok(nav.scroll > nav.width);
      await page.screenshot({path: path.join(out, `api-costs-${width}.png`), fullPage: true});
      const summary = page.locator('.cost-editor summary').first();
      await summary.focus(); await page.keyboard.press('Enter');
      await page.locator('.cost-form').first().waitFor({state: 'visible'});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path: path.join(out, `api-costs-editor-${width}.png`), fullPage: true});
    }
    state.fail = true; await page.locator('#costRefresh').click();
    await page.getByText('费用统计暂时不可用，可点击刷新重试。', {exact: true}).waitFor();
    await page.screenshot({path: path.join(out, 'api-costs-error.png'), fullPage: true});
    assert.deepEqual(errors, []);
    console.log('PASS browser: 360/430/1280, nav-only overflow, editor keyboard, errors; screenshots:', out);
  } finally { await browser.close(); }
}
(process.argv.includes('--browser') ? checkBrowser() : checkDOM()).catch(e => {console.error(e); process.exitCode = 1;});
