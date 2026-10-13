// DOM + real Python HTTP/MCP handlers over stdio; does not claim rendered layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const topicId = process.env.DEEPTALK_TOPIC || 'learning_goals_problem_solving';
const topic = JSON.parse(fs.readFileSync(path.join(root, 'deeptalk/data', topicId + '.json'), 'utf8'));
const total = topic.questions.length;
const home = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const displayTitle = JSON.parse(home.match(/const deeptalkTopics = (\[[\s\S]*?\n    \]);/)[1]).find(row => row[1] === topicId)[2];
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
  let failNextWrite = false, noBindings = false, hostileSource = false, failQuestion = false, questionReads = 0, delayQuestion = null;
  const html = fs.readFileSync(path.join(root,'deeptalk.html'),'utf8');
  function page(search = '', token = tokens[101]) {
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError',error=>errors.push(error.message));
    const dom = new JSDOM(html, {url:'https://deeptalk.test/deeptalk'+search,runScripts:'dangerously',pretendToBeVisual:true,virtualConsole,
      beforeParse(window) {
        // Exercise all existing write/retry/restore flows on older WebViews.
        Object.defineProperty(window.crypto, 'randomUUID', {value:undefined, configurable:true});
        if(token) window.localStorage.setItem('cedartoy_token',token);
        window.confirm=()=>true;
        window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
        window.HTMLDialogElement.prototype.close=function(){this.open=false;};
        window.fetch = async (url, options={}) => {
          const isQuestion = String(url).includes('action=question&');
          if(isQuestion) { questionReads++; if(failQuestion) throw new Error('offline'); }
          if (options.method === 'POST') {
            const body = JSON.parse(options.body);
            if (['create','answer'].includes(body.action)) assert.match(body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
          }
          const result = await transport({url, ...options});
          if(isQuestion && delayQuestion) await delayQuestion;
          if(failNextWrite && options.method==='POST') { failNextWrite=false; throw new Error('测试：响应丢失，请重试'); }
          if(noBindings && String(url).includes('action=context')) result.body.machines=[];
          if(hostileSource && String(url).includes('action=source')) result.body={title:'<img src=x onerror="window.xss=1">',entries:[{name:'<img src=x>',author:'<script>bad()</script>',source_url:'javascript:alert(1)',license:'TEST',license_url:'data:text/html,bad'}],note:'note'};
          return {ok:result.status<400,status:result.status,json:async()=>result.body};
        };
      }
    });
    windows.push(dom.window); return dom.window;
  }
  const $ = (w,id)=>w.document.getElementById(id);
  async function click(w,id) { assert.equal($(w,id).disabled,false,id+' enabled'); $(w,id).click(); await until(()=>!w.eval('busy'),id); }
  function input(w,text) { $(w,'answer').value=text; $(w,'answer').dispatchEvent(new w.Event('input')); }
  let w=page('?topic_id=' + topicId); await until(()=>!$(w,'setup').hidden,'setup');
  assert.equal($(w,'topic'),null);
  assert.equal($(w,'machine'),null);
  assert.equal($(w,'topicTitle').textContent,displayTitle);
  assert.equal(w.document.title,displayTitle+' · CEDAR TOY');
  $(w,'mode').value='chat'; $(w,'mode').dispatchEvent(new w.Event('change'));
  assert.equal($(w,'modeIntro').textContent,'边聊边答：双方已作答的内容实时可见。');
  assert.equal($(w,'create').hidden,false);
  $(w,'mode').value='blind'; $(w,'mode').dispatchEvent(new w.Event('change'));
  await click(w,'create'); assert.equal($(w,'round').hidden,false);
  assert.equal($(w,'newRound').getAttribute('href'),'/deeptalk?topic_id='+topicId);
  assert.equal(w.eval('state.sharing_mode'),'bound_shared');
  assert.equal($(w,'roundTitle').textContent,displayTitle);
  assert.equal(w.document.title,displayTitle+' · CEDAR TOY');
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
  for(let i=2;i<total;i++) { $(w,'questionJump').value=String(i); $(w,'questionJump').dispatchEvent(new w.Event('change')); await until(()=>!w.eval('busy'),'jump'); await click(w,'skip'); }
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
  const realQuestions=await mcp('questions',{topic_id:topicId,limit:100});
  for(let i=1;i<=total;i++) await mcp('answer',{round_id:rid,question_id:realQuestions.questions[i-1].id,text:'小机私密回答 '+i,revision:i-1,request_id:'ui_answer_'+i});
  await click(w,'refresh'); assert.equal($(w,'records').hidden,true); assert.doesNotMatch(w.document.body.textContent,/小机私密回答/);
  await mcp('submit',{round_id:rid,revision:total,confirm:true}); await click(w,'refresh');
  assert.equal($(w,'records').hidden,false); assert.equal($(w,'records').querySelectorAll('.record').length,total);
  assert.match($(w,'records').textContent,/小机私密回答 1/);
  assert.equal($(w,'records').querySelector('img'),null); assert.equal(w.xss,undefined);
  const history=await transport({url:'/api/auth/history',headers:{Authorization:'Bearer '+tokens[101]}});
  const summary=history.body.self.deeptalk_history.rounds.find(r=>r.round_id===rid);
  assert.equal(summary.status,'complete'); assert.equal(summary.url,'/deeptalk?round_id='+rid);
  assert.doesNotMatch(JSON.stringify(history.body),/小机私密回答|我的回答|questions|answers/);
  const other=page(search,tokens[102]); await until(()=>$(other,'message').classList.contains('error'),'unauthorized');
  assert.equal($(other,'round').hidden,true); assert.doesNotMatch(other.document.body.textContent,/小机私密回答/);
  const blank=page(); await until(()=>!$(blank,'chooseTopic').hidden,'choose topic');
  assert.equal($(blank,'setup').hidden,true);
  const invalid=page('?topic_id=invalid'); await until(()=>!$(invalid,'chooseTopic').hidden,'invalid topic');
  assert.equal($(invalid,'setup').hidden,true);
  const loggedOut=page('?topic_id='+topicId,null); await until(()=>!$(loggedOut,'login').hidden,'login');
  let retry=page('?topic_id='+topicId); await until(()=>!$(retry,'setup').hidden,'new page');
  failNextWrite=true; await click(retry,'create');
  const pendingCreate=retry.sessionStorage.getItem('deeptalk_pending_create');
  const pendingId=JSON.parse(pendingCreate).body.request_id;
  retry.close(); retry=page('?topic_id='+topicId);
  retry.sessionStorage.setItem('deeptalk_pending_create',pendingCreate);
  await until(()=>!$(retry,'setup').hidden,'retry after reload');
  assert.equal(retry.eval('pendingCreate.body.request_id'),pendingId);
  await click(retry,'create');
  assert.notEqual(new URLSearchParams(retry.location.search).get('round_id'),rid);
  const finalHistory=await transport({url:'/api/deeptalk?action=history',headers:{Authorization:'Bearer '+tokens[101]}});
  assert.equal(finalHistory.body.total,3,'fixture + completed + retried creation');
  const legacySummary=finalHistory.body.rounds.find(r=>r.sharing_mode==='single_ai');
  const legacyPage=page('?round_id='+legacySummary.round_id); await until(()=>!$(legacyPage,'round').hidden,'legacy restore');
  assert.equal(legacyPage.eval('state.mode'),'blind'); assert.equal($(legacyPage,'peerAnswer').hidden,true);
  legacyPage.close();
  let chat=page('?topic_id='+topicId); await until(()=>!$(chat,'setup').hidden,'chat setup');
  $(chat,'mode').value='chat'; $(chat,'mode').dispatchEvent(new chat.Event('change'));
  await click(chat,'create');
  const chatSearch=chat.location.search, chatId=new URLSearchParams(chatSearch).get('round_id');
  assert.equal(chat.eval('state.mode'),'chat');
  assert.equal($(chat,'peerAnswer').hidden,false);
  assert.equal($(chat,'peerText').textContent,'尚未回答');
  const copy=chat.document.body.cloneNode(true); copy.querySelectorAll('script').forEach(el=>el.remove());
  assert.doesNotMatch(copy.textContent,/MCP|topic_id|questions|保存|服务器|token|面对面聊/);
  input(chat,'还在输入，不应公开'); $(chat,'answer').focus(); $(chat,'answer').setSelectionRange(2,4);
  assert.equal((await mcp('question',{round_id:chatId})).questions[0].partner,undefined);
  await mcp('answer',{round_id:chatId,question_id:topic.questions[0].id,text:'实时小机回答',revision:0,request_id:'live_ui_001'});
  await chat.eval('pollPeer()');
  assert.equal($(chat,'peerText').textContent,'实时小机回答');
  assert.equal($(chat,'answer').value,'还在输入，不应公开');
  assert.equal(chat.document.activeElement,$(chat,'answer'));
  assert.equal($(chat,'answer').selectionStart,2); assert.equal($(chat,'answer').selectionEnd,4);
  assert.equal(chat.eval('state.revision'),0);
  await click(chat,'next'); assert.equal(chat.eval('index'),0);
  assert.equal((await mcp('question',{round_id:chatId})).questions[0].partner,undefined);
  await click(chat,'refresh'); assert.equal($(chat,'answer').value,'还在输入，不应公开');
  assert.equal(chat.eval('dirty'),true);
  failQuestion=true; await chat.eval('pollPeer()'); assert.match($(chat,'liveHint').textContent,/重试/);
  failQuestion=false; await chat.eval('pollPeer()'); assert.equal($(chat,'liveHint').textContent,'');
  const reads=questionReads;
  Object.defineProperty(chat.document,'hidden',{configurable:true,value:true});
  await chat.eval('pollPeer()'); assert.equal(questionReads,reads,'hidden pages do not poll');
  Object.defineProperty(chat.document,'hidden',{configurable:true,value:false});
  failNextWrite=true; await click(chat,'save'); await click(chat,'save');
  assert.equal(chat.eval('state.revision'),1);
  assert.equal((await mcp('result',{round_id:chatId})).questions[0].partner.text,'还在输入，不应公开');
  // A delayed current-question response must never replace the next question.
  let release; delayQuestion=new Promise(resolve=>release=resolve);
  const pendingPoll=chat.eval('pollPeer()'); await until(()=>questionReads>reads,'delayed poll');
  await click(chat,'next'); release(); await pendingPoll; delayQuestion=null;
  assert.equal($(chat,'peerText').textContent,'尚未回答');
  chat.close(); chat=page(chatSearch); await until(()=>!$(chat,'round').hidden,'chat restore');
  assert.equal(chat.eval('state.mode'),'chat');
  await click(chat,'previous'); assert.equal($(chat,'answer').value,'还在输入，不应公开');
  assert.equal($(chat,'peerText').textContent,'实时小机回答');
  input(chat,'我这页的新回答');
  const concurrent=await transport({url:'/api/deeptalk/action',method:'POST',headers:{Authorization:'Bearer '+tokens[101]},body:JSON.stringify({action:'answer',round_id:chatId,question_id:topic.questions[0].id,text:'另一页面的回答',revision:1,request_id:'other_tab_001'})});
  assert.equal(concurrent.status,200);
  await chat.eval('pollPeer()'); assert.equal(chat.eval('state.revision'),1);
  assert.equal($(chat,'answer').value,'我这页的新回答');
  await click(chat,'save'); assert.match($(chat,'message').textContent,/更新/);
  let conflictPrompt=''; chat.confirm=text=>(conflictPrompt=text,true);
  await click(chat,'refresh'); assert.match(conflictPrompt,/另一页面的回答/);
  assert.equal($(chat,'answer').value,'我这页的新回答'); assert.equal(chat.eval('state.revision'),2);
  await click(chat,'save'); assert.equal(chat.eval('state.revision'),3);
  await click(chat,'next');
  for(let i=1;i<total;i++) await click(chat,'skip');
  await click(chat,'submit'); assert.doesNotMatch($(chat,'confirmText').textContent,/解锁/);
  await click(chat,'confirmSubmit'); assert.equal($(chat,'peerAnswer').hidden,false);
  for(let i=1;i<total;i++) await mcp('answer',{round_id:chatId,question_id:topic.questions[i].id,skip:true,revision:i,request_id:'chat_complete_'+i});
  await mcp('submit',{round_id:chatId,revision:total,confirm:true});
  await click(chat,'refresh'); assert.equal($(chat,'records').hidden,false);
  assert.equal($(chat,'records').querySelectorAll('.record').length,total);
  assert.match($(chat,'records').textContent,/我这页的新回答|实时小机回答/);
  chat.close();
  // All real package sources use readable links, without editorial/raw metadata.
  const sourceTopics=await transport({url:'/api/deeptalk?action=context',headers:{Authorization:'Bearer '+tokens[101]}});
  for(const {topic_id:tid} of sourceTopics.body.topics) {
    const sourcePage=page('?topic_id='+tid); await until(()=>!$(sourcePage,'setup').hidden,'source setup');
    assert.equal($(sourcePage,'sources').open,false);
    $(sourcePage,'sources').open=true;
    await until(()=>$(sourcePage,'sourceBody').querySelector('a') && !sourcePage.eval('busy'),'source loaded');
    const body=$(sourcePage,'sourceBody');
    assert.doesNotMatch(body.textContent,/OC|adaptation_note|question_ids|docs\//);
    assert.ok(body.querySelectorAll('a').length>=2);
    for(const link of body.querySelectorAll('a')) {
      assert.match(link.href,/^https?:/); assert.equal(link.rel,'noopener noreferrer');
    }
    sourcePage.close();
  }
  hostileSource=true;
  const hostilePage=page('?topic_id='+topicId); await until(()=>!$(hostilePage,'setup').hidden,'hostile setup');
  $(hostilePage,'sources').open=true;
  await until(()=>$(hostilePage,'sourceBody').textContent.includes('note') && !hostilePage.eval('busy'),'hostile source');
  assert.equal($(hostilePage,'sourceBody').querySelector('a,img,script'),null);
  assert.equal(hostilePage.xss,undefined); hostileSource=false;
  noBindings=true;
  const unbound=page('?topic_id='+topicId); await until(()=>!$(unbound,'setup').hidden,'unbound setup');
  assert.equal($(unbound,'create').disabled,true);
  $(unbound,'mode').value='chat'; $(unbound,'mode').dispatchEvent(new unbound.Event('change'));
  assert.equal($(unbound,'create').disabled,true);
  $(unbound,'mode').value='blind'; $(unbound,'mode').dispatchEvent(new unbound.Event('change'));
  assert.equal($(unbound,'create').disabled,true); noBindings=false;
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
  assert.equal(home.eval('games.filter(g => g.testGroup === "deeptalk").length'),10);
  assert.equal(home.eval('games.filter(g => g.id === "deeptalk").length'),0);
  // A hostile participant name is text, even in the existing HTML history renderer.
  const hostile=home.document.createElement('div');
  hostile.innerHTML=home.renderDeeptalkHistory({rounds:[{...summary,human_name:'<img src=x onerror="window.xss=1">'}]});
  assert.equal(hostile.querySelector('img'),null);
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: create/blind/chat live polling/draft retention/save/skip/edit/retry/refresh/confirmation/secrecy/unlock/XSS/history/authorization (DOM + real handlers).');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{for(const w of windows)w.close();child.stdin.end();});
