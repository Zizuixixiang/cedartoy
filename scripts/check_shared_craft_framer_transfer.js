/* Actual adapted script/HTML in jsdom; canvas/network boundaries are simulated.
 * No production API, credentials, stores or vendor files are modified.
 * This is behavioral verification, not mobile Safari or rendered visual proof. */
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const python = process.env.SHARED_CRAFT_TEST_PYTHON || 'python3';
const source = process.argv[2] ? fs.readFileSync(process.argv[2], 'utf8') : execFileSync(python, ['-c',
  "from shared_craft_adapter.ui import asset; print(asset('beads-framer.js').body.decode())"], {encoding:'utf8'});
const original = fs.readFileSync('vendor/shared-craft-room/src/shared_craft_room/static/beads-framer.js', 'utf8');
const html = fs.readFileSync('vendor/shared-craft-room/src/shared_craft_room/static/beads.html', 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 5));

(async () => {
  const {JSDOM} = await import('jsdom');
  async function setup(script = source, storage = {}) {
    const dom = new JSDOM(html, {url:'https://craft.test/shared-craft/beads/',
      runScripts:'outside-only', pretendToBeVisual:true});
    const w = dom.window, $ = s => w.document.querySelector(s);
    const calls = [], downloads = [], diagnostics = [];
    let mode = 'ok', release, timeout, blobFailure = false, downloadsBreakFetch = false;
    w.console.info = (...args) => diagnostics.push(args[1]);
    const nativeTimeout = w.setTimeout.bind(w);
    w.setTimeout = (fn, ms, ...args) => {
      if (ms === 45000) { timeout = fn; return 987654; }
      return nativeTimeout(fn, ms, ...args);
    };
    w.Image = class {
      constructor() { this.width = this.height = 96; this.complete = false; }
      set src(value) { this._src = value; nativeTimeout(() => {this.complete = true; this.onload?.();}, 0); }
      get src() { return this._src || ''; }
    };
    w.HTMLCanvasElement.prototype.getContext = function() {
      return new Proxy({}, {get: (_, name) => name === 'getImageData' ? () => {
        const data = new Uint8ClampedArray(900 * 900 * 4);
        data[(450 * 900 + 450) * 4 + 3] = 255;
        return {data};
      } : name === 'createLinearGradient' ? () => ({addColorStop(){}}) : () => {}});
    };
    w.HTMLCanvasElement.prototype.toDataURL = function() {
      return 'data:image/png;base64,' + Buffer.from(`fixture-${this.width}x${this.height}`).toString('base64');
    };
    w.HTMLCanvasElement.prototype.toBlob = function(fn) {
      fn(blobFailure ? null : new w.Blob(['fixture'], {type:'image/png'}));
    };
    w.URL.createObjectURL = () => 'blob:fixture';
    w.URL.revokeObjectURL = () => {};
    w.HTMLAnchorElement.prototype.click = function() {
      downloads.push(this.download);
      if (downloadsBreakFetch) mode = 'network';
    };
    w.makeBeadPiece = () => {
      const canvas = w.document.createElement('canvas'); canvas.width = canvas.height = 24;
      canvas.beadMeta = {keys:['0,0', '1,0'],cell:12,sx:36,sy:36}; return canvas;
    };
    for (const [key, value] of Object.entries(storage)) w.localStorage.setItem(key, value);
    w.fetch = async (url, options) => {
      calls.push({url, options});
      const response = () => ({ok:true, status:200, json:async()=>({ok:true})});
      if (mode === 'network') throw new w.TypeError('Load failed');
      if (mode === 'pending') return new Promise(resolve => {release = () => resolve(response());});
      if (mode === 'timeout') return new Promise((_, reject) => {
        options.signal.addEventListener('abort', () => reject(new w.DOMException('aborted', 'AbortError')));
      });
      if (mode === 'invalid') return {ok:true,status:200,json:async()=>{throw new SyntaxError('HTML');}};
      if (mode === 'rejected') return {ok:true,status:200,json:async()=>({ok:false})};
      if (typeof mode === 'number') return {ok:false,status:mode,json:async()=>{throw Error('non-JSON');}};
      return response();
    };
    w.eval(script);
    const board = {id:'fixture-board',title:'回归豆板',craft:{stage:'finished',decorated:false}};
    await w.openBeadFramer(board);
    await tick();
    return {w, $, board, calls, downloads, diagnostics, close:()=>w.close(),
      mode:value=>{mode=value;}, release:()=>release(), timeout:()=>timeout(),
      blobFailure:value=>{blobFailure=value;}, downloadsBreakFetch:()=>{downloadsBreakFetch=true;},
      save:()=>$('#framer-export').onclick(), download:()=>$('#framer-download').onclick(),
      draft:()=>w.localStorage.getItem('bead-framer-v1:fixture-board')};
  }
  // Reproduce the known *ordering vulnerability*, not a claimed real Safari cause.
  const old = await setup(original);
  old.downloadsBreakFetch();
  await old.save();
  assert.equal(old.downloads.length, 1);
  assert.equal(old.calls.length, 1);
  assert.match(old.$('#framer-hint').textContent, /保存失败：Load failed/);
  old.close();

  const x = await setup();
  try {
    x.mode('network'); await x.save();
    const initial = x.draft(); assert(initial);
    assert.equal(x.downloads.length, 0, 'cloud save never invokes the browser download');
    assert.match(x.$('#framer-cloud-status').textContent, /未确认/);
    assert.equal(x.board.craft.decorated, false);
    assert.equal(x.$('#framer-gallery').hidden, true);
    assert.equal(x.draft(), initial);
    assert.equal(x.$('#framer-export').disabled, false);
    assert.match(x.$('#framer-export').textContent, /重试/);
    const failure = x.diagnostics.find(d => d.operation === 'cloud-error');
    assert.equal(failure.stage, 'request'); assert.equal(failure.status, null);
    assert(failure.bytes > 0); assert.equal(failure.errorName, 'TypeError');
    assert(!JSON.stringify(x.diagnostics).includes('fixture-board'));
    assert(!JSON.stringify(x.diagnostics).includes('data:image'));
    const n = x.calls.length;
    await x.download();
    assert.equal(x.calls.length, n, 'download does not need a working network');
    assert.equal(x.downloads.length, 1);
    assert.match(x.$('#framer-download-status').textContent, /已发起下载/);
    assert.match(x.$('#framer-cloud-status').textContent, /未确认/);

    for (const mode of [401, 403, 413, 503, 'invalid', 'rejected']) {
      x.mode(mode); await x.save();
      assert.equal(x.board.craft.decorated, false);
      assert.equal(x.draft(), initial);
      assert.equal(x.$('#framer-export').disabled, false);
      const d = x.diagnostics.filter(d => d.operation === 'cloud-error').at(-1);
      assert.equal(d.stage, 'response');
      assert.equal(d.status, typeof mode === 'number' ? mode : 200);
    }
    x.mode('timeout'); const waiting = x.save(); x.timeout(); await waiting;
    assert.match(x.$('#framer-cloud-status').textContent, /超时.*未确认/);
    assert.equal(x.$('#framer-export').disabled, false);
    assert.equal(x.draft(), initial);

    x.mode('pending'); const pending = x.save(); const count = x.calls.length;
    await x.save(); await x.download();
    assert.equal(x.calls.length, count, 'double clicks cannot duplicate POST');
    assert.equal(x.downloads.length, 1, 'no download interrupts an in-flight upload');
    x.w.dispatchEvent(new x.w.Event('pagehide'));
    x.release(); await pending;
    assert.equal(x.board.craft.decorated, true);
    assert.equal(x.$('#framer-gallery').hidden, false);
    assert.match(x.$('#framer-cloud-status').textContent, /已保存/);
    assert.equal(x.diagnostics.filter(d=>d.operation === 'cloud-end').at(-1).wasHidden, true);
    assert.equal(x.calls.at(-1).url, '/shared-craft/api/bead-boards/fixture-board');
    const body = JSON.parse(x.calls.at(-1).options.body);
    assert.equal(body.action, 'decoration');
    assert.notEqual(body.data_url, body.piece_data_url);
    assert.equal(body.has_pouch, false);

    x.blobFailure(true); await x.download();
    assert.match(x.$('#framer-download-status').textContent, /失败/);
    assert.match(x.$('#framer-cloud-status').textContent, /已保存/);
    x.blobFailure(false); await x.download();
    assert.match(x.$('#framer-download-status').textContent, /已发起/);
    assert.equal(x.downloads.at(-1), '回归豆板-完整成品.png');

    // Changes while an upload is pending must not be presented as saved.
    x.mode('pending'); const changing = x.save();
    x.$('[data-bg="grid"]').click();
    x.release(); await changing;
    assert.match(x.$('#framer-cloud-status').textContent, /新修改待保存/);
    assert.equal(x.$('#framer-gallery').hidden, true);
    x.mode('network'); await x.save(); // flush last edit even on failure
    const editedDraft = x.draft(); assert.equal(JSON.parse(editedDraft).bg, 'grid');
    const restored = await setup(source, {'bead-framer-v1:fixture-board':editedDraft});
    assert.equal(restored.draft(), editedDraft, 'failed-save draft survives reopening');
    restored.close();

    x.mode('pending'); const switching = x.save();
    await x.w.openBeadFramer({id:'other-board',title:'另一块',craft:{decorated:false}});
    x.release(); await switching;
    assert.equal(x.$('#framer-cloud-status').textContent, '云端：尚未保存');
    assert.equal(x.$('#framer-gallery').hidden, true, 'late response cannot update another board');

    const stored = x.w.localStorage.getItem('bead-framer-v1:other-board');
    x.w.Storage.prototype.setItem = () => {throw new x.w.DOMException('full', 'QuotaExceededError');};
    x.mode('network'); await x.save();
    assert.match(x.$('#framer-draft-status').textContent, /仅在当前页面，请勿关闭/);
    assert.equal(x.w.localStorage.getItem('bead-framer-v1:other-board'), stored);
    assert.equal(x.$('#framer-export').disabled, false);
  } finally { x.close(); }
  console.log('PASS: original ordering reproduction; independent save/download; network/HTTP/non-JSON/timeout recovery; retry; draft reopen/quota; duplicate clicks; editing and board switch races. DOM only, no visual verification.');
})().catch(error => {console.error(error); process.exitCode = 1;});
