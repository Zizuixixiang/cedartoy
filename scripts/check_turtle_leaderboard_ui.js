/* Actual React DOM with controlled response ordering; no services or databases. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const frontend = path.resolve(__dirname, '../turtle-soup/frontend');
const esbuild = require(path.join(frontend, 'node_modules/esbuild'));
const pause = () => new Promise(resolve => setTimeout(resolve, 10));
async function until(check) {
  for (let i = 0; i < 100 && !check(); i++) await pause();
  assert.ok(check(), 'DOM update completed');
}
(async () => {
  const compiled = await esbuild.build({stdin: {contents: `import React from 'react'; import {createRoot} from 'react-dom/client'; import {MemoryRouter} from 'react-router-dom'; import Lobby from './src/pages/Lobby.jsx'; createRoot(document.getElementById('root')).render(<MemoryRouter><Lobby/></MemoryRouter>);`, resolveDir: frontend, loader: 'jsx'}, bundle: true,
    write: false, format: 'iife', jsx: 'automatic', define: {'process.env.NODE_ENV': '"test"'}});
  const dom = new JSDOM('<div id="root"></div>', {url: 'http://soup.test/soup/', runScripts: 'dangerously', pretendToBeVisual: true});
  const w = dom.window, doc = w.document;
  const requests = [], errors = [];
  w.addEventListener('error', e => errors.push(e.message));
  w.fetch = url => {
    if (url.startsWith('/soup/api/leaderboard/')) {
      return new Promise(resolve => requests.push({url, reply: (rows, ok = true) => resolve({ok, status: ok ? 200 : 500, json: async () => rows})}));
    }
    const player = {id: 1, username: '测试游客', is_guest: true};
    const data = url.endsWith('/auth/guest') ? {token: 'fixture-token', player}
      : url.endsWith('/auth/me') ? {player} : [];
    return Promise.resolve({ok: true, status: 200, json: async () => data});
  };
  const respond = (req, name) => req.reply([{id: 1, username: name, is_ai: 0, score: 7}]);
  const scopeButtons = () => [...doc.querySelectorAll('.leaderboard-scope button')];
  const metricButtons = () => [...doc.querySelectorAll('.leaderboard-tabs button')];
  const request = async (count, url) => {await until(() => requests.length === count); assert.equal(requests.at(-1).url, '/soup/api/leaderboard/' + url); return requests.at(-1);};
  try {
    w.eval(compiled.outputFiles[0].text);
    await until(() => doc.querySelectorAll('.lobby-bottom-nav button').length === 4);
    assert.equal(scopeButtons().length, 0, 'scope is absent from the room list');
    [...doc.querySelectorAll('.lobby-bottom-nav button')].find(b => b.textContent === '排行榜').click();
    respond(await request(1, 'games'), '总榜');
    await until(() => doc.querySelector('.rank-list li')?.textContent === '总榜7');
    assert.deepEqual(scopeButtons().map(b => b.textContent), ['总榜', '今日']);
    assert.equal(doc.querySelector('.leaderboard-heading h1').textContent, '▥排行榜');
    assert.equal(doc.querySelectorAll('.rooms-head .leaderboard-heading .leaderboard-scope').length, 1);
    assert.equal(doc.querySelectorAll('.leaderboard-panel .leaderboard-scope').length, 0, 'no duplicate control inside the panel');
    assert.equal(scopeButtons()[0].getAttribute('aria-pressed'), 'true');
    assert.deepEqual(metricButtons().map(b => b.textContent), ['完成对局最多', '猜中汤底最多', '提问最多', '被答“是”最多', '被答“不是”最多']);
    metricButtons()[3].click();
    const slowAll = await request(2, 'yes');
    scopeButtons()[1].click();
    const today = await request(3, 'yes?scope=today');
    assert.equal(metricButtons()[3].className, 'active', 'scope preserves the selected metric');
    assert.equal(doc.querySelectorAll('.rank-list li').length, 0, 'old results clear while loading');
    respond(today, '今日');
    await until(() => doc.querySelector('.rank-list li')?.textContent === '今日7');
    respond(slowAll, '迟到总榜');
    await pause();
    assert.equal(doc.querySelector('.rank-list li').textContent, '今日7', 'stale all response cannot overwrite today');
    const metrics = ['games', 'wins', 'asks', 'yes', 'no'];
    let count = 3;
    for (let i = 0; i < metrics.length; i++) {
      metricButtons()[i].click();
      const req = await request(++count, metrics[i] + '?scope=today');
      req.reply([
        {id: 2, username: '机器侦探', is_ai: 1, score: 9},
        {id: 1, username: '人类侦探', is_ai: 0, score: 7},
      ]);
      await until(() => doc.querySelectorAll('.rank-list li').length === 2);
      assert.deepEqual([...doc.querySelectorAll('.rank-list li span')].map(el => el.textContent), ['机器侦探 🤖', '人类侦探']);
      assert.deepEqual([...doc.querySelectorAll('.rank-list li b')].map(el => el.textContent), ['9', '7']);
      assert.ok(!doc.querySelector('.rank-list').textContent.includes('· AI'));
      await pause();
      assert.equal(scopeButtons()[1].getAttribute('aria-pressed'), 'true');
    }
    scopeButtons()[0].click();
    const all = await request(++count, 'no');
    all.reply({detail: 'failure'}, false);
    await pause();
    assert.equal(doc.querySelectorAll('.rank-list li').length, 0);
    assert.equal(metricButtons()[4].className, 'active');
    metricButtons()[0].click();
    const empty = await request(++count, 'games');
    empty.reply([]);
    await pause();
    assert.equal(doc.querySelectorAll('.rank-list li').length, 0);
    assert.deepEqual(errors, []);
    const source = fs.readFileSync(path.join(frontend, 'src/components/Leaderboard.jsx'), 'utf8');
    assert.ok(!source.includes('· AI'), 'legacy AI suffix is absent');
    const css = fs.readFileSync(path.join(frontend, 'src/styles/global.css'), 'utf8');
    assert.equal((css.match(/\.leaderboard-scope \{/g) || []).length, 1, 'no stacked scope overrides');
    assert.match(css, /\.leaderboard-tabs\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*overflow-x:\s*auto;/);
    console.log('turtle leaderboard DOM/static checks passed: AI emoji/human names, scores/order, title scope placement, default, all metrics, retained metric, loading/empty/error, stale responses');
  } finally {w.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
