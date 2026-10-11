// DOM semantics and data preservation; real browser layout is a separate check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const errors=[];
const vc=new VirtualConsole(); vc.on('jsdomError', e=>errors.push(e.message));
const dom=new JSDOM(html,{url:'https://fixture.invalid/',runScripts:'dangerously',virtualConsole:vc,
  beforeParse(w){w.fetch=async()=>({ok:true,json:async()=>({announcements:[],unread_count:0})});}});
const w=dom.window, d=w.document;
(async () => {
try {
  await new Promise(resolve=>setTimeout(resolve,20));
  const rows=[{result:'a',count:2},{result:'b',count:8},{result:'c',count:0}];
  w.fixtureRows=rows;
  w.fixtureResearch={aidep:{status:'ready',sample_size:10,dimensions:[{label:'全量表均分',buckets:[{result:'1.0–1.8',count:2},{result:'1.8–2.6',count:8}]}]},bsr:{status:'insufficient',dimensions:[]}};
  w.eval(`platformStatsState={loading:false,error:'',data:{save_counts:{},test_result_distributions:Object.fromEntries(Object.keys(platformTestLabels).map(k=>[k,fixtureRows])),research_score_distributions:fixtureResearch}};renderPlatformStats();`);
  const blocks=[...d.querySelectorAll('details.platform-dist-block')];
  const oldCount=w.eval('Object.keys(platformTestLabels).length');
  const newCount=w.eval('Object.keys(platformResearchLabels).length');
  assert.equal(newCount,18,'all 18 scored scales, including human-only AIDep');
  assert.equal(blocks.length,oldCount+newCount);
  assert.ok(blocks.length>0);
  for(const block of blocks.slice(0,oldCount)){
    assert.equal(block.open,false);
    const title=block.firstElementChild;
    assert.equal(title.tagName,'SUMMARY');
    assert.equal(w.getComputedStyle(title).minHeight,'44px');
    assert.deepEqual([...block.querySelectorAll('.platform-dist-row b')].map(n=>n.textContent),['8','2','0']);
    assert.deepEqual([...block.querySelectorAll('.platform-dist-track span')].map(n=>n.style.width),['100%','25%','4%']);
    title.click(); assert.equal(block.open,true);
    block.querySelector('.platform-dist-body').click(); assert.equal(block.open,true);
    title.click(); assert.equal(block.open,false);
  }
  for(const block of blocks.slice(oldCount)) assert.equal(block.open,false);
  const scored=d.querySelector('.platform-dist-aidep');
  assert.match(scored.querySelector('summary').textContent,/AI 依赖倾向.*10份/);
  assert.deepEqual([...scored.querySelectorAll('.platform-dist-row b')].map(x=>x.textContent),['2','8']);
  assert.deepEqual([...scored.querySelectorAll('.platform-dist-track span')].map(x=>x.style.width),['25%','100%']);
  assert.match(d.querySelector('.platform-dist-bsr').textContent,/样本不足/);
  assert.match(d.querySelector('.platform-dist-irrc').textContent,/暂无数据/);
  assert.equal(d.querySelectorAll('.platform-dist-aidep').length,1);
  assert.ok([...d.querySelectorAll('.platform-stat-card,.platform-stat-note')].every(n=>!n.closest('details')));
  assert.match(d.querySelector('.platform-stat-note').textContent,/仅展示聚合/);
  const empty=d.createElement('div');empty.innerHTML=w.renderDistribution('mbti',[]);
  assert.equal(empty.querySelector('details').open,false);assert.match(empty.textContent,/暂无数据/);
  assert.deepEqual(rows,[{result:'a',count:2},{result:'b',count:8},{result:'c',count:0}]);
  assert.deepEqual(errors,[]);
  console.log('PASS: native independent closed details; title-only toggle, 44px summaries, unchanged counts/order/bar widths, summary/privacy outside folds, empty state.');
} finally {w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
