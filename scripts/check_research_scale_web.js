"use strict";
// DOM + real in-process HTTP-handler smoke. No sockets or production databases.
// This cannot replace browser rendering at mobile/desktop widths.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
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
server.mbti_handler.DB_PATH = str(server.SESSIONS_DB_PATH)
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
      window.HTMLElement.prototype.scrollIntoView=function(){};
      window.confirm=()=>options.confirm ?? false;
      for(const [key,value] of Object.entries(saved)) window.localStorage.setItem(key,value);
      window.fetch=async (url,opts)=>{
        const body=JSON.parse(opts.body);
        requests.push({url,body});
        const result=await request(url,body,opts.headers);
        if(options.loseAnswer && url.endsWith('/answer')) { options.loseAnswer=false; throw new Error('response lost'); }
        return {ok:result.status===200,status:result.status,json:async()=>JSON.parse(result.body)};
      };
    }});
  await until(()=>dom.window.eval('!busy') && dom.window.document.getElementById('statusText').textContent!=='SYSTEM / CONNECTING','initial state');
  return dom;
}
function storage(dom){return {...dom.window.localStorage};}
const $=(dom,id)=>dom.window.document.getElementById(id);
async function choose(dom,option=0){
  dom.window.document.querySelectorAll('.answer-button')[option].click();
  if(!$(dom,'saveAnswerButton').hidden) $(dom,'saveAnswerButton').click();
  await until(()=>!dom.window.eval('busy'),'answer saved');
}
async function main(){
  const games=fs.readdirSync(path.join(root,'research_scales/data')).filter(n=>n.endsWith('.json'))
    .map(n=>JSON.parse(fs.readFileSync(path.join(root,'research_scales/data',n),'utf8'))).filter(s=>!s.blocked);
  assert.equal(games.length,18);
  for(const spec of games){
    let dom=await page(spec.game), d=dom.window.document;
    assert.equal(d.querySelectorAll('.mode-button').length,0,spec.game);
    assert.equal($(dom,'modePanel').hidden,true);
    assert.equal($(dom,'questionPanel').hidden,false);
    assert.equal($(dom,'pageDisclaimer').textContent,'仅供娱乐/自我了解，不作诊断');
    assert.equal($(dom,'researchDetails').open,false);
    assert.ok($(dom,'researchDetails').textContent.includes(spec.notice));
    assert.ok($(dom,'researchDetails').contains($(dom,'source')));
    // All 18 real pages: change a not-yet-submitted choice, restore it, commit,
    // reload and revisit a server receipt, then complete and restore the report.
    d.querySelectorAll('.answer-button')[1].click();
    d.querySelectorAll('.answer-button')[0].click();
    let saved=storage(dom);dom.window.close();dom=await page(spec.game,saved);d=dom.window.document;
    assert.equal(d.querySelector('.answer-button').classList.contains('selected'),true);
    await choose(dom);
    saved=storage(dom);dom.window.close();dom=await page(spec.game,saved);d=dom.window.document;
    assert.match($(dom,'progressLabel').textContent,new RegExp('2 / '+spec.items.length));
    $(dom,'previousButton').click();
    assert.ok(d.querySelector('.answer-button.selected'));
    assert.ok([...d.querySelectorAll('.answer-button')].every(b=>b.disabled));
    assert.match($(dom,'researchSaveHint').textContent,/不能修改/);
    $(dom,'nextSavedButton').click();
    for(let i=1;i<spec.items.length;i++) await choose(dom);
    assert.equal($(dom,'resultPanel').hidden,false);
    const result=$(dom,'resultText').textContent;
    assert.ok(result.includes(spec.notice));
    assert.equal($(dom,'compareBox').hidden,true);
    saved=storage(dom);dom.window.close();dom=await page(spec.game,saved);
    assert.equal($(dom,'resultText').textContent,result);
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
  assert.match($(dom,'researchSaveHint').textContent,/旧版.*只存本机/);
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
  $(dom,'saveAnswerButton').click();await until(()=>!dom.window.eval('busy'),'idempotent retry');
  assert.equal(dom.window.eval('activeState.progress'),1);
  dom.window.close();
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
  // No authenticated-to-guest replay on an expired login.
  const before=requests.length;
  dom=await page('romlon',{cedartoy_token:'expired'});
  assert.match($(dom,'error').textContent,/登录已失效/);
  assert.equal(requests.length,before+1);
  assert.equal(dom.window.localStorage.getItem('cedartoy_token'),'expired');dom.window.close();
  dom=await page('aidep',{cedartoy_token:'fixture-ai'});
  assert.match($(dom,'error').textContent,/人类/);
  assert.equal($(dom,'questionPanel').hidden,true);dom.window.close();
  // Existing mode choices remain on legacy tests.
  for(const game of ['mbti','enneagram']){
    dom=await page(game);
    assert.equal($(dom,'error').hidden,true,'legacy test database is isolated and readable');
    assert.equal($(dom,'modePanel').hidden,false);
    assert.equal(dom.window.document.querySelectorAll('.mode-button').length,2);
    assert.equal($(dom,'researchDetails').hidden,true);dom.window.close();
  }
  // Untrusted question/source/notice remain plain text, never HTML.
  dom=await page('romlon');
  dom.window.eval(`activeState.questions[0].text='<img src=x onerror="window.xss=1">'; renderQuestion();`);
  assert.equal($(dom,'questionText').querySelector('img'),null);
  assert.equal(dom.window.xss,undefined);dom.window.close();
  console.log('PASS: 18 research pages, real temporary-DB HTTP handlers, direct start / edit selection / save / reload / revisit / report / confirmed retest; legacy draft editing / batch / preservation, retry, auth, XSS, old modes. DOM only.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  child.stdin.end();await exited;fs.rmSync(temp,{recursive:true,force:true});
});
