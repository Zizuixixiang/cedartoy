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
      const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: width < 600, reducedMotion: 'reduce' });
      let configs = structuredClone(fixtures), denied = false, loadFail = false, testFail = false, saveFail = false, delay = 0;
      const writes = [], errors = [], unexpected = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => {
        localStorage.setItem('cedartoy_user_id', '201');
        localStorage.setItem('cedartoy_token', 'platform-fixture');
      });
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
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
          } else if (/^\/admin\/api-configs(?:\/\d+)?\/test$/.test(endpoint)) {
            writes.push({ endpoint, method, body: req.postDataJSON(), auth: req.headers().authorization });
            await new Promise(r => setTimeout(r, 80));
            json = testFail
              ? { success: false, message: 'fake-secret-from-provider', data: { config_name: '测试渠道', http_status: 429, llm_ms: 42, raw: { api_key: 'fake-secret-from-provider', authorization: 'Bearer abcdef' } } }
              : { success: true, data: { config_name: '测试渠道', model: 'fixture-model', reply: '连接成功 sk-fake-secret', http_status: 200, llm_ms: 42, api_key: 'hidden-response-key' } };
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
      assert.equal(await page.locator('.api-routing-notes').getAttribute('open'), null);
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
        assert.equal(await page.getByLabel('用途', { exact: true }).inputValue(), purpose);
        assert.deepEqual(await page.getByLabel('用途', { exact: true }).locator('option').evaluateAll(options => options.map(o => o.value)), ['judge', 'hint', 'both', 'npc_decision', 'npc_speech', 'npc', 'all', 'tarot']);
        await page.keyboard.press('Escape');
      }
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
      assert.match(await row(names[0]).innerText(), /优先级 4[\s\S]*已停用/);
      await row(names[6]).getByRole('button', { name: '测试', exact: true }).click();
      await page.getByText('测试成功', { exact: true }).waitFor();
      assert.match(await page.locator('.cfg-test-result').innerText(), /HTTP：200[\s\S]*耗时：42ms[\s\S]*连接成功/);
      assert.doesNotMatch(await page.locator('.cfg-test-result').innerText(), /sk-fake-secret|hidden-response-key/);
      assert.equal(writes.at(-1).endpoint, '/admin/api-configs/7/test');
      testFail = true;
      await row(names[6]).getByRole('button', { name: '编辑', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: '测试', exact: true }).click();
      await page.getByRole('dialog').getByText('测试失败', { exact: true }).waitFor();
      assert.match(await page.getByRole('dialog').innerText(), /HTTP：429/);
      assert.doesNotMatch(await page.getByRole('dialog').innerText(), /fake-secret-from-provider|abcdef/);
      assert.equal(writes.at(-1).body.config_id, 7);
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
      assert.match(await page.locator('.api-config-tools').innerText(), /上次记录/);
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
    console.log(baseline ? 'Baseline screenshots recorded.' : 'PASS: groups/legacy/shared, search/filter, view/edit/add/delete/test, payloads, masked results, state/priority, empty/error/auth, 360/430/1280 reflow. All network mocked.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
