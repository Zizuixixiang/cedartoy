// Execute the real homepage controls in a DOM; no production HTTP or storage.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const home = fs.readFileSync('index.html', 'utf8');
const catalog = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(home, {runScripts: 'outside-only', url: 'http://localhost/'});
const w = dom.window;
const source = home.slice(home.indexOf('    const puzzleBoxState ='), home.indexOf('    const saveGameNames ='));
const tabs = home.slice(home.indexOf('    function gameHasGuide(game)'), home.indexOf('    function memoriaGuideBoxes()'));
const selection = home.slice(home.indexOf('    function selectGame(id, maybeDrawer)'), home.indexOf('    let pendingAdultUrl ='));
const guideLoader = home.slice(home.indexOf('    function ensureMemoriaGuidesLoaded()'), home.indexOf('    async function loadMemoriaGuides()'));
const gameCatalog = home.slice(home.indexOf('    const games = ['), home.indexOf('    let selected = "soup";'));
const longCode = 'SUYgWU9VIEZPVU5EIFRISVMsIEkgV0FTIEhPUElORyBJVCBXT1VMRCBCRSBZT1Uu'.repeat(3);
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
    return {ok: true, json: async () => ({id: body.puzzle_id, steps: '单题步骤\n' + longCode + '\nK7→M2→B3→Q4→H8→R6→A9→T5→N3→P8→D2→L4→X1→C5→V8→J3→end', answer: '单题标准谜底\n' + 'LONGANSWER'.repeat(20)})};
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
  ${gameCatalog}
  let selected = 'memoria';
  let detailTab = 'guide', drawerTab = 'guide';
  const currentGame = () => games.find(game => game.id === selected);
  const memoriaGuideState = {loaded:true};
  const renderGames = () => {};
  const renderDetail = () => {renderDetailTabs(currentGame());renderPuzzleBox();};
  const openGameDrawer = () => setDrawerTab(drawerTab);
  const aiBindings = () => me ? me.bindings : [];
  const escapeHtml = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
  const memoriaGuideBoxes = () => [$('memoriaGuides'), $('drawerMemoriaGuides')];
  const closeBankPicker = () => $('bankPicker').classList.remove('show');
  ${tabs}
  ${selection}
  ${guideLoader}
  ${source}
  window.testBox = {loadPuzzleBox, revealPuzzleBox, openPuzzleBoxPicker, state: puzzleBoxState,
    logout: () => {me=null;testToken='';}, renderPuzzleBox,
    setGame: id => {selected=id;renderDetailTabs(currentGame());}, setDrawerTab, setDetailTab,
    selectGame, previewGame};
