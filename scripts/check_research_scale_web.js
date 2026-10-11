"use strict";
// DOM + real in-process HTTP-handler smoke. No sockets or production databases.
// This cannot replace browser rendering at mobile/desktop widths.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cedar-research-dom-'));
const env = {...process.env, SESSIONS_DB:path.join(temp, 'sessions.db'), TURTLE_SOUP_DB:path.join(temp, 'accounts.db')};
const bridge = `
import io, json, sys
from unittest.mock import Mock
from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import server
for config in server.HUMAN_TEST_GAMES.values():
    config['handler'].DB_PATH = str(server.SESSIONS_DB_PATH)
original_account=server._current_account
for line in sys.stdin:
    request=json.loads(line)
    h=object.__new__(server.CedarToyHandler)
    h.path=request['path']; h.client_address=('127.0.0.1',12345)
    raw=json.dumps(request.get('body')).encode() if 'body' in request else b''
    h.headers={'Content-Length':str(len(raw)), **request.get('headers',{})}
    server._current_account=original_account
    if h.headers.get('Authorization') == 'Bearer fixture-ai':
        server._current_account=lambda token: {'id':99,'is_ai':True}
    h.rfile=io.BytesIO(raw); h.wfile=io.BytesIO()
    h.send_response=Mock(); h.send_header=Mock(); h.end_headers=Mock()
    if 'body' in request: h.do_POST()
    else: h.do_GET()
    print(json.dumps({'status':h.send_response.call_args.args[0], 'body':h.wfile.getvalue().decode()}),flush=True)
`;
const child=spawn('python3',['-u','-c',bridge], {cwd:root,env,stdio:['pipe','pipe','inherit']});
const queue=[];
readline.createInterface({input:child.stdout}).on('line',line=>queue.shift().resolve(JSON.parse(line)));
const exited=new Promise(resolve=>child.on('exit',code=>{
  queue.splice(0).forEach(p=>p.reject(new Error('bridge exited '+code)));resolve();
}));
function request(url, body, headers={}) {
  return new Promise((resolve,reject)=>{
    queue.push({resolve,reject});
    child.stdin.write(JSON.stringify({path:url,headers,...(body===undefined?{}:{body})})+'\n');
  });
}
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const requests=[];
async function until(test,label){
  for(let i=0;i<400;i++){if(test())return;await tick();}
  throw new Error('timeout: '+label);
}
async function page(game, saved={}, options={}) {
  const response=await request('/'+game);
  assert.equal(response.status,200);
  const dom=new JSDOM(response.body,{url:'https://fixture.invalid/'+game,runScripts:'dangerously',pretendToBeVisual:true,
    beforeParse(window){
      Object.defineProperty(window, 'innerWidth', {value:options.width || 1280});
      window.HTMLElement.prototype.scrollIntoView=function(){};
      window.confirm=()=>options.confirm ?? false;
      for(const [key,value] of Object.entries(saved)) window.localStorage.setItem(key,value);
      window.fetch=async (url,opts)=>{
        const body=JSON.parse(opts.body);
        requests.push({url,body});
        if(url.endsWith('/save_answer') && options.waitAnswer) await options.waitAnswer;
        if(url.endsWith('/save_answer') && options.failAnswer) {
          options.failAnswer=false;
          return {ok:false,status:503,json:async()=>({error:'保存失败'})};
        }
        const result=await request(url,body,opts.headers);
        if(options.loseAnswer && url.endsWith('/save_answer')) { options.loseAnswer=false; throw new Error('response lost'); }
        return {ok:result.status===200,status:result.status,json:async()=>JSON.parse(result.body)};
      };
    }});
  await until(()=>dom.window.eval('!busy') && dom.window.document.getElementById('statusText').textContent!=='SYSTEM / CONNECTING','initial state');
  return dom;
}
function storage(dom){return {...dom.window.localStorage};}
const $=(dom,id)=>dom.window.document.getElementById(id);
async function choose(dom,option=0){
  dom.window.document.querySelectorAll('#answers button')[option].click();
  if(!$(dom,'saveAnswerButton').hidden) $(dom,'saveAnswerButton').click();
  await until(()=>!dom.window.eval('busy'),'answer saved');
}
async function homeTitles(width, query="") {
  const errors=[];
  const virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError',error=>errors.push(error.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{
    url:'https://fixture.invalid/'+query,runScripts:'dangerously',pretendToBeVisual:true,virtualConsole,
    beforeParse(window) {
      window.innerWidth=width;
      window.matchMedia=query=>({matches:query.includes('max-width: 599px') && width<600});
      window.fetch=async url=>({ok:true,status:200,json:async()=>String(url).includes('rooms')?[]:{}});
    }
  });
  try {
    await tick();
    const d=dom.window.document;
    const expected=query==='?tab=test'?'test':'mini';
    assert.equal(dom.window.eval('filter'),expected);
    assert.equal(d.querySelector('.tab-button.active').dataset.filter,expected);
    assert.equal(dom.window.eval('games.filter(g=>g.testGroup==="deeptalk").length'),10);
    d.querySelector('[data-filter="test"]').click();
    const names=JSON.parse(dom.window.eval('JSON.stringify(Object.fromEntries(researchScales.map(([id,name])=>[id,name])))'));
    assert.equal(Object.keys(names).length,18);
    const examples={irrc:'恋爱关系能力 IRRC',jrrs:'恋爱嫉妒 JRRS',rbi:'伴侣关系互动 RBI',ross:'恋爱求支持 RoSS',rss:'恋爱自我破坏 RSS',bsr:'伪深刻话语测试'};
    for(const [game,name] of Object.entries(names)) {
      if(examples[game]) assert.equal(name,examples[game]);
      assert.doesNotMatch(name,/描述版|娱乐改编版/);
      assert.equal(d.querySelector(`[data-game="${game}"] .game-title`).textContent,name);
      assert.equal(dom.window.eval(`games.find(g=>g.id===${JSON.stringify(game)}).url`),'/'+game);
    }
    for(const status of ['empty','insufficient','ready']) {
      const distributions=Object.fromEntries(Object.keys(names).map(game=>[game,{
        status,sample_size:20,dimensions:status==='ready'?[{label:'维度',buckets:[{result:'1–2',count:20}]}]:[]
      }]));
      dom.window.eval(`platformStatsState={data:{research_score_distributions:${JSON.stringify(distributions)}},loading:false,error:''};renderPlatformStats();`);
      for(const [game,name] of Object.entries(names)) {
        assert.equal(d.querySelector(`.platform-dist-${game} summary`).textContent,`${name} · 分数分布${status==='ready'?' · 20份':''}`);
      }
    }
    assert.deepEqual(errors,[]);
    return names;
  } finally { dom.window.close(); }
}
function expectedNotice(game) {
  if (['rbi','irrc','jrrs','ross','rss','ppts','bsfl'].includes(game)) return '非官方评分，仅供娱乐参考。';
  if (game==='bsr') return '题目经改编，仅供娱乐参考。';
  if (game==='aidep') return '仅供人类，按原量表计分，仅供娱乐参考。';
  return '按原量表计分，仅供娱乐参考。';
}
function assertResearchResult(dom, spec) {
  const data=JSON.parse(dom.window.eval('JSON.stringify(activeState.result_data)'));
  assert.equal($(dom,'resultText').hidden,true);
  assert.equal($(dom,'resultStructured').hidden,false);
  assert.deepEqual([...dom.window.document.querySelectorAll('.research-score dd .research-score-value')].map(n=>n.textContent),Object.values(data.scores).map(n=>n.toFixed(2)));
  for (const [key, bounds] of Object.entries(data.score_ranges)) {
    const order = Object.keys(data.scores).indexOf(key);
    const shown = dom.window.document.querySelectorAll('.research-score dd')[order].textContent;
    assert.ok(shown.includes(bounds.kind==='difference' ? `范围 ${bounds.min}～${bounds.max}` : ` / ${bounds.max}`), `${spec.game}/${key} range`);
  }
  assert.equal(dom.window.document.querySelectorAll('.research-report-footnote').length,0,'no dense report disclaimer');
  assert.equal($(dom,'pageDisclaimer').textContent,expectedNotice(spec.game));
  assert.equal(dom.window.document.querySelectorAll('.research-result .research-notice').length,0);
  assert.equal(dom.window.document.querySelector('.research-result a').href,spec.source);
  const hasReport=Boolean(data.interpretation);
  assert.equal(dom.window.document.querySelectorAll('.research-interpretation').length,hasReport?1:0);
  assert.equal(dom.window.document.querySelectorAll('.rbi-interpretation').length,spec.game==='rbi'&&!hasReport?1:0);
  assert.doesNotMatch($(dom,'resultStructured').textContent,/存档身份|小机作答|原研究对象/);
  assert.ok(dom.window.eval('activeState.result').startsWith(`【${spec.title}】`),'backend formal title retained');
  assert.ok(dom.window.eval('activeState.notice').includes(spec.notice),'backend notice retained in metadata');
  if(spec.scoring_note) assert.equal(data.scoring_note,spec.scoring_note);
}
async function checkRbiInterpretation(){
  const dom=await page('rbi');
  try {
    // Actual engine result for 20/7, 25/7, 18/4, using the existing temporary DB bridge.
    await dom.window.eval(`(async()=>{
      renderState(await api('start',{mode:'full_fast',restart:true,confirm:true,session_id:activeState.session_id}));
      renderState(await api('answer_batch',{answers:[3,3,3,3,3,3,2,4,4,4,4,3,3,3,4,4,5,5]}));
    })()`);
    const d=dom.window.document;
    assert.deepEqual([...d.querySelectorAll('.research-score dd')].map(n=>n.textContent),['2.86 / 5','3.57 / 5','4.50 / 5']);
    const serverReport=JSON.parse(dom.window.eval('JSON.stringify(activeState.result_data.interpretation)'));
    if (serverReport) {
      assert.equal(d.querySelector('.research-interpretation .research-summary').textContent,serverReport.summary);
      assert.equal(d.querySelectorAll('.research-dimension').length,3);
      assert.equal(d.querySelectorAll('.research-actions li').length,serverReport.recommendations.length);
      assert.match(serverReport.summary,/需求|沟通|关心|回应/);
    }
    // Keep the independent RBI client fallback tested for older API responses.
    dom.window.eval(`renderResult({...activeState.result_data,interpretation:null},'')`);
    const paragraphs=[...d.querySelectorAll('.rbi-interpretation p:not(.research-help)')].map(n=>n.textContent);
    assert.equal(paragraphs.length,4);
    assert.match(paragraphs[0],/中间选项.*双方都舒服/);
    assert.match(paragraphs[1],/共同期待.*分工.*“做到了”/);
    assert.match(paragraphs[2],/非常符合.*可行回应/);
    assert.match(paragraphs[3],/4.50.*2.86.*1.64.*需求.*关心/);
    assert.equal(d.querySelectorAll('.research-result .research-notice').length,0);
    assert.match(d.querySelector('.rbi-interpretation .research-help').textContent,/平台反思建议，并非作者个人评分报告/);
    const describe=scores=>JSON.parse(dom.window.eval(`JSON.stringify(describeRbiScores(${JSON.stringify(scores)}))`));
    const score=(sai,fod,openness)=>({sai,fod,openness});
    const equal=[1,3,5].map(n=>describe(score(n,n,n)));
    assert.ok(equal.every(r=>r.observation.includes('不必分出主次')));
    assert.equal(new Set(equal.map(r=>r.observation)).size,3);
    for(let i=0;i<3;i++) assert.equal(new Set(equal.map(r=>r.dimensions[i].text)).size,3);
    const permutations=[[1,3,5],[1,5,3],[3,1,5],[3,5,1],[5,1,3],[5,3,1]];
    const directions=permutations.map(v=>describe(score(...v)).observation);
    assert.equal(new Set(directions.map(t=>t.split('；')[1])).size,6,'six meaningful comparison directions');
    for (const values of [[5,5,1],[5,1,5],[1,5,5],[5,1,1],[1,5,1],[1,1,5]]) {
      assert.match(describe(score(...values)).observation,/并列/);
    }
    for (const values of [[3,3.1,3.2],[4.8,5,4.9],[1,1.1,1.2]]) {
      assert.match(describe(score(...values)).observation,/不必分出主次/);
      assert.doesNotMatch(describe(score(...values)).observation,/更突出|相对少/);
    }
    assert.match(describe(score(3,3.3,3.6)).observation,/相邻分数仍较接近/);
    assert.match(describe(score(1,4.8,5)).observation,/较接近/);
    assert.match(describe(score(1,1.2,5)).observation,/较接近/);
    // Every anchor combination, immutability, finite output, no invented grading/diagnosis.
    dom.window.eval(`for(let sai=1;sai<=5;sai++) for(let fod=1;fod<=5;fod++) for(let openness=1;openness<=5;openness++) {
      const scores=Object.freeze({sai,fod,openness});
      const result=describeRbiScores(scores);
      if(result.dimensions.length!==3 || /undefined|NaN|高分|低分|诊断|关系质量差|伴侣不满意/.test(JSON.stringify(result))) throw Error('invalid RBI copy');
    }`);
    for(const expression of ['null','{}','{sai:3,fod:3}','{sai:null,fod:3,openness:3}',
      '{sai:"3",fod:3,openness:3}','{sai:NaN,fod:3,openness:3}','{sai:Infinity,fod:3,openness:3}',
      '{sai:0,fod:3,openness:3}','{sai:3,fod:6,openness:3}']) {
      assert.equal(dom.window.eval(`describeRbiScores(${expression})`),null);
    }
    dom.window.eval(`renderResult({...activeState.result_data,interpretation:null,scores:{sai:3,fod:3}},'')`);
    assert.equal(d.querySelector('.rbi-interpretation'),null,'partial dimensions keep scores without inventing interpretation');
    assert.equal(d.querySelectorAll('.research-score').length,2);
    dom.window.eval(`renderResult({...activeState.result_data,interpretation:null,scores:{sai:0,fod:3,openness:3}},'')`);
    assert.equal(d.querySelector('.rbi-interpretation'),null);
    assert.equal(d.querySelectorAll('.research-score').length,3);
    dom.window.eval(`renderResult({...activeState.result_data,kind:'other'},'')`);
    assert.equal(d.querySelector('.rbi-interpretation'),null);
    console.log('PASS: RBI actual 2.86/3.57/4.50, six orders, ties, small gaps, 125 anchor combinations, invalid/missing scores and kind guard.');
  } finally {dom.window.close();}
}
async function main(){
  await checkRbiInterpretation();
  const games=fs.readdirSync(path.join(root,'research_scales/data')).filter(n=>n.endsWith('.json'))
    .map(n=>JSON.parse(fs.readFileSync(path.join(root,'research_scales/data',n),'utf8'))).filter(s=>!s.blocked);
  assert.equal(games.length,18);
  let names;
  for(const width of [360,430,1280]) {
    for(const query of ["", "?tab=test", "?tab=mini", "?tab=invalid"]) names=await homeTitles(width,query);
  }
  for(const spec of games){
    let dom=await page(spec.game), d=dom.window.document;
    assert.equal($(dom,'title').textContent,names[spec.game]);
    assert.equal(d.title,`${names[spec.game]} · CEDAR TOY`);
    assert.ok([...d.querySelectorAll('.brand, .back-link, #resultPanel .actions a')].every(a=>a.getAttribute('href')==='/?tab=test'));
    assert.doesNotMatch($(dom,'questionIntro').textContent,/自动保存|不可修改|续答旧版/);
    assert.equal(dom.window.eval('CONFIG.title'),spec.title,'formal title stays intact');
    assert.equal(d.querySelectorAll('.mode-button').length,0,spec.game);
    assert.equal($(dom,'modePanel').hidden,true);
    assert.equal($(dom,'questionPanel').hidden,false);
    assert.equal($(dom,'pageDisclaimer').textContent,expectedNotice(spec.game));
    assert.equal($(dom,'researchDetails').open,false);
    assert.doesNotMatch($(dom,'researchDetails').textContent,/小机作答|4399|不作诊断|原研究对象|常模|效度/);
    assert.equal($(dom,'researchDetails').querySelector('summary').textContent,'来源与许可');
    assert.ok($(dom,'researchDetails').contains($(dom,'source')));
    const rows=[...$(dom,'source').querySelectorAll('dd')];
    assert.equal(rows.length,4);
    assert.equal(rows[0].textContent,spec.author,'full author credit retained');
    assert.equal(rows[1].textContent,spec.source);
    assert.equal(rows[2].textContent,spec.license.replace(/\s*(?:—\s*)?https?:\/\/[^\s；]+/g,''),'license name/restrictions retained');
    const licenseURL=spec.license.match(/https?:\/\/[^\s；]+/);
    if(licenseURL) assert.equal(rows[2].querySelector('a').href,licenseURL[0]);
    assert.ok(rows[2].querySelector('a'),'clickable license');
    if(spec.license_source) assert.equal(rows[2].querySelector('a').href,spec.license_source);
    assert.ok(rows[3].textContent.length<40,'one short translation/adaptation line');
    assert.ok([...$(dom,'source').querySelectorAll('a')].some(a=>a.href===spec.source));
    for(const a of $(dom,'source').querySelectorAll('a')) {
      assert.match(a.href,/^https?:\/\//);
      assert.equal(a.rel,'noopener noreferrer');
    }
    assert.equal($(dom,'researchSaveHint'),null);
    assert.equal($(dom,'saveAnswerButton').textContent,'下一题');
    assert.equal($(dom,'saveAnswerButton').disabled,true);
    const buttons=[...d.querySelectorAll('#answers button')];
    assert.equal(buttons.length,spec.options.length);
    // Computed CSS catches cascade regressions; jsdom cannot measure geometry.
    const css=node=>dom.window.getComputedStyle(node);
    assert.equal(css($(dom,'pageDisclaimer')).fontSize,'12px');
    assert.equal(css($(dom,'questionIntro')).fontSize,'13px');
    assert.equal(css($(dom,'saveAnswerButton')).minHeight,'44px');
    const track=d.querySelector('.research-scale-track');
    if(track) {
      assert.equal(css(d.querySelector('.research-scale-ends')).fontSize,'12px');
      assert.equal(css(track).gridTemplateColumns,spec.options.length<=7
        ? `repeat(${spec.options.length}, minmax(0, 1fr))` : 'repeat(auto-fit, minmax(44px, 1fr))');
      assert.equal(css(buttons[0]).minHeight,'48px');
      assert.equal(css(buttons[0]).fontSize,'13px');
    } else {
      assert.equal(css(buttons[0]).minHeight,'44px');
      assert.equal(css(buttons[0]).fontSize,'14px');
    }
    spec.options.forEach((option,i)=>{
      if(String(option.value)===String(option.text)) {
        assert.equal(buttons[i].textContent,String(option.value));
        assert.equal(buttons[i].getAttribute('aria-label'),null,'no invented middle labels');
      } else {
        assert.ok(buttons[i].textContent.includes(option.text) || buttons[i].getAttribute('aria-label').includes(option.text));
      }
    });
    // All 18 real pages: change a not-yet-submitted choice, restore it, commit,
    // reload and revisit a server receipt, then complete and restore the report.
    d.querySelectorAll('#answers button')[1].click();
    d.querySelectorAll('#answers button')[0].click();
    let saved=storage(dom);dom.window.close();dom=await page(spec.game,saved);d=dom.window.document;
    assert.equal(d.querySelector('#answers button').classList.contains('selected'),true);
    await choose(dom);
    saved=storage(dom);dom.window.close();dom=await page(spec.game,saved);d=dom.window.document;
    assert.match($(dom,'progressLabel').textContent,new RegExp('2 / '+spec.items.length));
    $(dom,'previousButton').click();
    assert.ok(d.querySelector('#answers button.selected'));
    assert.ok([...d.querySelectorAll('#answers button')].every(b=>!b.disabled));
    assert.equal($(dom,'researchSaveHint'),null);
    assert.equal($(dom,'modeLabel').textContent,'全题作答');
    $(dom,'saveAnswerButton').click();await until(()=>!dom.window.eval('busy'),'revisit next');
    assert.equal($(dom,'questionIntro').hidden,true);
    await choose(dom,1); // second question
    d.querySelectorAll('#answers button')[2].click(); // third, not yet advanced
    $(dom,'previousButton').click();await until(()=>!dom.window.eval('busy'),'save third before going back');
    $(dom,'previousButton').click();
    d.querySelectorAll('#answers button')[1].click();
    $(dom,'saveAnswerButton').click();await until(()=>!dom.window.eval('busy'),'edit first');
    $(dom,'saveAnswerButton').click();await until(()=>!dom.window.eval('busy'),'return to third');
    assert.equal(dom.window.eval('answers[2]'),spec.options[2].value,'third selection survives first edit');
    saved=storage(dom);dom.window.close();dom=await page(spec.game,saved);d=dom.window.document;
    assert.equal(dom.window.eval('cursor'),2);
    assert.deepEqual(JSON.parse(dom.window.eval('JSON.stringify(answers.slice(0,3))')),[spec.options[1].value,spec.options[1].value,spec.options[2].value]);
    assert.equal(d.querySelector('#answers button.selected').getAttribute('aria-pressed'),'true');
    for(let i=2;i<spec.items.length;i++) {
      assert.equal($(dom,'saveAnswerButton').textContent,i===spec.items.length-1?'查看结果':'下一题');
      await choose(dom);
    }
    assert.equal($(dom,'resultPanel').hidden,false);
    assertResearchResult(dom,spec);
    const result=$(dom,'resultStructured').textContent;
    assert.equal($(dom,'compareBox').hidden,true);
    saved=storage(dom);dom.window.close();dom=await page(spec.game,saved);
    assertResearchResult(dom,spec);
    assert.equal($(dom,'resultStructured').textContent,result);
    const before=requests.length;
    $(dom,'restartButton').click();
    assert.equal(requests.length,before,'cancel retest is read-only');
    dom.window.confirm=()=>true;
    $(dom,'restartButton').click();await until(()=>!dom.window.eval('busy'),'retest');
    assert.equal($(dom,'questionPanel').hidden,false);
    assert.equal(requests.at(-1).body.confirm,true);
    assert.equal(requests.at(-1).body.restart,true);
    assert.equal(requests.at(-1).body.mode,'full');
    dom.window.close();
  }
  // Legacy full_fast record and original-format draft: no reset or conversion.
  const player='guest:weblegacy';
  const state=JSON.parse((await request('/api/romlon/start',{player_id:player,mode:'full_fast'})).body);
  const key='cedartoy_romlon_web_draft_'+player;
  const raw=JSON.stringify({game:'romlon',edition:'full_fast',session_id:state.session_id,total:4,answers:[1,2,null,null],cursor:2});
  let dom=await page('romlon',{cedartoy_guest_web_player_id:player,[key]:raw});
  assert.doesNotMatch($(dom,'questionIntro').textContent,/旧版|只存本机|自动保存|不可修改/);
  assert.match($(dom,'progressLabel').textContent,/3 \/ 4/);
  $(dom,'previousButton').click();await choose(dom,2);
  let saved=storage(dom);dom.window.close();dom=await page('romlon',saved);
  assert.equal(dom.window.eval('answers[1]'),3);
  assert.equal(dom.window.localStorage.getItem(key),raw,'legacy original kept verbatim');
  await choose(dom);await choose(dom);
  assert.equal($(dom,'reviewPanel').hidden,false);
  $(dom,'modifyButton').click();$(dom,'previousButton').click();await choose(dom,1);
  $(dom,'submitButton').click();await until(()=>!dom.window.eval('busy'),'legacy submit');
  assert.equal($(dom,'resultPanel').hidden,false);
  assert.equal(requests.at(-1).body.session_id,state.session_id);
  assert.deepEqual(requests.at(-1).body.answers,[1,3,2,1]);
  dom.window.close();
  // Mismatched and malformed drafts never silently replaced or deleted.
  for(const raw of ['{broken',JSON.stringify({game:'romlon',edition:'full_fast',session_id:'old',total:4,answers:[1,2,null,null]})]){
    dom=await page('romlon',{[key]:raw,cedartoy_guest_web_player_id:player});
    assert.equal(dom.window.localStorage.getItem(key),raw);
    assert.equal($(dom,'draftNotice').hidden,false);
    assert.ok(decodeURIComponent($(dom,'draftNotice').querySelector('a').href).includes(JSON.stringify(raw)));
    dom.window.close();
  }
  // A lost answer response can be retried without double advancing.
  dom=await page('romlon',{}, {loseAnswer:true});await choose(dom);
  assert.match($(dom,'error').textContent,/未确认保存/);
  assert.equal(dom.window.eval('cursor'),0);
  assert.equal(dom.window.eval('activeState.progress'),0);
  assert.ok(dom.window.document.querySelector('#answers button.selected'));
  $(dom,'saveAnswerButton').click();await until(()=>!dom.window.eval('busy'),'idempotent retry');
  assert.equal(dom.window.eval('activeState.progress'),1);
  dom.window.close();
  // Real server writes are gated behind success, at mobile and desktop DOM sizes.
  for(const width of [360,430,1280]) {
    let release;
    const opts={width,failAnswer:true,waitAnswer:new Promise(resolve=>{release=resolve;})};
    dom=await page('romlon',{},opts);
    dom.window.document.querySelector('#answers button').click();
    assert.equal(dom.window.document.activeElement,dom.window.document.querySelector('#answers button.selected'));
    const before=requests.filter(r=>r.url.endsWith('/save_answer')).length;
    $(dom,'saveAnswerButton').click();$(dom,'saveAnswerButton').click();
    assert.equal(dom.window.eval('busy'),true);
    assert.equal(dom.window.eval('cursor'),0,'no optimistic navigation');
    assert.ok([...dom.window.document.querySelectorAll('#questionPanel button')].every(b=>b.disabled));
    assert.equal(requests.filter(r=>r.url.endsWith('/save_answer')).length,before+1,'double click sends once');
    release();await until(()=>!dom.window.eval('busy'),'failed save');
    assert.equal(dom.window.eval('cursor'),0);
    assert.equal($(dom,'saveAnswerButton').disabled,false);
    assert.match($(dom,'error').textContent,/保存失败/);
    saved=storage(dom);dom.window.close();dom=await page('romlon',saved,{width});
    assert.equal(dom.window.eval('activeState.progress'),0);
    assert.equal(dom.window.document.querySelector('#answers button.selected').textContent,'1');
    $(dom,'saveAnswerButton').click();await until(()=>!dom.window.eval('busy'),'retry succeeds');
    assert.equal(dom.window.eval('activeState.progress'),1);
    assert.equal(dom.window.eval('cursor'),1);
    assert.equal(dom.window.document.activeElement,$(dom,'questionText'));
    dom.window.close();
  }
  // A local choice conflicting with another tab's saved answer stays downloadable.
  const conflictPlayer='guest:webconflict';
  const conflictState=JSON.parse((await request('/api/romlon/start',{player_id:conflictPlayer,mode:'full'})).body);
  await request('/api/romlon/answer',{player_id:conflictPlayer,session_id:conflictState.session_id,expected_index:0,answer:1});
  const conflictKey=`cedartoy_romlon_web_draft_${conflictPlayer}_session_${conflictState.session_id}`;
  const conflictRaw=JSON.stringify({game:'romlon',edition:'full',session_id:conflictState.session_id,total:4,answers:[2,null,null,null],cursor:0});
  dom=await page('romlon',{cedartoy_guest_web_player_id:conflictPlayer,[conflictKey]:conflictRaw});
  assert.equal(dom.window.eval('answers[0]'),1,'server receipt wins for display');
  assert.equal($(dom,'draftNotice').hidden,false);
  await choose(dom);
  const archived=Object.entries(storage(dom)).find(([key])=>key.startsWith(conflictKey+'_conflict_'));
  assert.equal(archived[1],conflictRaw,'conflicting selection preserved before next write');
  dom.window.close();
  // Two live tabs: a stale first-answer edit cannot overwrite the latest value.
  dom=await page('rbi');await choose(dom);
  const shared=storage(dom);
  const other=await page('rbi',shared);
  $(dom,'previousButton').click();$(other,'previousButton').click();
  await choose(dom,1);
  await choose(other,2);
  assert.match($(other,'error').textContent,/其他页面更新/);
  assert.equal(other.window.eval('cursor'),0);
  assert.equal(other.window.eval('answers[0]'),3,'failed edit stays selected for review');
  const fresh=await page('rbi',storage(dom));
  assert.equal(fresh.window.eval('answers[0]'),2,'latest server answer remains intact');
  const conflictStorage=storage(other);other.window.close();
  const recovered=await page('rbi',conflictStorage);
  assert.equal(recovered.window.eval('answers[0]'),2);
  assert.equal($(recovered,'draftNotice').hidden,false,'conflicting local edit remains downloadable');
  recovered.window.close();fresh.window.close();dom.window.close();
  // No authenticated-to-guest replay on an expired login.
  const before=requests.length;
  dom=await page('romlon',{cedartoy_token:'expired'});
  assert.match($(dom,'error').textContent,/登录已失效/);
  assert.equal(requests.length,before+1);
  assert.equal(dom.window.localStorage.getItem('cedartoy_token'),'expired');dom.window.close();
  dom=await page('aidep',{cedartoy_token:'fixture-ai'});
  assert.match($(dom,'error').textContent,/人类/);
  assert.equal($(dom,'questionPanel').hidden,true);dom.window.close();
  // All seven original tests sharing this template still auto-advance locally,
  // allow edits, then batch-submit. The eighth has its own untouched page.
  for(const game of ['mbti','enneagram','dnd','love','ecr','humanity','sins_virtues']){
    dom=await page(game);
    assert.equal($(dom,'title').textContent,dom.window.eval('CONFIG.title'));
    assert.ok([...dom.window.document.querySelectorAll('.brand, .back-link, #resultPanel .actions a')].every(a=>a.getAttribute('href')==='/?tab=test'));
    assert.equal(dom.window.document.title,dom.window.eval('CONFIG.title')+' · CEDAR TOY');
    assert.equal($(dom,'error').hidden,true,'legacy test database is isolated and readable');
    if(['mbti','enneagram'].includes(game)) {
      assert.equal($(dom,'modePanel').hidden,false);
      assert.equal(dom.window.document.querySelectorAll('.mode-button').length,2);
      dom.window.document.querySelector('.mode-button').click();
      await until(()=>!dom.window.eval('busy'),'old mode start');
    }
    assert.equal($(dom,'researchDetails').hidden,true);
    assert.equal($(dom,'saveAnswerButton').hidden,true);
    const before=requests.length;
    const total=dom.window.eval('activeState.total');
    dom.window.document.querySelector('#answers button').click();
    assert.equal(dom.window.eval('cursor'),1,'old selection auto-advances');
    $(dom,'previousButton').click();
    assert.equal(dom.window.document.querySelector('#answers button.selected').disabled,false);
    dom.window.document.querySelectorAll('#answers button')[1].click();
    const changed=dom.window.eval('answers[0]');
    saved=storage(dom);dom.window.close();dom=await page(game,saved);
    assert.equal(dom.window.eval('answers[0]'),changed);
    for(let i=1;i<total;i++) dom.window.document.querySelector('#answers button').click();
    assert.equal($(dom,'reviewPanel').hidden,false);
    assert.equal(requests.length,before+1,'only the reload reads from backend');
    $(dom,'submitButton').click();await until(()=>!dom.window.eval('busy'),'old batch result');
    assert.equal($(dom,'resultPanel').hidden,false,game);
    assert.equal($(dom,'resultStructured').hidden,false,game+' original structured result');
    assert.equal(dom.window.document.querySelector('.research-result'),null);
    assert.ok(requests.at(-1).url.endsWith('/answer_batch'));
    dom.window.close();
  }
  // Untrusted question/source/notice remain plain text, never HTML.
  dom=await page('romlon');
  dom.window.eval(`activeState.questions[0].text='<img src=x onerror="window.xss=1">'; renderQuestion();`);
  assert.equal($(dom,'questionText').querySelector('img'),null);
  dom.window.eval(`setResearchLinkedText($('source'), '<img src=x onerror="window.xss=1"> https://example.org/paper?a=1&b=2；许可');`);
  assert.equal($(dom,'source').querySelector('img'),null);
  assert.equal($(dom,'source').querySelector('a').href,'https://example.org/paper?a=1&b=2');
  dom.window.eval(`renderResult({kind:'research_scale',scores:{zero:0,negative:-2.5},dimensions:{zero:'零值',negative:'<img src=x onerror="window.xss=1">'}}, '原始冗余说明');`);
  assert.deepEqual([...dom.window.document.querySelectorAll('.research-score dd')].map(n=>n.textContent),['0.00','-2.50']);
  assert.equal($(dom,'resultStructured').querySelector('img'),null);
  dom.window.eval(`renderResult({kind:'research_scale',scores:{bad:null},dimensions:{bad:'缺失分数'}}, '原始冗余说明');`);
  assert.equal($(dom,'resultText').textContent,'暂时无法展示分数，请刷新重试。');
  assert.equal(dom.window.xss,undefined);dom.window.close();
  console.log('PASS: 18 home/page/browser titles and statistics empty/insufficient/ready states at 360/430/1280 DOM widths; formal titles/notices/scoring preserved; real temporary-DB edit-first/retain-third/reload/report, concurrent tabs/retry, home tab return, numeric labels/CSS, legacy drafts, auth/XSS, seven original template flows. DOM only, no rendered geometry.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  child.stdin.end();await exited;fs.rmSync(temp,{recursive:true,force:true});
});
