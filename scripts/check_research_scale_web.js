"use strict";
// DOM + real in-process HTTP-handler smoke. No sockets or production databases.
// This cannot replace browser rendering at mobile/desktop widths.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cedar-research-dom-'));
const env = {...process.env, SESSIONS_DB:path.join(temp, 'sessions.db'), TURTLE_SOUP_DB:path.join(temp, 'accounts.db')};
const bridge = `
import io, json, sys
from unittest.mock import Mock
from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import server
request=json.loads(sys.argv[1])
h=object.__new__(server.CedarToyHandler)
h.path=request['path']; h.client_address=('127.0.0.1',12345)
raw=json.dumps(request.get('body')).encode() if 'body' in request else b''
h.headers={'Content-Length':str(len(raw)), **request.get('headers',{})}
if h.headers.get('Authorization') == 'Bearer fixture-ai':
    server._current_account=lambda token: {'id':99,'is_ai':True}
h.rfile=io.BytesIO(raw); h.wfile=io.BytesIO()
h.send_response=Mock(); h.send_header=Mock(); h.end_headers=Mock()
if 'body' in request: h.do_POST()
else: h.do_GET()
print(json.dumps({'status':h.send_response.call_args.args[0], 'body':h.wfile.getvalue().decode()}))
`;
function request(url, body, headers={}) {
  const result=spawnSync('python3',['-c',bridge,JSON.stringify({path:url,headers,...(body===undefined?{}:{body})})], {cwd:root,env,encoding:'utf8',timeout:20000});
  assert.equal(result.status,0,result.stderr);
  return JSON.parse(result.stdout);
}
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const requests=[];
function page(game, saved={}) {
  const response=request('/'+game);
  assert.equal(response.status,200);
  return new JSDOM(response.body,{url:'https://fixture.invalid/'+game,runScripts:'dangerously',pretendToBeVisual:true,
    beforeParse(window){
      window.HTMLElement.prototype.scrollIntoView=function(){};
      for(const [key,value] of Object.entries(saved)) window.localStorage.setItem(key,value);
      window.fetch=async (url,options)=>{
        const body=JSON.parse(options.body);
        requests.push({url,body});
        const result=request(url,body,options.headers);
        return {ok:result.status===200,status:result.status,json:async()=>JSON.parse(result.body)};
      };
    }});
}
function storage(dom){return {...dom.window.localStorage};}
async function main(){
  const games=fs.readdirSync(path.join(root,'research_scales/data')).filter(n=>n.endsWith('.json'))
    .map(n=>JSON.parse(fs.readFileSync(path.join(root,'research_scales/data',n),'utf8'))).filter(s=>!s.blocked);
  assert.equal(games.length,18);
  assert.equal(games.reduce((n,s)=>n+s.items.length,0),265);
  for(const spec of games){
    const catalogPage=page(spec.game);await tick();
    const d=catalogPage.window.document;
    assert.equal(d.querySelectorAll('.mode-button').length,2,spec.game);
    assert.match(d.body.textContent,/未验证翻译/);
    assert.match(d.body.textContent,/不作诊断/);
    catalogPage.window.close();
  }
  const bsr=games.find(s=>s.game==='bsr');
  const bsrAnswers=new Map(bsr.items.map((q,i)=>[q.text,i<10?1:5]));
  for(const mode of [0,1]){
    let run=page('bsr');await tick();
    let d=run.window.document;
    d.querySelectorAll('.mode-button')[mode].click();await tick();
    const seen=[];
    for(let i=0;i<20;i++){
      const text=d.getElementById('questionText').textContent;
      seen.push(text);
      d.querySelectorAll('.answer-button')[bsrAnswers.get(text)-1].click();await tick();
      if(i===6){
        const next=d.getElementById('questionText').textContent;
        const saved=storage(run);run.window.close();
        run=page('bsr',saved);await tick();d=run.window.document;
        assert.equal(d.getElementById('questionText').textContent,next,'BSR order survives reload');
        assert.match(d.getElementById('progressLabel').textContent,/8 \/ 20/);
      }
    }
    assert.equal(new Set(seen).size,20,'every shuffled item exactly once');
    if(mode===1){d.getElementById('submitButton').click();await tick();}
    assert.equal(d.getElementById('resultPanel').hidden,false);
    const result=d.getElementById('resultText').textContent;
    assert.match(result,/伪深刻语句评价均分：1\.00/);
    assert.match(result,/励志格言均分：5\.00/);
    assert.match(result,/差值（励志格言 − 伪深刻语句）：4\.00/);
    const saved=storage(run);run.window.close();
    run=page('bsr',saved);await tick();d=run.window.document;
    assert.equal(d.getElementById('resultText').textContent,result,'BSR result survives reload');
    run.window.close();
  }
  let dom=page('romlon');
  await tick();
  let doc=dom.window.document;
  assert.equal(doc.getElementById('modePanel').hidden,false);
  assert.equal(doc.querySelectorAll('.mode-button').length,2);
  doc.querySelector('.mode-button').click(); await tick();
  assert.match(doc.getElementById('modeLabel').textContent,/逐题/);
  assert.equal(doc.getElementById('previousButton').hidden,true);
  doc.querySelector('.answer-button').click(); await tick();
  assert.match(doc.getElementById('progressLabel').textContent,/2 \/ 4.*未答 3/);
  assert.equal(doc.getElementById('progressFill').style.width,'25%');
  const saved=storage(dom); dom.window.close();
  dom=page('romlon',saved); await tick(); doc=dom.window.document;
  assert.match(doc.getElementById('progressLabel').textContent,/2 \/ 4/);
  for(let i=0;i<3;i++){doc.querySelector('.answer-button').click(); await tick();}
  assert.equal(doc.getElementById('resultPanel').hidden,false);
  assert.equal(doc.getElementById('compareBox').hidden,true);
  const sequential=doc.getElementById('resultText').textContent;
  assert.match(sequential,/4\.00/);
  doc.getElementById('restartButton').click();
  doc.querySelectorAll('.mode-button')[1].click(); await tick();
  assert.match(doc.getElementById('modeLabel').textContent,/批量/);
  const n=requests.filter(r=>r.url.endsWith('/answer')).length;
  doc.querySelector('.answer-button').click(); await tick();
  assert.equal(requests.filter(r=>r.url.endsWith('/answer')).length,n);
  const draft=storage(dom);dom.window.close();
  dom=page('romlon',draft);await tick();doc=dom.window.document;
  assert.match(doc.getElementById('progressLabel').textContent,/2 \/ 4.*未答 3/);
  for(let i=0;i<3;i++){doc.querySelector('.answer-button').click();await tick();}
  assert.equal(doc.getElementById('reviewPanel').hidden,false);
  doc.getElementById('modifyButton').click();
  assert.equal(doc.getElementById('questionPanel').hidden,false);
  doc.getElementById('reviewButton').click();
  doc.getElementById('submitButton').click();await tick();
  assert.equal(doc.getElementById('resultPanel').hidden,false);
  assert.match(doc.getElementById('resultText').textContent,/4\.00/);
  assert.equal(requests.filter(r=>r.url==='/api/romlon/answer_batch').length,1);
  assert.equal(requests.at(-1).body.answers.length,4);
  assert.equal(typeof requests.at(-1).body.session_id,'string');
  const history=storage(dom);dom.window.close();
  dom=page('romlon',history);await tick();doc=dom.window.document;
  assert.equal(doc.getElementById('resultPanel').hidden,false);
  assert.match(doc.getElementById('resultText').textContent,/4\.00/);
  dom.window.close();
  for(const [game,count] of [['mesx',10],['bsfl',5]]){
  dom=page(game);await tick();doc=dom.window.document;
  doc.querySelectorAll('.mode-button')[1].click();await tick();
  for(let i=0;i<count;i++){doc.querySelector('.answer-button').click();await tick();}
  doc.getElementById('submitButton').click();await tick();
  assert.equal(doc.getElementById('resultPanel').hidden,false);
  assert.match(doc.getElementById('resultText').textContent,/0\.00/);
  assert.ok(requests.at(-1).body.answers.every(v=>v===0));
  dom.window.close();
  }
  dom=page('aidep',{cedartoy_token:'fixture-ai'});await tick();doc=dom.window.document;
  assert.equal(doc.getElementById('error').hidden,false);
  assert.match(doc.getElementById('error').textContent,/人类/);
  assert.equal(doc.getElementById('questionPanel').hidden,true);
  doc.querySelector('.mode-button').click();await tick();
  assert.equal(doc.getElementById('questionPanel').hidden,true);
  assert.equal(dom.window.localStorage.getItem('cedartoy_token'),'fixture-ai');
  assert.match(doc.getElementById('error').textContent,/人类/);
  dom.window.close();
  console.log('PASS: 18 pages / 265 items, real HTTP handler + DOM, both modes, BSR shuffled scoring/reload, reload/draft/review/history, MESx/BSFL zero, AIDep AI rejection');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>fs.rmSync(temp,{recursive:true,force:true}));