`);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const b = w.testBox;
  // Carry a previous game's guide tab into selection: puzzle_box must reset both views.
  w.matchMedia = () => ({matches:true});
  b.selectGame('puzzle_box', true);
  assert.equal(w.document.querySelector('.preview').dataset.detailTab, 'preview');
  assert.equal(w.document.getElementById('gameDrawerPanel').dataset.detailTab, 'preview');
  assert.equal(calls.length, 0);
  b.setGame('memoria');
  b.setDetailTab('guide');
  b.setDrawerTab('guide');
  b.previewGame('puzzle_box');
  assert.equal(w.document.querySelector('.preview').dataset.detailTab, 'preview');
  assert.equal(w.document.getElementById('gameDrawerPanel').dataset.detailTab, 'preview');
  await tick();
  assert.equal(calls.length, 0);
  assert.equal(b.state.data, null);

  // Only a deliberate guide click begins the progress request (desktop + mobile).
  w.document.getElementById('guideTab').addEventListener('click', () => b.setDetailTab('guide'));
  w.document.getElementById('guideTab').click();
  await tick();
  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.startsWith('/api/puzzle-box/progress'));
  b.selectGame('puzzle_box', true);
  assert.equal(calls.length, 1);
  b.state.data = null; // Exercise an uncached mobile guide entry as well.
  const mobileGuide = w.document.querySelector('[data-drawer-tab="guide"]');
  mobileGuide.addEventListener('click', () => b.setDrawerTab('guide'));
  mobileGuide.click();
  await tick();
  assert.equal(calls.length, 2);
  assert.ok(calls[1].url.startsWith('/api/puzzle-box/progress'));
  const panel = w.document.getElementById('memoriaGuides');
  const drawerPanel = w.document.getElementById('drawerMemoriaGuides');
  for (const root of [panel, drawerPanel]) {
    assert.equal(root.querySelector('[data-puzzle-picker]').textContent, '小机甲');
  }
  const style = element => w.getComputedStyle(element);
  assert.equal(panel.querySelectorAll('[data-puzzle-reveal]').length, 22);
  assert.equal(panel.querySelectorAll('small').length, 6);
  for (const [status, label, count, pid] of [['solved','✓ 已解',8,'N01'], ['opened','◐ 待解',7,'N02'], ['unseen','○ 未拆',7,'N03']]) {
    for (const root of [panel, drawerPanel]) {
      assert.equal(root.querySelector(`[data-puzzle-reveal="${pid}"] .puzzle-box-status--${status}`).textContent, label);
      assert.equal(root.querySelector(`.puzzle-box-summary .puzzle-box-status--${status}`).textContent, `${label} ${count}`);
    }
  }
  const solved = panel.querySelector('[data-puzzle-reveal="N01"] .puzzle-box-status');
  const unseen = panel.querySelector('[data-puzzle-reveal="N03"] .puzzle-box-status');
  assert.notEqual(solved.className, unseen.className);
  assert.notEqual(style(solved).backgroundColor, style(unseen).backgroundColor);
  assert.notEqual(style(solved).color, style(unseen).color);
  assert.equal(panel.querySelectorAll('.puzzle-box-challenge').length, 6);
  assert.equal(panel.querySelectorAll('.puzzle-box-challenge.puzzle-box-status').length, 0);

  // Only this game's guide CTA disappears; preview/card entry and other games survive.
  const guideEnter = w.document.getElementById('guideEnterButton');
  const drawerActions = w.document.querySelector('#gameDrawerPanel .drawer-actions');
  assert.equal(style(guideEnter).display, 'none');
  assert.equal(style(drawerActions).display, 'none');
  assert.notEqual(style(w.document.getElementById('enterButton')).display, 'none');
  b.setDrawerTab('preview');
  assert.notEqual(style(drawerActions).display, 'none');
  b.setGame('memoria');
  b.setDrawerTab('guide');
  assert.notEqual(style(guideEnter).display, 'none');
  assert.notEqual(style(drawerActions).display, 'none');
  b.setGame('puzzle_box');

  // Run the real card's navigation handler against a fake location, no external request.
  const openGame = home.slice(home.indexOf('    function openGame(game)'), home.indexOf('    function enterGame()'));
  const nav = {window:{location:{href:''}}};
  vm.runInNewContext(gameCatalog + openGame + '\nthis.puzzleGame = games.find(g => g.id === "puzzle_box");openGame(puzzleGame);', nav);
  assert.equal(nav.window.location.href, 'https://xhslink.cn/o/3h6OJZlNro4');
  assert.equal(nav.puzzleGame.metricLabel, '存档数');
  assert.equal(nav.puzzleGame.metric, '--');
  assert.equal(nav.puzzleGame.level, '22 道');
  assert.equal(nav.puzzleGame.iconFile, 'puzzle_box.png');
  const cover = fs.readFileSync('assets/icons/' + nav.puzzleGame.iconFile);
  assert.equal(cover.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
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
  for (const root of [panel, drawerPanel]) {
    const detail = root.querySelector('.puzzle-box-detail');
    assert.ok(detail);
    assert.equal(detail.querySelector('.puzzle-box-steps h3').textContent, '解题步骤');
    assert.equal(detail.querySelector('.puzzle-box-solution h3').textContent, '标准谜底');
    assert.ok(detail.querySelector('.puzzle-box-steps .puzzle-box-text').textContent.includes(longCode));
    assert.ok(detail.querySelector('.puzzle-box-solution .puzzle-box-answer'));
    assert.equal(detail.querySelector('.puzzle-box-steps .puzzle-box-answer'), null);
    // JSDOM has no layout engine: guard the actual computed rules that prevent min-content growth.
    assert.ok(root.classList.contains('puzzle-box-guides'));
    assert.equal(style(root).gridTemplateColumns, 'minmax(0, 1fr)');
    for (const node of [root, root.querySelector('.memoria-guide-list'), detail.parentElement, detail,
                        detail.querySelector('.puzzle-box-solution'), ...detail.querySelectorAll('.puzzle-box-text')]) {
      assert.equal(parseFloat(style(node).minWidth), 0);
      assert.equal(style(node).maxWidth, '100%');
      assert.notEqual(style(node).overflowX, 'hidden');
    }
    for (const node of detail.querySelectorAll('.puzzle-box-text')) {
      assert.equal(style(node).whiteSpace, 'pre-wrap');
      assert.equal(style(node).overflowWrap, 'anywhere');
    }
    assert.notEqual(style(detail.querySelector('.puzzle-box-solution')).backgroundColor, 'rgba(0, 0, 0, 0)');
  }
  assert.equal(b.state.data.items.find(item => item.id === 'N02').status, 'opened');

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
  for (const root of [panel, drawerPanel]) {
    assert.equal(root.querySelector('[data-puzzle-picker]').textContent, '小机乙');
  }
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
