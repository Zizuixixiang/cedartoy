// Execute the real homepage controls in a DOM; no production HTTP or storage.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const home = fs.readFileSync('index.html', 'utf8');
const catalog = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(home, {runScripts: 'outside-only', url: 'http://localhost/'});
const w = dom.window;
const source = home.slice(home.indexOf('    const puzzleBoxState ='), home.indexOf('    const saveGameNames ='));
const calls = [];
let confirmation = false;
let confirms = 0;
let pendingReveal = null;
w.confirm = () => { confirms++; return confirmation; };
w.fetch = async (url, options) => {
  calls.push({url, options});
  if (url.endsWith('/reveal')) {
    if (pendingReveal) return pendingReveal;
    const body = JSON.parse(options.body);
    return {ok: true, json: async () => ({id: body.puzzle_id, steps: '单题步骤', answer: '单题标准谜底'})};
  }
  const items = catalog.items.map((p, i) => ({...p, status: url.includes('ai_user_id=2') ? 'unseen' : ['solved', 'opened', 'unseen'][i % 3]}));
  return {ok: true, json: async () => ({items, summary: url.includes('ai_user_id=') ? {solved: 8, opened: 7, unseen: 7} : null})};
};
w.eval(`
  let me = {user:{id:10,is_ai:false},bindings:[{id:1,username:'小机甲',is_ai:true},{id:2,username:'小机乙',is_ai:true}]};
  let testToken = 'human-token';
  const token = () => testToken;
  const headers = () => ({Authorization: 'Bearer ' + token(), 'Content-Type':'application/json'});
  const $ = id => document.getElementById(id);
  const currentGame = () => ({id:'puzzle_box'});
  const aiBindings = () => me ? me.bindings : [];
  const escapeHtml = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
  const memoriaGuideBoxes = () => [$('memoriaGuides'), $('drawerMemoriaGuides')];
  const closeBankPicker = () => $('bankPicker').classList.remove('show');
  ${source}
  window.testBox = {loadPuzzleBox, revealPuzzleBox, openPuzzleBoxPicker, state: puzzleBoxState,
    logout: () => {me=null;testToken='';}, renderPuzzleBox};
`);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const b = w.testBox;
  await b.loadPuzzleBox();
  const panel = w.document.getElementById('memoriaGuides');
  assert.equal(panel.querySelectorAll('[data-puzzle-reveal]').length, 22);
  assert.equal(panel.querySelectorAll('small').length, 6);
  assert.ok(panel.textContent.includes('已解 8 · 待解 7 · 未拆 7'));
  assert.ok(!panel.textContent.includes('标准谜底\n'));
  assert.equal(calls.filter(c => c.url.endsWith('/reveal')).length, 0);

  // Opened/unseen: cancelling must not even request answer content.
  for (const pid of ['N02', 'N03']) {
    await b.revealPuzzleBox(pid);
    assert.equal(calls.filter(c => c.url.endsWith('/reveal')).length, 0);
  }
  assert.equal(confirms, 2);
  confirmation = true;
  panel.querySelector('[data-puzzle-reveal="N02"]').click();
  await tick();
  assert.equal(confirms, 3);
  assert.equal(JSON.parse(calls.at(-1).options.body).confirm_spoiler, true);
  assert.ok(panel.textContent.includes('单题标准谜底'));
  assert.equal(panel.querySelectorAll('[aria-expanded="true"]').length, 1);

  // Solved: no warning; only one answer is expanded at once.
  await b.revealPuzzleBox('N01');
  assert.equal(confirms, 3);
  assert.equal(JSON.parse(calls.at(-1).options.body).confirm_spoiler, false);
  assert.equal(panel.querySelectorAll('[aria-expanded="true"]').length, 1);
  assert.equal(b.state.expanded, 'N01');
  await b.revealPuzzleBox('N01');
  assert.ok(!panel.textContent.includes('单题标准谜底'));

  // Use the actual site's picker elements; switching clears the previous answer.
  b.openPuzzleBoxPicker();
  assert.equal(w.document.querySelectorAll('[data-puzzle-ai]').length, 2);
  w.document.querySelector('[data-puzzle-ai="2"]').click();
  await tick();
  assert.ok(calls.at(-1).url.includes('ai_user_id=2'));
  assert.ok(panel.textContent.includes('小机乙'));
  assert.equal(b.state.expanded, '');
  assert.ok(!panel.textContent.includes('单题标准谜底'));

  // A delayed response for another machine must never appear after switching.
  let finish;
  pendingReveal = new Promise(resolve => { finish = resolve; });
  const stale = b.revealPuzzleBox('N02');
  b.openPuzzleBoxPicker();
  w.document.querySelector('[data-puzzle-ai="1"]').click();
  await tick();
  finish({ok:true,json:async () => ({answer:'不应出现的旧答案',steps:'旧步骤'})});
  await stale;
  assert.ok(!panel.textContent.includes('不应出现的旧答案'));

  b.logout();
  await b.loadPuzzleBox();
  assert.ok(!panel.textContent.includes('小机甲'));
  assert.equal(panel.querySelectorAll('[data-puzzle-reveal]:disabled').length, 22);
  assert.ok(!panel.textContent.includes('单题标准谜底'));
  dom.window.close();
  console.log('Puzzle box DOM interactions passed');
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
