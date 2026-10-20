/* Real Chromium + original UI + real ASGI handlers; bridge owns temporary stores. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {createHash} = require('node:crypto');
const browserName = process.env.SHARED_CRAFT_BROWSER || 'chromium';
const browserType = require(process.env.PLAYWRIGHT_MODULE || 'playwright')[browserName];
const {bridge} = require('./shared_craft_test_bridge');

(async () => {
  const api = bridge();
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-craft-framer-'));
  let browser;
  let posts = [], errors = [];
  // Actual browser HTTP requests, forwarding only to disposable ASGI fixtures.
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (!url.pathname.startsWith('/shared-craft/')) {
        res.writeHead(404); res.end(); return;
      }
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const raw = Buffer.concat(chunks);
      const body = raw.length ? JSON.parse(raw) : undefined;
      const result = await api.request(url.pathname.slice('/shared-craft/'.length), req.method, body);
      if (req.method === 'POST') posts.push({path: url.pathname, body, result, requestBytes: raw.length});
      const bytes = Buffer.from(result.body, 'base64');
      res.writeHead(result.status, {'Content-Type': result.contentType, 'Content-Length': bytes.length});
      res.end(bytes);
    } catch (error) {
      errors.push(error.message);
      res.writeHead(500); res.end(String(error));
    }
  });
  const report = [];
  const json = async (url, method = 'GET', body) => {
    const r = await api.request(url, method, body);
    assert.equal(r.status, 200, Buffer.from(r.body, 'base64').toString().slice(0, 500));
    assert.match(r.contentType, /application\/json/);
    return JSON.parse(Buffer.from(r.body, 'base64'));
  };
  try {
    await api.ready;
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const origin = `http://127.0.0.1:${server.address().port}`;
    browser = await browserType.launch({headless: true,
      ...(browserName === 'chromium' ? {args: ['--no-sandbox']} : {})});
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      page.setDefaultTimeout(15000);
      errors = []; posts = [];
      const pouch = width !== 360;
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => { errors.push(`Unexpected confirmation: ${dialog.message()}`); dialog.dismiss(); });
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin || !url.pathname.startsWith('/shared-craft/')) {
          errors.push(`Unexpected URL: ${url.href}`); return route.abort();
        }
        return route.continue();
      });
      const board = (await json('api/bead-boards', 'POST',
        {title: `装裱 ${width}`, mode: 'together', size: 8})).board;
      await json(`api/bead-boards/${board.id}`, 'POST',
        {action: 'place', beads: [0, 1, 2].map(x => ({x, y: 0, color: 'F14'}))});
      const boardURL = `${origin}/shared-craft/beads/#${board.id}`;
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
      await page.evaluate(() => document.fonts.ready);
      const preview = () => page.locator('#framer-canvas').evaluate(c => c.toDataURL('image/png'));
      const exportWork = async () => {
        const downloadPromise = page.waitForEvent('download');
        await page.locator('#framer-export').click();
        const download = await downloadPromise;
        await page.waitForFunction(() => document.querySelector('#framer-hint').textContent.includes('已保存到后端'));
        const save = posts.filter(p => p.body?.action === 'decoration').at(-1);
        assert(save, 'original save button must POST decoration over HTTP');
        assert.equal(save.result.status, 200);
        assert.match(save.result.contentType, /application\/json/);
        assert.equal(JSON.parse(Buffer.from(save.result.body, 'base64')).ok, true);
        assert.deepEqual(fs.readFileSync(await download.path()), Buffer.from(save.body.data_url.split(',')[1], 'base64'));
        return save;
      };
      const purePreview = await preview();
      const pure = await exportWork();
      assert.equal(pure.body.data_url, purePreview, 'plain bead export includes the original preview background');
      assert.equal(pure.body.has_pouch, false);
      assert.notEqual(pure.body.data_url, pure.body.piece_data_url);
      // UI geometry and typography are measured without adding/replacing styles.
      const geometry = await page.evaluate(() => {
        const measure = selector => {
          const e = document.querySelector(selector), r = e.getBoundingClientRect(), s = getComputedStyle(e);
          return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            width: r.width, height: r.height, font: s.fontFamily, size: s.fontSize,
            lineHeight: s.lineHeight, border: s.borderWidth, shadow: s.boxShadow, padding: s.padding};
        };
        return Object.fromEntries(['#framer-canvas', '#framer-export', '#framer-hint', '.framer-head strong']
          .map(selector => [selector, measure(selector)]));
      });
      for (const selector of ['#framer-canvas', '#framer-export']) {
        const r = geometry[selector];
        assert(r.left >= 0 && r.right <= width && r.top >= 0 && r.bottom <= 900, JSON.stringify(geometry));
      }
      assert(parseFloat(geometry['#framer-hint'].size) < parseFloat(geometry['.framer-head strong'].size));
      await page.locator('[data-bg="grid"]').click();
      const uploaded = await page.evaluate(() => {
        const png = (color, size) => {
          const c = document.createElement('canvas'); c.width = c.height = size;
          const ctx = c.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0, 0, size, size);
          return c.toDataURL('image/png');
        };
        return {background: png('#376b9a', 96), sticker: png('#22dd66', 90)};
      });
      const upload = async (selector, url) => page.locator(selector).setInputFiles({
        name: 'fixture.png', mimeType: 'image/png', buffer: Buffer.from(url.split(',')[1], 'base64')
      });
      if (width === 430) {
        await upload('#framer-bg-file', uploaded.background);
        await page.waitForFunction(() => document.querySelector('#framer-canvas').getContext('2d').getImageData(5, 5, 1, 1).data[0] === 55);
      }
      const backgroundPreview = await preview();
      const moveDecoration = async (x, y) => {
        const box = await page.locator('#framer-canvas').boundingBox();
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width * x / 900, box.y + box.height * y / 900, {steps: 8});
        await page.mouse.up();
      };
      await page.locator('[data-framer-tab="sticker"]').click();
      await page.locator('[data-sticker="🎀"]').click();
      await moveDecoration(160, 140);
      await page.locator('[data-sticker="🌸"]').click();
      await moveDecoration(730, 150);
      await upload('#framer-sticker-file', uploaded.sticker);
      await page.waitForFunction(() => document.querySelector('#framer-canvas').getContext('2d').getImageData(450, 450, 1, 1).data[1] === 221);
      await moveDecoration(730, 730);
      await page.locator('[data-framer-tab="layer"]').click();
      await page.locator('#framer-outline').click();
      await page.locator('#framer-shadow').click();
      if (pouch) await page.locator('#framer-shell').click();
      const fullPreview = await preview();
      // Select a sticker: selection handles must not be included in the PNG.
      const box = await page.locator('#framer-canvas').boundingBox();
      await page.mouse.click(box.x + box.width * 730 / 900, box.y + box.height * 730 / 900);
      const selectedPreview = await preview();
      assert.notEqual(selectedPreview, fullPreview);
      await page.screenshot({path: path.join(output, `${width}-preview.png`), fullPage: true});
      const save = await exportWork();
      assert.equal(save.body.data_url, fullPreview, 'full PNG must exactly equal the unselected 900x900 preview');
      assert.equal(await preview(), selectedPreview, 'exports must restore the selected preview');
      assert.equal(save.path, `/shared-craft/api/bead-boards/${board.id}`);
      assert.equal(save.body.has_pouch, pouch);
      assert(save.requestBytes <= 17_000_000 && save.requestBytes <= 20_000_000);
      const images = [save.body.data_url, save.body.piece_data_url].map(url => {
        assert.match(url, /^data:image\/png;base64,/);
        const png = Buffer.from(url.split(',')[1], 'base64');
        assert.equal(png.subarray(1, 4).toString(), 'PNG');
        assert(png.length <= 6_000_000, 'PNG must fit the existing backend limit');
        return png;
      });
      assert(!images[0].equals(images[1]), 'full composition and transparent piece must stay distinct');
      const pixels = await page.evaluate(async ({full, piece, background}) => {
        const decode = async src => {
          const img = new Image(); img.src = src; await img.decode();
          const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
          const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
          return {width: c.width, height: c.height, data: ctx.getImageData(0, 0, c.width, c.height).data};
        };
        const f = await decode(full), p = await decode(piece), bg = await decode(background);
        const changed = (x, y) => {
          let count = 0;
          for (let dy = -60; dy < 60; dy++) for (let dx = -60; dx < 60; dx++) {
            const i = ((y + dy) * 900 + x + dx) * 4;
            if ([0, 1, 2].some(k => Math.abs(f.data[i + k] - bg.data[i + k]) > 10)) count++;
          }
          return count;
        };
        return {size: [f.width, f.height], bow: changed(160, 140), flower: changed(730, 150),
          upload: Array.from(f.data.slice((730 * 900 + 730) * 4, (730 * 900 + 730) * 4 + 4)),
          background: Array.from(f.data.slice((5 * 900 + 5) * 4, (5 * 900 + 5) * 4 + 4)),
          originalBackground: Array.from(bg.data.slice((5 * 900 + 5) * 4, (5 * 900 + 5) * 4 + 4)),
          pieceTransparent: p.data.some((v, i) => i % 4 === 3 && v === 0),
          pieceOpaque: p.data.some((v, i) => i % 4 === 3 && v === 255),
          pieceSize: [p.width, p.height]};
      }, {full: save.body.data_url, piece: save.body.piece_data_url, background: backgroundPreview});
      assert.deepEqual(pixels.size, [900, 900]);
      assert(pixels.bow > 100 && pixels.flower > 100, JSON.stringify(pixels));
      assert.deepEqual(pixels.upload, [34, 221, 102, 255]);
      assert.deepEqual(pixels.background, pixels.originalBackground);
      assert.equal(pixels.background[3], 255);
      assert(pixels.pieceTransparent && pixels.pieceOpaque);
      assert(pixels.pieceSize[0] < 900 && pixels.pieceSize[1] < 900);
      fs.writeFileSync(path.join(output, `${width}-full.png`), images[0]);
      fs.writeFileSync(path.join(output, `${width}-piece.png`), images[1]);
      const draftKey = `bead-framer-v1:${board.id}`;
      const draft = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), draftKey);
      assert.equal(draft.bg, 'grid');
      assert(draft.objects.some(o => o.kind === 'emoji' && o.value === '🎀'));
      assert(draft.objects.some(o => o.kind === 'emoji' && o.value === '🌸'));
      assert(draft.objects.some(o => o.kind === 'sticker' && o.src === uploaded.sticker));
      assert.equal(draft.bgImage, width === 430 ? uploaded.background : null);
      assert(draft.objects.some(o => o.kind === 'piece' && o.shell === pouch && o.outline && o.shadow));
      await page.screenshot({path: path.join(output, `${width}-saved.png`), fullPage: true});

      await page.reload();
      await page.locator('#save').click();
      await page.locator('#framer-gallery').waitFor();
      assert.deepEqual(await page.evaluate(key => JSON.parse(localStorage.getItem(key)), draftKey), draft);
      // Upstream re-renders fused bead texture with a seed based on board.revision.
      // Saving decoration increments revision, so its bead highlights change on
      // reopening even though the persisted decoration/background/stickers match.
      // Test exactly those stable layers and ensure only a small image fraction
      // can change, rather than requiring an upstream pixel-identical rerender.
      await page.waitForTimeout(500);
      const restoredVisual = await page.evaluate(async expected => {
        const im = new Image(); im.src = expected; await im.decode();
        const reference = document.createElement('canvas');
        reference.width = reference.height = 900;
        reference.getContext('2d').drawImage(im, 0, 0);
        const before = reference.getContext('2d').getImageData(0, 0, 900, 900).data;
        const after = document.querySelector('#framer-canvas').getContext('2d').getImageData(0, 0, 900, 900).data;
        const protectedRegions = [[160,140],[730,150],[730,730]];
        let changed = 0, decorationsChanged = 0;
        for (let y=0;y<900;y++) for (let x=0;x<900;x++) {
          const i=(y*900+x)*4;
          if ([0,1,2,3].some(k=>Math.abs(before[i+k]-after[i+k])>2)) {
            changed++;
            if (protectedRegions.some(([cx,cy])=>Math.abs(x-cx)<65&&Math.abs(y-cy)<65))
              decorationsChanged++;
          }
        }
        return {changedFraction:changed/(900*900), decorationsChanged};
      }, fullPreview);
      assert(restoredVisual.changedFraction < .08, `reopened picture must match visually: ${JSON.stringify(restoredVisual)}`);
      assert.equal(restoredVisual.decorationsChanged, 0, 'background, bow, flower and uploaded sticker must survive reload');
      for (const selector of ['#framer-outline', '#framer-shadow'])
        assert(await page.locator(selector).evaluate(e => e.classList.contains('active')));
      assert.equal(await page.locator('#framer-shell').evaluate(e => e.classList.contains('active')), pouch);
      const state = (await json(`api/bead-boards/${board.id}`)).board;
      assert.equal(state.craft.stage, 'finished');
      assert.equal(state.craft.decorated, true);
      assert.equal(state.craft.has_pouch, pouch);
      await page.locator('#framer-gallery').click();
      await page.waitForURL('**/shared-craft/crafts/?v=5');
      const collect = posts.find(p => p.path === '/shared-craft/api/gallery');
      assert(collect, 'original gallery button must POST gallery');
      assert.equal(collect.result.status, 200);
      assert.match(collect.result.contentType, /application\/json/);
      const saved = JSON.parse(Buffer.from(collect.result.body, 'base64'));
      assert.equal(saved.ok, true);
      assert.equal(saved.item.source_id, board.id);
      assert.equal(saved.item.has_pouch, pouch);
      assert.equal(saved.item.presentation, 'decorated');
      assert.equal(collect.body.piece_columns, 3);
      assert.equal(collect.body.piece_rows, 1);
      await page.reload();
      await page.locator('#open-all').click();
      await page.getByText(board.title, {exact: true}).first().waitFor();
      assert((await json('api/gallery')).items.some(item => item.id === saved.item.id));
      for (const [index, suffix] of ['.png', '/display.png'].entries()) {
        const image = await page.request.get(`${origin}/shared-craft/api/gallery/${saved.item.id}${suffix}`);
        assert.equal(image.status(), 200);
        assert.match(image.headers()['content-type'], /image\/png/);
        assert.equal(Number(image.headers()['content-length']), images[index].length);
        assert.deepEqual(await image.body(), images[index]);
      }
      if (pouch) {
        await page.locator(`[data-hang="${saved.item.id}"]`).click();
        await page.locator(`#charms [data-open="${saved.item.id}"] img`).waitFor({state: 'attached'});
        assert.match(await page.locator(`#charms [data-open="${saved.item.id}"] img`).getAttribute('src'), /\/display.png$/);
        await page.locator('#close').click();
      } else {
        await page.locator(`[data-feature="${saved.item.id}"]`).click();
        await page.locator('[data-slot="1"]').click();
        await page.locator(`[data-slot="1"][data-id="${saved.item.id}"] img`).waitFor();
        assert.match(await page.locator('[data-slot="1"] img').getAttribute('src'), new RegExp(`${saved.item.id}\\.png$`));
      }
      await page.screenshot({path: path.join(output, `${width}-gallery.png`), fullPage: true});
      await page.locator('#open-all').click();
      const shareButton = page.locator(`[data-share="${saved.item.id}"]`);
      assert.equal(await shareButton.count(), 1);
      assert.equal(await shareButton.textContent(), '分享给作者');
      const beforeSharing = (await api.request('author', 'GET', undefined, 6));
      const authorCount = JSON.parse(Buffer.from(beforeSharing.body, 'base64')).total;
      await shareButton.click();
      await page.locator('#share-cancel').click();
      assert(!posts.some(p => p.path === '/shared-craft/api/shares'), 'cancel remains private');
      await shareButton.click();
      assert.equal(await page.locator('#share-decline').count(), 0);
      await page.locator('#share-note').fill(`完整成品 ${width}`);
      const dialogBox = await page.locator('#share-dialog').boundingBox();
      assert(dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= width);
      assert(dialogBox.y >= 0 && dialogBox.y + dialogBox.height <= 900);
      await page.screenshot({path: path.join(output, `${width}-share.png`), fullPage: true});
      await page.locator('#share-form button[type="submit"]').click();
      await page.waitForFunction(() => !document.querySelector('#share-dialog').open);
      const submissions = posts.filter(p => p.path === '/shared-craft/api/shares');
      assert.equal(submissions.length, 1, 'one explicit submit, no second confirmation');
      assert.equal('confirm' in submissions[0].body, false);
      const shared = JSON.parse(Buffer.from(submissions[0].result.body, 'base64'));
      assert.equal(shared.status, 'shared');
      assert.deepEqual(Buffer.from((await api.request(`author/${shared.id}`, 'GET', undefined, 6)).body, 'base64'), images[0]);
      assert.equal(JSON.parse(Buffer.from((await api.request('author', 'GET', undefined, 6)).body, 'base64')).total, authorCount + 1);
      assert.deepEqual(errors, []);
      report.push({browser: browserName, width, saveJSON: true, galleryJSON: true, downloadMatches: true,
        draftRestored: true, imagesPersisted: true, pouch, pixels, geometry,
        requestBytes: save.requestBytes,
        pngs: images.map(png => ({bytes: png.length,
          sha256: createHash('sha256').update(png).digest('hex')})),
        directSharing: true, realHTTP: true, errors});
      await page.close();
    }
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(`PASS ${browserName}: 360/430/1280 ironing, decoration, download, gallery and reload; ${output}`);
  } finally {
    await browser?.close();
    if (server.listening) await new Promise(resolve => server.close(resolve));
    await api.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
