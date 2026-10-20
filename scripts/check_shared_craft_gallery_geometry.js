/* CSS/image geometry proof, NOT a browser render or screenshot.
 * Run: node scripts/check_shared_craft_gallery_geometry.js
 * Loads the real adapted HTML/assets without accounts, servers or save writes.
 * Model: centered width-limited main, ratio-sized wall, centered object-fit:cover,
 * absolute percentage hotspots. Fail if relevant CSS leaves this model.
 */
const assert = require('node:assert/strict');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const cssTree = require('css-tree');

(async () => {
  const {JSDOM} = await import('jsdom');
  const {stdout} = await promisify(execFile)(process.env.SHARED_CRAFT_TEST_PYTHON || 'python3', ['-c', `
import json, re
from PIL import Image
from shared_craft_adapter import ui
html = ui.page("crafts").body.decode()
names = re.findall(r'<link rel="stylesheet" href="../assets/([^"?]+)', html)
with Image.open(ui.STATIC / "craft-gallery-pouches-v2.webp") as image:
    size = image.size
print(json.dumps(dict(html=html, size=size,
    styles=[dict(name=name, text=ui.asset(name).body.decode()) for name in names])))
`], {encoding: 'utf8'});
  const source = JSON.parse(stdout);
  assert.deepEqual(source.size, [941, 1672]);
  assert.deepEqual(source.styles.map(s => s.name), ['crafts.css', 'crafts-fix.css', 'platform.css']);
  const dom = new JSDOM(source.html);
  const doc = dom.window.document;
  // Parse source declarations directly: jsdom's style engine drops some min()
  // values and has no layout engine. Its DOM is used only for selector matching.
  const styles = source.styles.map(asset => cssTree.parse(asset.text));

  function css(selector, viewportWidth, adapted) {
    const element = doc.querySelector(selector), winners = {};
    function rules(list) {
      for (const rule of list) {
        if (rule.type === 'Atrule') {
          assert.equal(rule.name, 'media');
          const condition = cssTree.generate(rule.prelude);
          const match = /^\(max-width:\s*(\d+)px\)$/.exec(condition);
          assert(match, `Unsupported media query: ${condition}`);
          if (viewportWidth <= +match[1]) rules(rule.block.children);
        } else if (rule.type === 'Rule') {
          for (const selector of cssTree.generate(rule.prelude).split(',')) {
            if (!element.matches(selector)) continue;
            // All selectors matching the measured elements are simple compounds.
            assert(/^[\w.#*\s-]+$/.test(selector), `Unsupported selector: ${selector}`);
            const weight = (selector.match(/#/g) || []).length * 100
              + (selector.match(/\./g) || []).length * 10
              + (selector.match(/(?:^|\s)[a-z]+/g) || []).length;
            for (const declaration of rule.block.children) {
              assert.equal(declaration.type, 'Declaration');
              const key = declaration.property;
              assert(!declaration.important, 'Unexpected !important');
              if (!winners[key] || weight >= winners[key].weight)
                winners[key] = {weight, value: cssTree.generate(declaration.value)};
            }
          }
        }
      }
    }
    styles.slice(0, adapted ? 3 : 2).forEach(sheet => rules(sheet.children));
    return Object.fromEntries(Object.entries(winners).map(([k, v]) => [k, v.value]));
  }

  const percent = value => {
    assert(/^\d+(\.\d+)?%$/.test(value), `Expected percentage: ${value}`);
    return parseFloat(value) / 100;
  };
  // Independently read inner-opening bounds in the 941×1672 source artwork.
  // Rounded pixel measurements, with 2px source-image tolerance.
  const openings = [[54, 177, 295, 495], [349, 161, 588, 513], [649, 177, 898, 507]];
  function geometry(width, height, adapted) {
    const main = css('main', width, adapted), wall = css('.wall', width, adapted);
    const img = css('.room-bg', width, adapted);
    assert.equal(main.width, 'min(100%,590px)');
    assert.equal(main.margin, 'auto');
    assert.equal(wall.position, 'relative');
    assert.equal(wall.width, '100%');
    assert.equal(img.width, '100%');
    assert.equal(img.height, '100%');
    assert.equal(img['object-fit'], 'cover');
    for (const style of [main, wall, img]) {
      for (const key of ['padding', 'border', 'max-height', 'transform', 'object-position'])
        assert(!style[key], `Unmodeled geometry: ${key}=${style[key]}`);
    }
    assert(!wall.height && !main.height && !main['min-width'] && !wall['min-width']);
    assert([undefined, '0', '100dvh'].includes(wall['min-height']));
    const ratio = wall['aspect-ratio'].split('/').map(Number);
    assert(ratio.length === 2 && ratio.every(n => n > 0));
    const w = Math.min(width, 590);
    const h = Math.max(w * ratio[1] / ratio[0], wall['min-height'] === '100dvh' ? height : 0);
    const scale = Math.max(w / source.size[0], h / source.size[1]);
    const cropX = (source.size[0] * scale - w) / 2;
    const cropY = (source.size[1] * scale - h) / 2;
    const hotspots = openings.map((opening, i) => {
      const slot = css(`.slot-${i + 1}`, width, adapted);
      assert.equal(slot.position, 'absolute');
      const [l, t, sw, sh] = ['left', 'top', 'width', 'height'].map(k => percent(slot[k]));
      const edges = [l * w, t * h, (l + sw) * w, (t + sh) * h];
      assert(edges[0] >= 0 && edges[2] <= w && sw * w >= 44 && sh * h >= 44);
      const painted = opening.map((p, j) => p * scale - (j % 2 ? cropY : cropX));
      return {edges, error: Math.max(...edges.map((v, j) => Math.abs(v - painted[j])))};
    });
    return {w, h, scale, cropX, cropY, hotspots};
  }

  const reports = [];
  for (const [width, height] of [[360, 640], [360, 800], [360, 960], [390, 844],
                               [420, 932], [430, 640], [430, 932], [1280, 900]]) {
    const before = geometry(width, height, false), after = geometry(width, height, true);
    assert(after.cropX < 0.25 && after.cropY < 0.25, 'Background must be essentially complete');
    if (width <= 420) assert(Math.abs(after.cropX) < 1e-9 && Math.abs(after.cropY) < 1e-9);
    else assert.deepEqual(after, before, 'Wider layouts must remain unchanged');
    for (const slot of after.hotspots) assert(slot.error <= 2 * after.scale, 'Hotspot misses painted opening');
    if (width === 360 && height === 960) {
      assert(before.cropX > 80, 'Fixture must reproduce the original clipping');
      assert(before.hotspots[0].error > 50, 'Fixture must reproduce hotspot drift');
    }
    reports.push({viewport: `${width}×${height}`, wall: `${after.w}×${after.h.toFixed(2)}`,
      oldCropEachSide: before.cropX.toFixed(2), newCropEachSide: after.cropX.toFixed(2),
      maxHotspotEdgeError: Math.max(...after.hotspots.map(s => s.error)).toFixed(2)});
  }
  console.table(reports);
  console.log('PASS: CSS/image geometry only; browser rendering remains unverified.');
  dom.window.close();
})().catch(error => {console.error(error); process.exitCode = 1;});
