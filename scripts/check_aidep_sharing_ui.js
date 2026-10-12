"use strict";
// Actual page JS + actual Python HTTP/auth/MCP handlers, temporary SQLite, stdio.
// DOM checks do not certify mobile layout, focus appearance, or browser rendering.
const assert = require('node:assert/strict');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const bridge = `
import io,json,sys
from unittest.mock import Mock,patch
from tests_toy.test_aidep_sharing import AidepSharingTests
import research_scales,server
fixture=AidepSharingTests()
fixture.setUp()
try:
    fixture.complete(2)
    fixture.complete(3)
    with server._db_connect() as conn:
        conn.execute('UPDATE toy_users SET username=? WHERE id=12', ('<img src=x onerror="window.xss=1">很长的小机名称'*3,))
    for game,engine in research_scales.SCALES.items():
        if game == 'aidep': continue
        state=engine.execute('start', {'player_id':'1','mode':'full_fast'})
        engine.execute('answer_batch', {'player_id':'1','session_id':state['session_id'],'answers':[engine.answer_min]*len(engine.items)})
    print(json.dumps({'tokens':fixture.tokens,'games':list(research_scales.SCALES)}),flush=True)
    with patch.object(server,'_duel_unread_request_reminder',return_value=''), patch.object(server,'_mcp_forced_announcement',return_value=''):
        for line in sys.stdin:
            req=json.loads(line)
            h=object.__new__(server.CedarToyHandler)
            h.path=req['url']; h.client_address=('127.0.0.1',12345)
            raw=req.get('body','').encode()
            h.headers={**req.get('headers',{}),'Content-Length':str(len(raw))}
            h.rfile=io.BytesIO(raw); h.wfile=io.BytesIO()
            h.send_response=Mock(); h.send_header=Mock(); h.end_headers=Mock()
            (h.do_POST if req.get('method')=='POST' else h.do_GET)()
            print(json.dumps({'status':h.send_response.call_args.args[0], 'body':h.wfile.getvalue().decode(),
                'headers':dict(c.args for c in h.send_header.call_args_list)}),flush=True)
finally:
    fixture.doCleanups()
`;
const child = spawn('python3', ['-u', '-c', bridge], {cwd:root, stdio:['pipe','pipe','inherit']});
const queue = [];
let initResolve, initReject;
const initial = new Promise((resolve,reject)=>{initResolve=resolve;initReject=reject;});
const lines = readline.createInterface({input:child.stdout});
lines.on('line', line=>{
  const value=JSON.parse(line);
  if(value.tokens) initResolve(value); else queue.shift().resolve(value);
});
child.on('error',initReject);
child.on('exit',code=>{
  if(code) initReject(new Error('fixture exited '+code));
  queue.splice(0).forEach(p=>p.reject(new Error('fixture exited '+code)));
});
function transport(value){return new Promise((resolve,reject)=>{
  queue.push({resolve,reject});child.stdin.write(JSON.stringify(value)+'\n');
});}
const windows=[];
const errors=[];
async function until(test,label){
  for(let i=0;i<300;i++){if(test())return;await new Promise(r=>setTimeout(r,10));}
  throw new Error('timeout: '+label);
}
async function main(){
  const {tokens,games}=await initial;
  let failRead=false, loseWrite=false, copied='';
  const requests=[];
  async function page(game='aidep', user=1, saved={}){
    const html=await transport({url:'/'+game});assert.equal(html.status,200);
    const virtualConsole=new VirtualConsole();
    virtualConsole.on('jsdomError',e=>errors.push(e.message));
    const dom=new JSDOM(html.body,{url:'https://aidep.test/'+game,runScripts:'dangerously',pretendToBeVisual:true,virtualConsole,
      beforeParse(w){
        w.HTMLElement.prototype.scrollIntoView=function(){};
        w.confirm=()=>true;
        if(user) w.localStorage.setItem('cedartoy_token',tokens[user]);
        for(const [key,value] of Object.entries(saved))w.localStorage.setItem(key,value);
        Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async text=>{copied=text;}}});
        w.fetch=async(url,options={})=>{
          requests.push({url,...options});
          if(url==='/api/aidep/sharing' && options.method==='GET' && failRead){failRead=false;throw new Error('模拟断网');}
          const result=await transport({url,...options});
          if(url==='/api/aidep/sharing' && options.method==='POST' && loseWrite){loseWrite=false;throw new Error('模拟响应丢失');}
          return {ok:result.status<400,status:result.status,json:async()=>JSON.parse(result.body)};
        };
      }});
    windows.push(dom.window);return dom.window;
  }
  const $=(w,id)=>w.document.getElementById(id);
  const status=w=>$(w,'aidepSharing')?.querySelector('[role=status]');
  const button=(w,text)=>Array.from($(w,'aidepSharing').querySelectorAll('button')).find(b=>b.textContent===text);
  async function ready(w){await until(()=>$(w,'aidepShareTarget') && !$(w,'aidepShareTarget').disabled,'sharing loaded');}
  async function select(w,value){
    $(w,'aidepShareTarget').value=value;
    $(w,'aidepShareTarget').dispatchEvent(new w.Event('change'));
    button(w,'保存选择').click();
    await until(()=>!status(w).textContent.includes('正在保存'),'saved');
  }
  async function mcp(ai=11,human=1){
    const result=await transport({url:'/mcp',method:'POST',headers:{Authorization:'Bearer '+tokens[ai]},body:JSON.stringify({
      jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'play',arguments:{game:'aidep',action:'shared_result',params:{human_id:human}}}})});
    return JSON.parse(result.body).result;
  }
  let w=await page();
  await until(()=>!$(w,'questionPanel').hidden && !w.eval('busy'),'direct questions');
  assert.equal($(w,'aidepSharing'),null,'incomplete hidden');
  for(let i=0;i<22;i++){
    assert.equal($(w,'aidepSharing'),null);
    w.document.querySelectorAll('#answers button')[2].click();
    await until(()=>!w.eval('busy') && w.eval('activeState.saved_answers.length')===i+1
      && w.eval('cursor')===Math.min(i+1,21),'autosaved answer');
  }
  assert.equal($(w,'resultPanel').hidden,true,'final choice never auto-finalizes');
  assert.equal($(w,'saveAnswerButton').textContent,'查看结果');
  $(w,'saveAnswerButton').click();
  await until(()=>!$(w,'resultPanel').hidden,'final explicit result');
  await ready(w);
  assert.equal($(w,'aidepShareTarget').value,'','private default');
  assert.equal($(w,'aidepShareTarget').options.length,3,'two explicit targets plus off');
  assert.equal((await mcp()).isError,true);
  await select(w,'11');assert.match(status(w).textContent,/已授权：小机甲/);
  assert.equal((await mcp()).isError,false);
  assert.equal((await mcp(12)).isError,true,'no broadcast');
  button(w,'复制给小机的查看指令').click();await until(()=>copied.includes('shared_result') && button(w,'已复制'),'copy');
  assert.match(copied,/human_id: 1/);
  w.navigator.clipboard.writeText=async()=>{throw new Error('clipboard unavailable');};
  button(w,'已复制').click();
  await until(()=>!$(w,'aidepSharing').querySelector('pre').hidden,'manual copy fallback');
  assert.match($(w,'aidepSharing').querySelector('pre').textContent,/shared_result/);
  w.close();w=await page();await ready(w);
  assert.equal($(w,'aidepShareTarget').value,'11','reload restores consent');
  await select(w,'12');assert.match(status(w).textContent,/<img/);
  assert.equal($(w,'aidepSharing').querySelector('img'),null);assert.equal(w.xss,undefined);
  assert.equal((await mcp()).isError,true);assert.equal((await mcp(12)).isError,false);
  loseWrite=true;await select(w,'');
  assert.match(status(w).textContent,/保存未确认/);
  assert.equal((await mcp(12)).isError,true,'revoke committed despite lost response');
  button(w,'刷新分享设置').click();await ready(w);assert.equal($(w,'aidepShareTarget').value,'');
  await select(w,'11');
  const stale=await page();await ready(stale);
  $(w,'restartButton').click();
  await until(()=>!$(w,'questionPanel').hidden && !w.eval('busy'),'retest');
  assert.equal($(w,'aidepSharing'),null);
  assert.equal((await mcp()).isError,true);
  await select(stale,'12');assert.match(status(stale).textContent,/结果已变化/);
  stale.close();w.close();
  failRead=true;w=await page('aidep',2);
  await until(()=>status(w)?.textContent.includes('无法确认'),'load error');
  assert.equal($(w,'aidepShareTarget').disabled,true);
  button(w,'刷新分享设置').click();await ready(w);
  assert.equal($(w,'aidepShareTarget').value,'');w.close();
  w=await page('aidep',3);await ready(w);
  assert.equal($(w,'aidepShareTarget').options.length,1);
  assert.match(status(w).textContent,/暂无已绑定/);w.close();
  w=await page('aidep',11);await until(()=>!$(w,'error').hidden,'AI denied');
  assert.equal($(w,'aidepSharing'),null);w.close();
  w=await page('aidep',null);await until(()=>!$(w,'questionPanel').hidden && !w.eval('busy'),'guest');
  const guest=w.localStorage.getItem('cedartoy_guest_web_player_id');
  const start=JSON.parse((await transport({url:'/api/aidep/start',method:'POST',body:JSON.stringify({player_id:guest,mode:'full_fast',restart:true,confirm:true,session_id:w.eval('activeState.session_id')})})).body);
  await transport({url:'/api/aidep/answer_batch',method:'POST',body:JSON.stringify({player_id:guest,session_id:start.session_id,answers:Array(22).fill(3)})});
  const saved={...w.localStorage};w.close();w=await page('aidep',null,saved);
  await until(()=>!$(w,'resultPanel').hidden,'guest result');assert.equal($(w,'aidepSharing'),null);w.close();
  for(const game of games.filter(g=>g!=='aidep')){
    w=await page(game);await until(()=>!$(w,'resultPanel').hidden,game+' result');
    assert.equal($(w,'aidepSharing'),null,game+' no sharing UI');w.close();
  }
  assert.deepEqual(errors,[]);
  assert.ok(requests.filter(r=>r.url==='/api/aidep/sharing').every(r=>r.cache==='no-store'));
  console.log('PASS: AIDep submit/history/default privacy/explicit target/switch/revoke/retest/stale form/real MCP/read and write failure recovery/XSS/copy/guest/AI/no bindings/other 17 scales. DOM only; rendered browser layouts remain unverified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{
  windows.forEach(w=>w.close());child.stdin.end();
});
