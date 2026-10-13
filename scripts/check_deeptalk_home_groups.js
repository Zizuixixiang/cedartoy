'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');

const homeHtml = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const deeptalkHtml = fs.readFileSync(path.join(__dirname, '../deeptalk.html'), 'utf8');
const topics = [
  ['dt_relations', 'relations_communication', 13, '好好说话，也好好相处'],
  ['dt_couple50', 'couple_50', 50, '恋人50问：聊聊我们'],
  ['dt_scenarios', 'relationship_scenarios', 14, '关系里的两难时刻'],
  ['dt_romance100', 'romance_values_100', 100, '恋爱观100问：我们想的一样吗？'],
  ['dt_work20', 'work_values_20', 20, '工作里，你最看重什么？'],
  ['dt_favorites100', 'favorites_100', 100, '你喜欢什么？100问'],
  ['dt_know100', 'getting_to_know_100', 100, '关于你，我还想知道100件事'],
  ['dt_life35', 'life_reflection_35', 35, '深夜谈心：35个人生问题'],
  ['dt_dilemmas30', 'real_life_dilemmas_30', 30, '如果有人向你求助：30道难题'],
  ['dt_learning_goals', 'learning_goals_problem_solving', 28, '学习、目标与未来计划']
];
const delay = () => new Promise(resolve => setTimeout(resolve, 5));

async function home(width) {
  const errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', err => errors.push(err.message));
  const dom = new JSDOM(homeHtml, {
    url: 'https://toy.cedarstar.org/', runScripts: 'dangerously', pretendToBeVisual: true,
    virtualConsole: console,
    beforeParse(window) {
      window.innerWidth = width;
      window.matchMedia = query => ({matches: query.includes('max-width: 599px') && width < 600});
      window.fetch = async url => ({ok: true, status: 200, json: async () => String(url).includes('rooms') ? [] : {}});
    }
  });
  try {
    const d = dom.window.document;
    await delay();
    const tabs = [...d.querySelectorAll('.tab-button')].map(tab => tab.dataset.filter);
    assert.deepEqual(tabs, ['test', 'mini'], 'must not add a third large tab');
    d.querySelector('[data-filter="test"]').click();
    const items = () => [...d.querySelector('#gameList').children].filter(el => !el.classList.contains('hidden'));
    const headings = () => items().filter(el => el.classList.contains('game-group-title')).map(el => el.textContent.trim());
    assert.deepEqual(headings(), ['深聊问答 · 不计分', '测试 · 有结果']);
    const visible = items().filter(el => el.dataset.game);
    assert.equal(visible.length, 36, '10 unscored + 26 scored (18 research & 8 legacy)');
    assert.deepEqual(visible.slice(0, 10).map(el => el.dataset.game), topics.map(row => row[0]));
    assert.equal(visible.slice(10).filter(el => [...el.querySelectorAll('.tag')].some(tag => tag.textContent.trim() === '心理量表')).length, 18);
    for (const [game, topic, count, title] of topics) {
      assert.ok(homeHtml.includes(`url: \`/deeptalk?topic_id=\${encodeURIComponent(topicId)}\``), 'the topic-specific URL must be generated once');
      const card = d.querySelector(`[data-game="${game}"]`);
      assert.equal(card.querySelector('.game-title').textContent.trim(), title);
      assert.equal(card.querySelector('.level').textContent.trim(), `${count}题`);
    }
    // jsdom cannot navigate documents; capture the existing navigation boundary.
    let destination;
    dom.window.openGame = game => { destination = game.url; };
    d.querySelector('[data-game="dt_learning_goals"]').click();
    assert.equal(dom.window.eval('games.find(g => g.id === selected).url'), '/deeptalk?topic_id=learning_goals_problem_solving');
    if (width < 600) {
      assert.ok(d.querySelector('.drawer.show') || d.querySelector('.drawer-panel.show') || d.querySelector('#gameDrawer.show'), 'mobile card opens existing drawer');
      d.getElementById('drawerEnter').click();
    }
    assert.equal(destination, '/deeptalk?topic_id=learning_goals_problem_solving');
    for (const [query, header] of [['恋爱观', '深聊问答 · 不计分'], ['MBTI', '测试 · 有结果'], ['ADHD', '测试 · 有结果'], ['adhd', '测试 · 有结果']]) {
      d.getElementById('searchInput').value = query;
      d.getElementById('searchInput').dispatchEvent(new dom.window.Event('input'));
      assert.deepEqual(headings(), [header]);
      if (query.toLowerCase() === 'adhd') assert.deepEqual(items().filter(el => el.dataset.game).map(el => el.dataset.game), ['ipip_adhd']);
    }
    d.getElementById('searchInput').value = '';
    d.getElementById('searchInput').dispatchEvent(new dom.window.Event('input'));
    d.querySelector('[data-filter="mini"]').click();
    assert.equal(d.querySelectorAll('.game-group-title').length, 0);
    assert.equal(d.querySelector('[data-game="deeptalk"]'), null, 'old single Deeptalk card must not remain');
    assert.ok(d.querySelector('[data-game="soup"]'));
    await delay();
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
  process.stdout.write(`PASS home ${width}px: 10 independent topics, 26 scored tests, two headings, 2 tabs, search & games retained\n`);
}

async function deeptalkTopic(topicId, count) {
  const errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', err => errors.push(err.message));
  const dom = new JSDOM(deeptalkHtml, {
    url: `https://toy.cedarstar.org/deeptalk?topic_id=${encodeURIComponent(topicId)}`,
    runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: console,
    beforeParse(window) {
      window.localStorage.setItem('cedartoy_token', 'fixture-human');
      window.fetch = async url => {
        assert.ok(String(url).startsWith('/api/deeptalk?'));
        return {ok: true, status: 200, json: async () => ({
          username: 'fixture-human',
          topics: topics.map(([, tid, size, name]) => ({topic_id: tid, total: size, title: name, description: name, available: true})),
          machines: [{id: 2, username: 'fixture-ai'}]
        })};
      };
    }
  });
  try {
    await delay();
    const d = dom.window.document;
    assert.equal(d.getElementById('setup').hidden, false);
    assert.equal(d.getElementById('topic'), null);
    assert.equal(d.getElementById('machine'), null);
    assert.equal(d.getElementById('topicTitle').textContent, topics.find(t => t[1] === topicId)[3]);
    assert.ok(d.getElementById('topicDescription').textContent.includes(`${count}题`));
    assert.equal(d.getElementById('mode'), null);
    assert.equal(d.getElementById('modeIntro'), null);
    assert.equal(d.getElementById('create').disabled, false);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

(async () => {
  await home(360); await home(430); await home(1280);
  for (const [, topicId, count] of topics) await deeptalkTopic(topicId, count);
  console.log('PASS Deeptalk page: all 10 deep links lock correct topic after login');
})().catch(err => { console.error(err.stack); process.exitCode = 1; });
