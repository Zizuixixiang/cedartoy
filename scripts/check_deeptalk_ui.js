// DOM + real Python HTTP/MCP handlers over stdio; does not claim rendered layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const child = spawn('python3', ['-u', 'scripts/deeptalk_test_server.py'], {cwd:root, stdio:['pipe','pipe','inherit']});
const queue = [];
const lines = readline.createInterface({input:child.stdout});
let initialResolve;
const initial = new Promise(resolve => initialResolve = resolve);
lines.on('line', line => { const value = JSON.parse(line); if (value.tokens) initialResolve(value.tokens); else queue.shift()(value); });
function transport(value) { return new Promise(resolve => { queue.push(resolve); child.stdin.write(JSON.stringify(value)+'\n'); }); }
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
async function until(fn, label) { for(let i=0;i<300;i++){ if(fn()) return; await sleep(10); } throw new Error('Timed out: '+label); }
let windows = [];
(async () => {
  const tokens = await initial;
  const errors = [];
  let failNextWrite = false;
  const html = fs.readFileSync(path.join(root,'deeptalk.html'),'utf8');
  function page(search = '', token = tokens[101]) {
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError',error=>errors.push(error.message));
    const dom = new JSDOM(html, {url:'https://deeptalk.test/deeptalk'+search,runScripts:'dangerously',pretendToBeVisual:true,virtualConsole,
      beforeParse(window) {
        if(token) window.localStorage.setItem('cedartoy_token',token);
        window.confirm=()=>true;
        window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
        window.HTMLDialogElement.prototype.close=function(){this.open=false;};
        window.fetch = async (url, options={}) => {
          const result = await transport({url, ...options});
          if(failNextWrite && options.method==='POST') { failNextWrite=false; throw new Error('测试：响应丢失，请重试'); }
          return {ok:result.status<400,status:result.status,json:async()=>result.body};
        };
      }
    });
    windows.push(dom.window); return dom.window;
  }
  const $ = (w,id)=>w.document.getElementById(id);
  async function click(w,id) { assert.equal($(w,id).disabled,false,id+' enabled'); $(w,id).click(); await until(()=>!w.eval('busy'),id); }
  function input(w,text) { $(w,'answer').value=text; $(w,'answer').dispatchEvent(new w.Event('input')); }
  let w=page(); await until(()=>!$(w,'setup').hidden,'setup');
  assert.equal($(w,'topic').options.length,9);
  $(w,'mode').value='chat'; $(w,'mode').dispatchEvent(new w.Event('change'));
  assert.equal($(w,'blindIntro').hidden,true); assert.match($(w,'chatIntro').textContent,/不创建轮次/);
  $(w,'mode').value='blind'; $(w,'mode').dispatchEvent(new w.Event('change'));
  await click(w,'create'); assert.equal($(w,'round').hidden,false);
  const search=w.location.search, rid=new URLSearchParams(search).get('round_id');
  assert.ok(rid);
  input(w,'<img src=x onerror="window.xss=1">我的回答');
  failNextWrite=true; await click(w,'save'); assert.match($(w,'message').textContent,/响应丢失/);
  await click(w,'save'); assert.equal(w.eval('state.revision'),1,'retry must not double-write');
  await click(w,'next'); await click(w,'skip'); await click(w,'previous');
  assert.match($(w,'saveHint').textContent,/已跳过/);
  input(w,'回到上一题补写'); await click(w,'save');
  w.close(); w=page(search); await until(()=>!$(w,'round').hidden,'restore');
  $(w,'questionJump').value='1'; $(w,'questionJump').dispatchEvent(new w.Event('change'));
  await until(()=>!w.eval('busy'),'jump'); assert.equal($(w,'answer').value,'回到上一题补写');
  // Explicit skips, then final whole-form confirmation.
  for(let i=2;i<13;i++) { $(w,'questionJump').value=String(i); $(w,'questionJump').dispatchEvent(new w.Event('change')); await until(()=>!w.eval('busy'),'jump'); await click(w,'skip'); }
  await click(w,'submit'); assert.equal($(w,'confirmDialog').open,true);
  await click(w,'cancelSubmit'); assert.equal(w.eval('state.submitted'),false);
  await click(w,'submit'); await click(w,'confirmSubmit'); assert.equal(w.eval('state.submitted'),true);
  assert.equal($(w,'records').hidden,true);
  async function mcp(action,params={}) {
    const result=await transport({kind:'mcp',token:tokens[201],payload:{jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'play',arguments:{game:'deeptalk',action,params}}}});
    assert.equal(result.result.isError,false,JSON.stringify(result));
    return JSON.parse(result.result.content[0].text);
  }
  const question=await mcp('question',{round_id:rid}); assert.equal(question.questions[0].partner,undefined);
  const realQuestions=await mcp('questions',{topic_id:'relations_communication',limit:100});
  for(let i=1;i<=13;i++) await mcp('answer',{round_id:rid,question_id:realQuestions.questions[i-1].id,text:'小机私密回答 '+i,revision:i-1,request_id:'ui_answer_'+i});
  await click(w,'refresh'); assert.equal($(w,'records').hidden,true); assert.doesNotMatch(w.document.body.textContent,/小机私密回答/);
  await mcp('submit',{round_id:rid,revision:13,confirm:true}); await click(w,'refresh');
  assert.equal($(w,'records').hidden,false); assert.equal($(w,'records').querySelectorAll('.record').length,13);
  assert.match($(w,'records').textContent,/小机私密回答 1/);
  assert.equal($(w,'records').querySelector('img'),null); assert.equal(w.xss,undefined);
  const history=await transport({url:'/api/auth/history',headers:{Authorization:'Bearer '+tokens[101]}});
  const summary=history.body.self.deeptalk_history.rounds.find(r=>r.round_id===rid);
  assert.equal(summary.status,'complete'); assert.equal(summary.url,'/deeptalk?round_id='+rid);
  assert.doesNotMatch(JSON.stringify(history.body),/小机私密回答|我的回答|questions|answers/);
  const other=page(search,tokens[102]); await until(()=>$(other,'message').classList.contains('error'),'unauthorized');
  assert.equal($(other,'round').hidden,true); assert.doesNotMatch(other.document.body.textContent,/小机私密回答/);
  const loggedOut=page('',null); await until(()=>!$(loggedOut,'login').hidden,'login');
  const retry=page(); await until(()=>!$(retry,'setup').hidden,'new page');
  failNextWrite=true; await click(retry,'create'); await click(retry,'create');
  assert.notEqual(new URLSearchParams(retry.location.search).get('round_id'),rid);
  const finalHistory=await transport({url:'/api/deeptalk?action=history',headers:{Authorization:'Bearer '+tokens[101]}});
  assert.equal(finalHistory.body.total,3,'fixture + completed + retried creation');
  const homeHtml = fs.readFileSync(path.join(root,'index.html'),'utf8');
  const homeConsole = new VirtualConsole();
  homeConsole.on('jsdomError', error => errors.push(error.message));
  const home = new JSDOM(homeHtml,{url:'https://deeptalk.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:homeConsole,
    beforeParse(window) {
      window.localStorage.setItem('cedartoy_token',tokens[101]);
      window.fetch=async(url,options={})=>{
        if(url==='/api/auth/me') return {ok:true,json:async()=>({user:{id:101,username:'人类甲',is_ai:false},bindings:[]})};
        if(url==='/api/auth/history' || url.startsWith('/api/deeptalk')) {
          const r=await transport({url,...options}); return {ok:r.status<400,status:r.status,json:async()=>r.body};
        }
        return {ok:true,json:async()=>({announcements:[],unread_count:0})};
      };
    }
  }).window;
  windows.push(home);
  await until(()=>Boolean($(home,'historyOpen')),'homepage login');
  await home.openHistory();
  const historyLink=home.document.querySelector(`#historyContent a[href="/deeptalk?round_id=${rid}"]`);
  assert.ok(historyLink); assert.equal(historyLink.textContent,'查看');
  assert.doesNotMatch($(home,'historyContent').textContent,/小机私密回答|我的回答/);
  assert.equal(home.eval('games.filter(g => g.id === "deeptalk").length'),1);
  // A hostile participant name is text, even in the existing HTML history renderer.
  const hostile=home.document.createElement('div');
  hostile.innerHTML=home.renderDeeptalkHistory({rounds:[{...summary,human_name:'<img src=x onerror="window.xss=1">'}]});
  assert.equal(hostile.querySelector('img'),null);
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: create/chat guide/save/skip/edit/retry/refresh/confirmation/secrecy/unlock/XSS/history/authorization (DOM + real handlers).');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{for(const w of windows)w.close();child.stdin.end();});
