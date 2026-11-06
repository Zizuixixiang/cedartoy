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
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cedar-standard-drafts-'));
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
const requests = [];
const windows = [];
async function until(test) {
  for (let i=0; i<300; i++) {
    if (test()) return;
    await new Promise(resolve=>setTimeout(resolve, 20));
  }
  throw new Error('page did not settle');
}
async function page(game, saved) {
  const html = await request('/'+game);
  assert.equal(html.status, 200);
  const dom = new JSDOM(html.body, {url:'https://fixture.invalid/'+game, runScripts:'dangerously', pretendToBeVisual:true,
    beforeParse(w) {
      w.HTMLElement.prototype.scrollIntoView=function(){};
      w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
      w.HTMLDialogElement.prototype.close=function(){this.open=false;};
      for (const [key,value] of Object.entries(saved)) w.localStorage.setItem(key,value);
      w.fetch=async(url,opts)=>{
        const body=JSON.parse(opts.body);
        requests.push({url,body});
        const response=await request(url,body,opts.headers);
        return {ok:response.status===200,status:response.status,json:async()=>JSON.parse(response.body)};
      };
    }});
  windows.push(dom.window);
  await until(()=>dom.window.eval('activeState !== null && !busy'));
  return dom.window;
}
const buttons=w=>[...w.document.querySelectorAll('#resumeChoices button')];
async function main() {
  for (const [game,total] of [['mbti',93],['enneagram',180]]) {
    const player='guest:webold'+game, key=`cedartoy_${game}_web_draft_${player}`;
    const start=async()=>JSON.parse((await request(`/api/${game}/start`,{player_id:player,edition:'complete'})).body);
    const first=await start();
    assert.equal(first.total,total);
    const values=first.questions.map(q=>q.options?.[0].value ?? 3);
    // Exact old-JS body: deliberately no session_id or expected_index.
    const oldBody={player_id:player,answers:values};
    const failed=await request(`/api/${game}/answer_batch`,oldBody);
    assert.notEqual(failed.status,200);
    assert.match(JSON.parse(failed.body).error,/恢复旧草稿/);
    const current=await start(); // A new run must not silently inherit old answers.
    assert.notEqual(current.session_id,first.session_id);
    const raw=JSON.stringify({game,edition:'complete',total,answers:values,cursor:total-1});
    let w=await page(game,{'cedartoy_guest_web_player_id':player,[key]:raw});
    assert.equal(w.eval('answers.filter(Number.isInteger).length'),0);
    assert.equal(w.document.querySelector('#resumeDialog').open,true);
    buttons(w)[0].click(); // Cancel is safe and leaves every old answer intact.
    assert.equal(w.localStorage.getItem(key),raw);
    assert.equal(w.eval('answers.filter(Number.isInteger).length'),0);
    w=await page(game,{...w.localStorage});
    buttons(w)[1].click();
    assert.equal(w.eval('JSON.stringify(answers)'),JSON.stringify(values));
    assert.equal(w.eval('cursor'),0); // Start at question one for checking.
    assert.equal(w.document.querySelector('#questionPanel').hidden,false);
    assert.equal(JSON.parse(w.localStorage.getItem(key)).session_id,current.session_id);
    assert(Object.entries(w.localStorage).some(([k,v])=>k.startsWith(key+'_conflict_') && v===raw));
    assert.equal(requests.filter(r=>r.url===`/api/${game}/answer_batch`).length,0,'restoring never submits');
    // Real review and submit controls send the current generation only after confirmation.
    w.document.querySelector('#reviewButton').click();
    assert.equal(w.document.querySelector('#reviewPanel').hidden,false);
    w.document.querySelector('#submitButton').click();
    await until(()=>w.eval('!busy'));
    assert.equal(w.eval('activeState.complete'),true);
    const sent=requests.find(r=>r.url===`/api/${game}/answer_batch`).body;
    assert.equal(sent.session_id,current.session_id);
    assert.equal(sent.expected_index,0);
    assert.deepEqual(sent.answers,values);
    assert(Object.values(w.localStorage).includes(raw),'original survives successful submission');

    // Archives made by the deployed version remain recoverable after base-slot replacement.
    await start();
    const partial=JSON.stringify({...JSON.parse(raw),answers:values.map((v,i)=>i<23?v:null),session_id:first.session_id});
    const bad=JSON.stringify({...JSON.parse(raw),answers:values.map(()=>999)});
    w=await page(game,{'cedartoy_guest_web_player_id':player,[key]:bad,[key+'_conflict_123']:partial});
    assert.equal(buttons(w).length,2,'invalid answers are not offered');
    buttons(w)[1].click();
    assert.equal(w.eval('answers.filter(Number.isInteger).length'),23);
    assert(Object.values(w.localStorage).includes(bad),'rejected raw draft is preserved');
    assert(Object.values(w.localStorage).includes(partial));
    const before=JSON.stringify({...w.localStorage});
    w=await page(game,{...w.localStorage});
    assert.equal(w.document.querySelector('#resumeDialog').open,false,'confirmed current draft resumes normally');
    assert.equal(w.eval('answers.filter(Number.isInteger).length'),23);
    assert.equal(JSON.stringify({...w.localStorage}),before);
  }
  // Existing research legacy full_fast recovery still follows its original path.
  const player='guest:weboldresearch', key=`cedartoy_romlon_web_draft_${player}`;
  await request('/api/romlon/start',{player_id:player,mode:'full_fast'});
  const raw=JSON.stringify({game:'romlon',edition:'full_fast',total:4,answers:[1,2,null,null],cursor:2});
  const w=await page('romlon',{'cedartoy_guest_web_player_id':player,[key]:raw});
  assert.equal(w.eval('JSON.stringify(answers)'),'[1,2,null,null]');
  assert.equal(w.document.querySelector('#resumeDialog').open,false);
  assert.equal(w.localStorage.getItem(key),raw);
  console.log('PASS: old JS rejection + 93/180 answer recovery, confirmation, archives, invalid drafts, research legacy');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  windows.forEach(w=>w.close());
  child.stdin.end();
  await exited;
  fs.rmSync(temp,{recursive:true,force:true});
});
