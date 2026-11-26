/* Offline browser fixtures only: all requests intercepted, no model/backend calls.
 * Build to /tmp first; set SOUP_UI_BUILD and PLAYWRIGHT_MODULE when needed.
 * SOUP_UI_BASELINE=1 records the old layout using the same fixtures.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const build = process.env.SOUP_UI_BUILD || '/tmp/cedar-admin-api-review/build';
const output = process.env.SOUP_UI_SCREENSHOTS || '/tmp/cedar-admin-api-review/screenshots';
const baseline = process.env.SOUP_UI_BASELINE === '1';
const player = { id: 7, user_id: 201, username: '南杉', is_guest: false, is_admin: true };
const purposes = ['both', 'judge', 'hint', 'npc', 'npc_decision', 'npc_speech', 'tarot', 'all', 'future'];
const names = ['日常裁判渠道', '备用裁判', '提示渠道', '旧版备用渠道', '双弈决策', '双弈发言', '星光解读', '跨游戏共用渠道', '待核实渠道'];
const fixtures = purposes.map((purpose, i) => ({
  id: i + 1, purpose, name: names[i], priority: i === 1 ? 9 : i === 2 ? 0 : 1, enabled: i === 2 ? 0 : 1,
  api_url: 'https://example.invalid/v1', api_key: 'sk-f...MASK', model: 'fixture-real-model-' + i,
  created_at: '2026-10-14 12:00:00', runtime_status: i === 1 ? 'cooling' : i === 3 ? 'half_open' : i === 8 ? null : 'healthy',
  consecutive_failures: i === 1 ? 3 : 0, cooldown_remaining_seconds: i === 1 ? 45 : 0,
}));
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const measurements = [];
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: width === 360 ? 740 : 900 }, hasTouch: width < 600, reducedMotion: 'reduce' });
      let configs = structuredClone(fixtures), denied = false, loadFail = false, testOutcome = 'success', saveFail = false, delay = 0;
      const writes = [], errors = [], unexpected = [];
      let conflict = true, quoteError = 0, previewError = false;
      const quoteSaves = new Map();
      const previewReads = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => {
        localStorage.setItem('cedartoy_user_id', '201');
        localStorage.setItem('cedartoy_token', 'platform-fixture');
      });
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        // The existing page links the lobby font; admin uses local system text.
        if (url.host === 'fonts.googleapis.com' && req.resourceType() === 'stylesheet') {
          return route.fulfill({ contentType: 'text/css', body: '' });
        }
        if (url.host !== 'soup.test') { unexpected.push(url.origin); return route.abort(); }
        if (url.pathname.startsWith('/soup/api/')) {
          const endpoint = url.pathname.slice('/soup/api'.length), method = req.method();
          let json = {}, status = 200;
          if (endpoint === '/auth/guest') json = { token: 'soup-fixture', player: { ...player, is_admin: !denied } };
          else if (endpoint === '/auth/me') json = { player: { ...player, is_admin: !denied } };
          else if (endpoint === '/admin/overview') json = { puzzles: 12, rooms: 2 };
          else if (endpoint === '/admin/api-configs' && method === 'GET') {
            if (delay) await new Promise(r => setTimeout(r, delay));
            if (loadFail) { status = 503; json = { detail: '读取失败，请稍后刷新' }; }
            else json = configs;
          } else if (/^\/admin\/api-pools\/(soup|npc|tarot)\/configs\/\d+\/price$/.test(endpoint)) {
            const id = Number(endpoint.split('/')[5]);
            const body = req.postDataJSON();
            assert.equal(method, 'PUT');
            assert.equal(body.model, configs[id - 1].model);
            assert.deepEqual(body.config_ids, id === 8 ? [8, 10] : [id]);
            assert.equal(body.group_revision, 'c'.repeat(64));
            assert.deepEqual(body.price_versions, id === 8 ? { 8: 4, 10: 3 } : { [id]: 0 });
            writes.push({ endpoint, method, body, auth: req.headers().authorization });
            if (quoteError) {
              status = quoteError;
              json = { detail: status === 409 ? '价格已变化，请刷新查看最新价格后再保存' : '报价暂时无法保存，请稍后重试' };
            } else quoteSaves.set(id, body);
          } else if (/^\/admin\/api-pools\/(soup|npc|tarot)\/price-order$/.test(endpoint)) {
            const pool = endpoint.split('/')[3];
            const direction = url.searchParams.get('direction');
            const cutoff = 1795226400, revision = 'a'.repeat(64);
            if (method === 'GET') {
              previewReads.push(pool);
              const changed = pool === 'soup' && (quoteSaves.has(2) || direction === 'desc');
              const ordered = pool === 'tarot' ? [6] : pool === 'npc' ? [4, 7] : direction === 'asc' ? (quoteSaves.has(2) ? [1, 7, 0, 2] : [7, 0, 1, 2]) : [0, 7, 1, 2];
              const prices = { 0: '0.012', 7: '0.0015' };
              json = { direction, revision, changed,
                usage: { count: 200, minimum: 10, limit: 200, days: 30, cutoff, means: ['1200', '300', '400'] },
                rows: ordered.map((index, i) => {
                  const cfg = configs[index], saved = quoteSaves.get(cfg.id);
                  const rule = saved || (index === 7 ? { mode: 'tokens', input: '1.20', output: '2.0', cache: '0.01', reference_only: true }
                    : prices[index] ? { mode: 'request', per_request: prices[index] } : null);
                  const cost = saved ? (saved.mode === 'request' ? saved.per_request : '0.0024') : prices[index] ?? null;
                  return { id: cfg.id, name: cfg.name, enabled: cfg.enabled, model: cfg.model, purpose: cfg.purpose,
                    before: pool === 'soup' && index === 0 ? 2 : cfg.priority,
                    after: pool === 'soup' && index !== 2 ? [1, 2, 9][i] : cfg.priority, cost,
                    group_revision: 'c'.repeat(64), versions: index === 7 ? { 8: saved ? 5 : 4, 10: saved ? 6 : 3 } : { [cfg.id]: saved ? 1 : 0 },
                    quotes: [{ id: cfg.id, name: cfg.name, purpose: cfg.purpose, rule, source: rule ? 'user' : null },
                      ...(index === 7 ? [{ id: 10, name: names[7] + ' · 塔罗', purpose: 'tarot', rule, source: 'user' }] : [])],
                    source: rule ? 'user' : null, mode: rule?.mode, reason: cost === null ? '暂无该渠道与模型的报价' : null };
                }) };
              if (previewError) { status = 503; json = { detail: '暂时无法读取排序，请稍后重新预览' }; }
            } else {
              assert.equal(method, 'PUT');
              assert.deepEqual(req.postDataJSON(), { direction: 'desc', cutoff, revision });
              writes.push({ endpoint, method, auth: req.headers().authorization });
              if (conflict) { status = 409; json = { detail: '价格已变化，请重新预览' }; conflict = false; }
              else configs[7].soup_priority = 2;
            }
          } else if (/^\/admin\/api-configs(?:\/\d+)?\/test$/.test(endpoint)) {
            writes.push({ endpoint, method, body: req.postDataJSON(), auth: req.headers().authorization });
            await new Promise(r => setTimeout(r, 80));
            json = testOutcome !== 'success'
              ? { success: false, message: 'fake-secret-from-provider', data: { http_status: 429, llm_ms: 42, raw: { authorization: 'Bearer abcdef' } } }
              : { success: true, data: { reply: '连接成功 sk-fake-secret', http_status: 200, llm_ms: 42, api_key: 'hidden-response-key' } };
            if (testOutcome === 'error') json.data.error_detail = '类型 rate_limit_error · 错误码 rate_limit_exceeded · 上游限制了请求频率';
          } else if (/^\/admin\/api-configs(?:\/\d+)?$/.test(endpoint) && method !== 'GET') {
            const body = req.postData() ? req.postDataJSON() : null;
            writes.push({ endpoint, method, body, auth: req.headers().authorization });
            if (saveFail) { status = 400; json = { detail: '用途配置不完整，请检查后重试' }; }
            else if (method === 'PUT') configs = configs.map(row => row.id === Number(endpoint.split('/').at(-1)) ? { ...row, ...body, api_key: row.api_key } : row);
            else if (method === 'DELETE') configs = configs.filter(row => row.id !== Number(endpoint.split('/').at(-1)));
            else if (method === 'POST') configs.push({ ...body, id: 90, api_key: '****' });
          } else unexpected.push(`${method} ${endpoint}`);
          return route.fulfill({ status, json });
        }
        const relative = url.pathname.startsWith('/soup/assets/') ? url.pathname.slice('/soup/'.length) : 'index.html';
        return route.fulfill({ body: fs.readFileSync(path.join(build, relative)), contentType: relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html' });
      });
      const search = () => page.locator('.admin-search');
      const group = id => page.locator('.api-config-group').filter({ has: page.locator(`#api-group-${id}`) });
      const row = name => page.locator('.api-config-row').filter({ has: page.getByText(name, { exact: true }) });
      const checkOverflow = async () => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `no overflow at ${width}`);
      await page.goto('http://soup.test/soup/admin');
      await page.getByRole('button', { name: '模型 API', exact: true }).click();
      await page.getByText(names[0], { exact: true }).waitFor();
      measurements.push(await page.evaluate(() => {
        const row = document.querySelector('.api-config-row') || document.querySelector('.admin-data-table tbody tr');
        const button = row.querySelector('button'), text = row.querySelector('strong') || row.querySelector('td');
        return { width: innerWidth, rowHeight: Math.round(row.getBoundingClientRect().height), buttonHeight: button.getBoundingClientRect().height,
          font: getComputedStyle(text).fontSize, fontFamily: getComputedStyle(text).fontFamily, lineHeight: getComputedStyle(text).lineHeight };
      }));
      await page.screenshot({ path: path.join(output, `${baseline ? 'before' : 'after'}-${width}.png`), fullPage: true });
      if (baseline) { await page.close(); continue; }
      await checkOverflow();
      assert.equal(await group('soup').locator('li').count(), 3);
      assert.equal(await group('npc').locator('li').count(), 3);
      assert.equal(await group('tarot').locator('li').count(), 1);
      assert.equal(await group('shared').locator('li').count(), 1);
      assert.equal(await row(names[7]).count(), 1, 'shared configuration is not duplicated');
      assert.equal(await group('other').locator('li').count(), 1);
      assert.match(await row(names[3]).innerText(), /旧版备用/);
      assert.match(await row(names[1]).innerText(), /优先级 9[\s\S]*冷却中 · 剩余 45 秒/);
      assert.match(await row(names[2]).innerText(), /已停用 · 不参与调用/);
      assert.match(await row(names[8]).innerText(), /状态未知/);
      assert.deepEqual(await group('soup').locator('.api-config-name strong').allTextContents(), [names[2], names[0], names[1]]);
      assert.equal(await page.locator('.api-routing-notes').count(), 0);
      assert.doesNotMatch(await page.locator('.api-config-groups').innerText(), /example\.invalid|fixture-real-model|MASK|2026-10-14/);
      for (const [needle, count] of [['海龟汤', 4], ['both', 1], ['npc', 3], ['tarot', 1], ['all', 1], ['fixture-real-model-6', 1], ['已停用', 1], ['不存在', 0]]) {
        await search().fill(needle);
        await page.waitForTimeout(30);
        assert.equal(await page.locator('.api-config-row').count(), count, `search ${needle}`);
      }
      assert.match(await page.locator('.api-config-groups').innerText(), /没有匹配的渠道/);
      await search().fill('');
      for (const [filter, purpose, count] of [['soup', 'both', 4], ['npc', 'npc_decision', 4], ['tarot', 'tarot', 1], ['shared', 'all', 1], ['all-groups', 'both', 9]]) {
        await page.getByLabel('用途分组').selectOption(filter);
        assert.equal(await page.locator('.api-config-row').count(), count);
        await page.getByRole('button', { name: '新增', exact: true }).click();
        assert.equal(await page.getByRole('combobox', { name: '用途', exact: true }).inputValue(), purpose);
        assert.deepEqual(await page.getByRole('combobox', { name: '用途', exact: true }).locator('option').evaluateAll(options => options.map(o => o.value)), ['judge', 'hint', 'both', 'npc_decision', 'npc_speech', 'npc', 'all', 'tarot']);
        await page.keyboard.press('Escape');
      }
      const sort = group('soup').getByRole('button', { name: '按价格排序' });
      assert.equal(await page.getByRole('button', { name: '按价格排序' }).count(), 3);
      await sort.focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog');
      await dialog.getByText('¥0.0015 / 次').waitFor();
      assert.equal(await dialog.locator('.price-preview-row').count(), 4);
      await checkOverflow();
      await page.screenshot({ path: path.join(output, `preview-${width}.png`) });
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'hidden' });
      assert.ok(await sort.evaluate(el => document.activeElement === el));
      assert.equal(writes.length, 0, 'preview and cancellation are read-only');
      for (const [pool, index] of [['npc', 7], ['tarot', 6], ['soup', 1]]) {
        await group(pool).getByRole('button', { name: '按价格排序' }).click();
        const edit = dialog.locator(`[data-edit-quote="${index + 1}"]`);
        if (pool === 'soup') assert.ok(await dialog.getByRole('button', { name: '确认应用到本池' }).isDisabled());
        await edit.click();
        const mode = dialog.getByLabel('计费模式');
        const save = dialog.getByRole('button', { name: '保存报价' });
        assert.equal(await dialog.getByRole('button', { name: '确认应用到本池' }).count(), 0);
        if (pool === 'npc') {
          assert.equal(await dialog.getByLabel('输入（元/百万 Token）').inputValue(), '1.20');
          assert.ok(await dialog.getByLabel('仅作参考估算').isChecked());
          await dialog.getByText('同时更新此模型报价').waitFor();
          await dialog.getByText(/跨游戏共用渠道 · 塔罗/).waitFor();
          await page.keyboard.press('Escape');
          assert.ok(await edit.evaluate(el => document.activeElement === el));
          await edit.click();
          await dialog.getByLabel('缓存命中（元/百万 Token）').fill('0.02');
          assert.ok(await dialog.locator('.price-preview-content').evaluate(el => el.scrollWidth <= el.clientWidth));
          assert.ok(await edit.count() === 0);
          await page.screenshot({ path: path.join(output, `quote-token-${width}.png`) });
        } else {
          const price = dialog.getByLabel('单价（元/次）');
          assert.equal(await price.inputValue(), '');
          await price.fill('0');
          if (pool === 'soup') {
            quoteError = 409;
            await save.click();
            await dialog.getByText('价格已变化，请刷新查看最新价格后再保存').waitFor();
            assert.equal(await price.inputValue(), '0');
            await page.screenshot({ path: path.join(output, `quote-conflict-${width}.png`) });
            await dialog.getByRole('button', { name: '刷新预览' }).click();
            await edit.click();
            await price.fill('0');
            quoteError = 503;
            await save.click();
            await dialog.getByText('报价暂时无法保存，请稍后重试').waitFor();
            assert.equal(await price.inputValue(), '0');
            quoteError = 0;
          }
        }
        assert.ok(await mode.isVisible());
        const count = previewReads.length;
        previewError = pool === 'tarot';
        await save.click();
        await dialog.getByText('报价已保存。').waitFor();
        if (previewError) {
          await dialog.getByRole('alert').waitFor();
          previewError = false;
          await dialog.getByRole('button', { name: '重新预览' }).click();
        }
        await edit.waitFor();
        assert.ok(previewReads.length > count && previewReads.slice(count).every(p => p === pool));
        assert.equal(await dialog.getByRole('button', { name: '确认应用到本池' }).isEnabled(), pool === 'soup');
        const expectedCost = pool === 'npc' ? '¥0.0024 / 次' : '¥0 / 次';
        await edit.locator('..').getByText(expectedCost, { exact: true }).waitFor();
        await checkOverflow();
        assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
        if (pool === 'soup') await page.screenshot({ path: path.join(output, `quote-refreshed-${width}.png`) });
        await dialog.getByRole('button', { name: '关闭' }).click();
      }
      assert.ok(writes.every(w => w.endpoint.endsWith('/price')));
      assert.equal(quoteSaves.get(8).cache, '0.02');
      assert.equal(quoteSaves.get(8).reference_only, true);
      await sort.click();
      await dialog.getByLabel('预计单次成本').selectOption('desc');
      await dialog.getByRole('button', { name: '确认应用到本池' }).click();
      await dialog.getByRole('alert').waitFor();
      await dialog.getByRole('button', { name: '重新预览' }).click();
      await dialog.getByRole('button', { name: '确认应用到本池' }).click();
      await page.getByText('海龟汤的调用顺序已保存。').waitFor();
      await row(names[7]).locator('.api-config-priority').getByText('2', { exact: true }).waitFor();
      assert.match(await row(names[7]).innerText(), /海龟汤 2[\s\S]*NPC 1/);
      // View keeps masked details; edit and save preserve actual purpose and serialization.
      await row(names[0]).getByRole('button', { name: '查看', exact: true }).click();
      assert.match(await page.getByRole('dialog').innerText(), /https:\/\/example.invalid\/v1[\s\S]*sk-f\.\.\.MASK[\s\S]*fixture-real-model-0/);
      await checkOverflow();
      await page.keyboard.press('Escape');
      assert.equal(await row(names[0]).getByRole('button', { name: '查看', exact: true }).evaluate(el => el === document.activeElement), true);
      await row(names[0]).getByRole('button', { name: '编辑', exact: true }).click();
      assert.equal(await page.getByLabel('密钥', { exact: true }).inputValue(), '');
      assert.equal(await page.getByLabel('密钥', { exact: true }).getAttribute('type'), 'password');
      await page.getByLabel('优先级', { exact: true }).fill('4');
      await page.getByLabel('启用', { exact: true }).uncheck();
      await checkOverflow();
      await page.screenshot({ path: path.join(output, `edit-${width}.png`), fullPage: true });
      saveFail = true;
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.getByText('用途配置不完整，请检查后重试').waitFor();
      assert.equal(await page.getByLabel('优先级', { exact: true }).inputValue(), '4');
      saveFail = false;
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      assert.deepEqual(writes.at(-1).body, { name: names[0], api_url: 'https://example.invalid/v1', api_key: '', model: 'fixture-real-model-0', purpose: 'both', enabled: 0, priority: 4 });
      await row(names[0]).getByText('已停用 · 不参与调用', { exact: true }).waitFor();
      assert.match(await row(names[0]).innerText(), /优先级 4[\s\S]*已停用/);
      await row(names[6]).getByRole('button', { name: '测试', exact: true }).click();
      await page.getByText('测试成功', { exact: true }).waitFor();
      assert.match(await page.locator('.cfg-test-result').innerText(), /HTTP：200[\s\S]*耗时：42ms[\s\S]*连接成功/);
      assert.doesNotMatch(await page.locator('.cfg-test-result').innerText(), /sk-fake-secret|hidden-response-key/);
      assert.equal(writes.at(-1).endpoint, '/admin/api-configs/7/test');
      testOutcome = 'error';
      await row(names[6]).getByRole('button', { name: '测试', exact: true }).click();
      await page.locator('.cfg-test-result').getByText(/上游限制了请求频率/).waitFor();
      assert.doesNotMatch(await page.locator('.cfg-test-result').innerText(), /fake-secret-from-provider|abcdef/);
      await row(names[6]).getByRole('button', { name: '编辑', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: '测试', exact: true }).click();
      await page.getByRole('dialog').getByText('测试失败', { exact: true }).waitFor();
      assert.match(await page.getByRole('dialog').innerText(), /HTTP：429[\s\S]*类型 rate_limit_error · 错误码 rate_limit_exceeded · 上游限制了请求频率/);
      assert.doesNotMatch(await page.getByRole('dialog').innerText(), /fake-secret-from-provider|abcdef/);
      assert.equal(writes.at(-1).body.config_id, 7);
      await checkOverflow();
      await page.screenshot({ path: path.join(output, `test-error-${width}.png`) });
      testOutcome = 'legacy';
      await page.getByRole('dialog').getByRole('button', { name: '测试', exact: true }).click();
      await page.getByRole('dialog').getByText(/请求未成功，请检查/).waitFor();
      assert.doesNotMatch(await page.getByRole('dialog').innerText(), /fake-secret-from-provider|abcdef/);
      await page.keyboard.press('Escape');
      // Add uses selected group's purpose; delete remains confirmed.
      await page.getByLabel('用途分组').selectOption('tarot');
      await page.getByRole('button', { name: '新增', exact: true }).click();
      await page.getByLabel('名称', { exact: true }).fill('新解读渠道');
      await page.getByLabel('接口地址', { exact: true }).fill('https://example.invalid/v1');
      await page.getByLabel('模型', { exact: true }).fill('fixture-new');
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await row('新解读渠道').waitFor();
      assert.equal(writes.at(-1).body.purpose, 'tarot');
      const writeCount = writes.length;
      page.once('dialog', dialog => dialog.dismiss());
      await row('新解读渠道').getByRole('button', { name: '删除', exact: true }).click();
      assert.equal(writes.length, writeCount);
      page.once('dialog', dialog => dialog.accept());
      await row('新解读渠道').getByRole('button', { name: '删除', exact: true }).click();
      await row('新解读渠道').waitFor({ state: 'hidden' });
      assert.equal(writes.at(-1).method, 'DELETE');
      // Refresh failure explicitly labels stale records, then empty state and auth revocation.
      loadFail = true;
      await page.getByRole('button', { name: '刷新', exact: true }).click();
      await page.getByRole('alert').waitFor();
      assert.equal(await page.locator('.api-routing-notes').count(), 0);
      loadFail = false; configs = []; delay = 150;
      await page.getByRole('button', { name: '刷新', exact: true }).click();
      await page.getByText('暂无配置。可选择此用途分组后新增。', { exact: true }).waitFor();
      assert.ok(writes.every(write => write.auth === 'Bearer soup-fixture'));
      denied = true;
      await page.getByRole('button', { name: '刷新', exact: true }).click();
      await page.getByRole('heading', { name: '当前统一账号没有管理员权限' }).waitFor();
      assert.equal(await page.locator('.api-config-row, .admin-toolbar').count(), 0);
      assert.deepEqual(errors, []);
      assert.deepEqual(unexpected, []);
      await page.close();
    }
    console.log(JSON.stringify(measurements, null, 2));
    console.log(baseline ? 'Baseline screenshots recorded.' : 'PASS: quote edit/cancel, token/cache/reference, zero, shared scope, conflict/503/retry, independent pool refresh, groups/legacy/shared, search/filter, view/edit/add/delete/test, payloads, masked results, state/priority, empty/error/auth, 360/430/1280 reflow. All network mocked.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
