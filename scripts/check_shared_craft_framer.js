/* Real Chromium + original UI + real ASGI handlers; bridge owns temporary stores. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {bridge} = require('./shared_craft_test_bridge');

(async () => {
  const api = bridge();
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-craft-framer-'));
  let browser;
  const report = [];
  const json = async (url, method = 'GET', body) => {
    const r = await api.request(url, method, body);
    assert.equal(r.status, 200, Buffer.from(r.body, 'base64').toString().slice(0, 500));
    assert.match(r.contentType, /application\/json/);
    return JSON.parse(Buffer.from(r.body, 'base64'));
  };
  try {
    await api.ready;
    browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      page.setDefaultTimeout(15000);
      const errors = [], posts = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        try {
          const req = route.request(), url = new URL(req.url());
          if (url.hostname !== 'craft.test') return route.abort();
          // An unadapted /api/beads/... must fail, as at the platform HTML fallback.
          if (!url.pathname.startsWith('/shared-craft/')) {
            errors.push(`Unexpected URL: ${url.pathname}`);
            return route.fulfill({contentType: 'text/html', body: '<html>Not found</html>'});
          }
          const body = req.postData() ? JSON.parse(req.postData()) : undefined;
          const result = await api.request(url.pathname.slice('/shared-craft/'.length), req.method(), body);
          if (req.method() === 'POST') posts.push({path: url.pathname, body, result});
          return route.fulfill({status: result.status, contentType: result.contentType,
            body: Buffer.from(result.body, 'base64')});
        } catch (error) {
          errors.push(error.message);
          await route.abort();
        }
      });
      const board = (await json('api/bead-boards', 'POST',
        {title: `装裱 ${width}`, mode: 'together', size: 8})).board;
      await json(`api/bead-boards/${board.id}`, 'POST',
        {action: 'place', beads: [0, 1, 2].map(x => ({x, y: 0, color: 'F14'}))});
      const boardURL = `https://craft.test/shared-craft/beads/#${board.id}`;
      await page.goto(boardURL);
      await page.locator('#cover').click();
      await page.locator('#heat').click();
      await page.waitForFunction(() => {
        const b = document.querySelector('#press-iron');
        return !b.hidden && !b.disabled;
      });
      const iron = await page.locator('#press-iron').boundingBox();
      await page.mouse.move(iron.x + iron.width / 2, iron.y + iron.height / 2);
      await page.mouse.down();
      await page.waitForFunction(() => document.querySelector('#studio').dataset.stage === 'ironing');
      await page.waitForTimeout(3500); // Upstream's real ironing duration, no engine/state stubs.
      await page.mouse.up();
      await page.waitForFunction(() => {
        const b = document.querySelector('#lift');
        return !b.hidden && !b.disabled;
      });
      await page.locator('#lift').click();
      await page.locator('#save').click();
      await page.locator('#framer').waitFor();
      await page.locator('[data-bg="grid"]').click();
      await page.locator('[data-framer-tab="sticker"]').click();
      await page.locator('[data-sticker="🎀"]').click();
      await page.locator('[data-framer-tab="layer"]').click();
      await page.locator('#framer-outline').click();
      await page.locator('#framer-shadow').click();
      await page.locator('#framer-shell').click();
      const downloadPromise = page.waitForEvent('download');
      await page.locator('#framer-export').click();
      const download = await downloadPromise;
      assert.match(download.suggestedFilename(), /-袋装成品\.png$/);
      await page.waitForFunction(() => document.querySelector('#framer-hint').textContent.includes('已保存到后端'));
      const save = posts.find(p => p.body?.action === 'decoration');
      assert(save, 'original save button must POST decoration');
      assert.equal(save.path, `/shared-craft/api/bead-boards/${board.id}`);
      assert.equal(save.result.status, 200);
      assert.match(save.result.contentType, /application\/json/);
      assert.equal(JSON.parse(Buffer.from(save.result.body, 'base64')).ok, true);
      assert.equal(save.body.has_pouch, true);
      const images = [save.body.data_url, save.body.piece_data_url].map(url => {
        assert.match(url, /^data:image\/png;base64,/);
        const png = Buffer.from(url.split(',')[1], 'base64');
        assert.equal(png.subarray(1, 4).toString(), 'PNG');
        return png;
      });
      assert(!images[0].equals(images[1]), 'pouch image and bare piece must stay distinct');
      assert.deepEqual(fs.readFileSync(await download.path()), images[0]);
      const draftKey = `bead-framer-v1:${board.id}`;
      const draft = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), draftKey);
      assert.equal(draft.bg, 'grid');
      assert(draft.objects.some(o => o.kind === 'emoji' && o.value === '🎀'));
      assert(draft.objects.some(o => o.kind === 'piece' && o.shell && o.outline && o.shadow));
      await page.screenshot({path: path.join(output, `${width}-saved.png`), fullPage: true});

      await page.reload();
      await page.locator('#save').click();
      await page.locator('#framer-gallery').waitFor();
      assert.deepEqual(await page.evaluate(key => JSON.parse(localStorage.getItem(key)), draftKey), draft);
      for (const selector of ['#framer-shell', '#framer-outline', '#framer-shadow'])
        assert(await page.locator(selector).evaluate(e => e.classList.contains('active')));
      const state = (await json(`api/bead-boards/${board.id}`)).board;
      assert.equal(state.craft.stage, 'finished');
      assert.equal(state.craft.decorated, true);
      assert.equal(state.craft.has_pouch, true);
      await page.locator('#framer-gallery').click();
      await page.waitForURL('**/shared-craft/crafts/?v=5');
      const collect = posts.find(p => p.path === '/shared-craft/api/gallery');
      assert(collect, 'original gallery button must POST gallery');
      assert.equal(collect.result.status, 200);
      assert.match(collect.result.contentType, /application\/json/);
      const saved = JSON.parse(Buffer.from(collect.result.body, 'base64'));
      assert.equal(saved.ok, true);
      assert.equal(saved.item.source_id, board.id);
      assert.equal(saved.item.has_pouch, true);
      assert.equal(saved.item.presentation, 'decorated');
      assert.equal(collect.body.piece_columns, 3);
      assert.equal(collect.body.piece_rows, 1);
      await page.reload();
      await page.locator('#open-all').click();
      await page.getByText(board.title, {exact: true}).first().waitFor();
      assert((await json('api/gallery')).items.some(item => item.id === saved.item.id));
      for (const [index, suffix] of ['.png', '/display.png'].entries()) {
        const image = await api.request(`api/gallery/${saved.item.id}${suffix}`);
        assert.equal(image.status, 200);
        assert.match(image.contentType, /image\/png/);
        assert.deepEqual(Buffer.from(image.body, 'base64'), images[index]);
      }
      await page.screenshot({path: path.join(output, `${width}-gallery.png`), fullPage: true});
      assert.deepEqual(errors, []);
      report.push({width, saveJSON: true, galleryJSON: true, downloadMatches: true,
        draftRestored: true, imagesPersisted: true, errors});
      await page.close();
    }
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(`PASS: 360/430/1280 ironing, decoration, download, gallery and reload; ${output}`);
  } finally {
    await browser?.close();
    await api.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
