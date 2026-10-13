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
  let failNextView = false, failBeforeWrite = false, writeCount = 0, failNextWrite = false, noBindings = false, hostileSource = false, failHistory = false, failQuestion = false, questionReads = 0, delayQuestion = null;
  const html = fs.readFileSync(path.join(root,'deeptalk.html'),'utf8');
  function page(search = '', token = tokens[101], session = {}) {
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError',error=>errors.push(error.message));
    const dom = new JSDOM(html, {url:'https://deeptalk.test/deeptalk'+search,runScripts:'dangerously',pretendToBeVisual:true,virtualConsole,
      beforeParse(window) {
        // Exercise all existing write/retry/restore flows on older WebViews.
        Object.defineProperty(window.crypto, 'randomUUID', {value:undefined, configurable:true});
        if(token) window.localStorage.setItem('cedartoy_token',token);
        for (const [key, value] of Object.entries(session)) window.sessionStorage.setItem(key,value);
        window.confirm=()=>true;
        window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
        window.HTMLDialogElement.prototype.close=function(){this.open=false;};
        window.fetch = async (url, options={}) => {
          if(failNextView && String(url).includes('action=view&')) { failNextView=false; throw new TypeError('view offline'); }
          if(failHistory && String(url).includes('action=history&')) { failHistory=false; throw new TypeError('history offline'); }
          const isQuestion = String(url).includes('action=question&');
          if(isQuestion) { questionReads++; if(failQuestion) throw new Error('offline'); }
          if (options.method === 'POST') {
            if (failBeforeWrite) { failBeforeWrite=false; throw new TypeError('offline'); }
            writeCount++;
            const body = JSON.parse(options.body);
            if (body.action === 'create') assert.equal(body.mode,'chat');
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
  async function jump(w,index) { $(w,'questionJump').value=String(index); $(w,'questionJump').dispatchEvent(new w.Event('change')); await until(()=>!w.eval('busy'),'jump'); }
  function questionStatus(w,index,text) { assert.equal($(w,'questionJump').options[index].textContent,`第${index+1}题 · ${text}`); }
  function input(w,text) { $(w,'answer').value=text; $(w,'answer').dispatchEvent(new w.Event('input')); }
  function sessionCopy(w) { return Object.fromEntries(Array.from({length:w.sessionStorage.length}, (_,i) => {
    const key=w.sessionStorage.key(i); return [key,w.sessionStorage.getItem(key)];
  })); }
  let w=page('?new=1&topic_id=' + topicId); await until(()=>!$(w,'setup').hidden,'setup');
  assert.equal($(w,'topic'),null);
  assert.equal($(w,'machine'),null);
  assert.equal($(w,'topicTitle').textContent,displayTitle);
  assert.equal(w.document.title,displayTitle+' · CEDAR TOY');
  assert.equal($(w,'mode'),null);
  assert.equal($(w,'modeIntro'),null);
  assert.equal($(w,'create').hidden,false);
  await click(w,'create'); assert.equal($(w,'round').hidden,false);
  assert.equal($(w,'newRound').getAttribute('href'),'/deeptalk?topic_id='+topicId+'&new=1');
  assert.equal(w.eval('state.sharing_mode'),'bound_shared');
  assert.equal(w.eval('state.mode'),'chat');
  assert.equal($(w,'roundTitle').textContent,displayTitle);
  assert.equal(w.document.title,displayTitle+' · CEDAR TOY');
  const search=w.location.search, rid=new URLSearchParams(search).get('round_id');
  assert.ok(rid);
  assert.equal($(w,'save'),null); assert.equal($(w,'skip'),null); assert.equal($(w,'refresh'),null);
  assert.equal($(w,'completion'),null); assert.equal($(w,'submit'),null); assert.equal($(w,'newRound').hidden,false);
  assert.equal($(w,'newRound').textContent,'题库 / 历史');
  questionStatus(w,0,'○待回答');
  input(w,'<img src=x onerror="window.xss=1">我的回答');
  failNextWrite=true; await click(w,'next'); assert.match($(w,'message').textContent,/响应丢失/);
  assert.equal(w.eval('index'),0); assert.match($(w,'answer').value,/我的回答/);
  await w.eval('pollPeer()'); assert.match($(w,'answer').value,/我的回答/);
  await click(w,'next'); assert.equal(w.eval('state.revision'),1,'retry must not double-write');
  questionStatus(w,0,'✓已回答'); questionStatus(w,1,'○待回答');
  input(w,'   '); await click(w,'next'); questionStatus(w,1,'—已跳过'); await click(w,'previous');
  assert.equal($(w,'saveHint').hidden,true,'skip state is already shown in question picker');
  assert.equal($(w,'questionJump').getAttribute('aria-label'),'选题');
  assert.equal($(w,'quickHint'),null,'quick guidance moved to top of round');
  input(w,'回到上一题补写'); await click(w,'previous');
  assert.equal(w.eval('index'),0); assert.equal(w.eval('state.questions[1].own.text'),'回到上一题补写');
  questionStatus(w,1,'✓已回答');
  w.close(); w=page(search); await until(()=>!$(w,'round').hidden,'restore');
  $(w,'questionJump').value='1'; $(w,'questionJump').dispatchEvent(new w.Event('change'));
  await until(()=>!w.eval('busy'),'jump'); assert.equal($(w,'answer').value,'回到上一题补写');
  // Explicit skips, then final whole-form confirmation.
  for(let i=2;i<total;i++) { $(w,'questionJump').value=String(i); $(w,'questionJump').dispatchEvent(new w.Event('change')); await until(()=>!w.eval('busy'),'jump'); await click(w,'next'); }
  assert.equal($(w,'confirmDialog').open,true,'last question leads to final confirmation');
  assert.equal(w.eval('state.submitted'),false); await click(w,'cancelSubmit');
  assert.equal($(w,'completion'),null); assert.equal($(w,'newRound').hidden,false);
  await click(w,'next'); assert.equal($(w,'confirmDialog').open,true);
  await click(w,'cancelSubmit'); assert.equal(w.eval('state.submitted'),false);
  await click(w,'next'); failBeforeWrite=true; await click(w,'confirmSubmit');
  assert.equal($(w,'confirmDialog').open,true); assert.match($(w,'confirmError').textContent,/网络/);
  assert.equal(w.eval('state.submitted'),false);
  failNextWrite=true; await click(w,'confirmSubmit'); // server committed; response lost
  assert.equal($(w,'confirmDialog').open,true); assert.match($(w,'confirmError').textContent,/响应丢失/);
  await click(w,'confirmSubmit'); assert.equal(w.eval('state.submitted'),true);
  assert.equal($(w,'records').hidden,true); assert.equal($(w,'newRound').hidden,false);
  async function mcp(action,params={}) {
    const result=await transport({kind:'mcp',token:tokens[201],payload:{jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'play',arguments:{game:'deeptalk',action,params}}}});
    assert.equal(result.result.isError,false,JSON.stringify(result));
    return JSON.parse(result.result.content[0].text);
  }
  const question=await mcp('question',{round_id:rid}); assert.match(question.questions[0].partner.text,/我的回答/);
  assert.equal(question.unlocked,false); assert.equal(question.peer_visible,true);
  const partial=await mcp('result',{round_id:rid,limit:20}); assert.ok(partial.questions.every(q=>q.partner));
  assert.equal($(w,'waiting').hidden,false);
  const writesBeforeReview=writeCount;
  await jump(w,0); await click(w,'next'); await click(w,'previous'); await jump(w,total-1);
  assert.equal(writeCount,writesBeforeReview,'submitted review must not write');
  assert.equal($(w,'next').disabled,true);
  assert.equal($(w,'answer').hidden,true); assert.equal($(w,'ownRead').hidden,false);
  const realQuestions=await mcp('questions',{topic_id:topicId,limit:100});
  for(let i=1;i<=total;i++) await mcp('answer',{round_id:rid,question_id:realQuestions.questions[i-1].id,text:'小机私密回答 '+i,revision:i-1,request_id:'ui_answer_'+i});
  await w.eval('pollPeer()'); assert.equal($(w,'records').hidden,true); assert.match($(w,'peerText').textContent,/小机私密回答/);
  await mcp('submit',{round_id:rid,revision:total,confirm:true}); await w.eval('pollPeer()');
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
  const loggedOut=page('?new=1&topic_id='+topicId,null); await until(()=>!$(loggedOut,'login').hidden,'login');
  let retry=page('?new=1&topic_id='+topicId); await until(()=>!$(retry,'setup').hidden,'new page');
  failNextWrite=true; await click(retry,'create');
  const pendingCreate=retry.sessionStorage.getItem('deeptalk_pending_create');
  const pendingId=JSON.parse(pendingCreate).body.request_id;
  retry.close(); retry=page('?new=1&topic_id='+topicId);
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
  // AI-first blind submission reveals its full side without saving or locking a human draft.
  const aiFirstFixture=await transport({kind:'legacy_create',topic_id:topicId,request_id:'legacy_ai_first'});
  const aiFirstId=aiFirstFixture.round_id;
  let aiFirst=page('?round_id='+aiFirstId); await until(()=>!$(aiFirst,'round').hidden,'AI-first legacy round');
  assert.match($(aiFirst,'roundDescription').textContent,/答完再看（旧模式）/);
  input(aiFirst,'人类仍在写的私密草稿'); $(aiFirst,'answer').focus(); $(aiFirst,'answer').setSelectionRange(2,4);
  for(let i=0;i<total;i++) await mcp('answer',{round_id:aiFirstId,question_id:topic.questions[i].id,
    ...(i===total-1 ? {skip:true} : {text:'先提交的小机 '+i}),revision:i,request_id:'ai_first_'+i});
  await aiFirst.eval('pollPeer()'); assert.equal($(aiFirst,'peerAnswer').hidden,true);
  const beforePeerSubmit=writeCount;
  await mcp('submit',{round_id:aiFirstId,revision:total,confirm:true}); await aiFirst.eval('pollPeer()');
  assert.equal(writeCount,beforePeerSubmit,'polling is read-only');
  assert.equal(aiFirst.eval('state.unlocked'),false); assert.equal(aiFirst.eval('state.submitted'),false);
  assert.equal(aiFirst.eval('state.revision'),0); assert.equal($(aiFirst,'answer').readOnly,false);
  assert.equal($(aiFirst,'answer').value,'人类仍在写的私密草稿');
  assert.equal(aiFirst.document.activeElement.id,'answer'); assert.equal($(aiFirst,'answer').selectionStart,2);
  assert.equal($(aiFirst,'peerText').textContent,'先提交的小机 0');
  assert.equal($(aiFirst,'records').hidden,true);
  assert.equal((await mcp('result',{round_id:aiFirstId})).questions[0].partner,undefined);
  await jump(aiFirst,total-1); assert.equal($(aiFirst,'peerText').textContent,'已跳过');
  assert.equal((await mcp('question',{round_id:aiFirstId})).questions[0].partner,undefined,'saved blind draft stays hidden');
  // Setup provides completion-specific links, server-filtered history and pagination.
  const submittedOnly=page('?new=1&topic_id='+topicId); await until(()=>!$(submittedOnly,'setup').hidden,'submitted history setup');
  await click(submittedOnly,'create'); const submittedOnlyId=submittedOnly.eval('roundId');
  for(let i=0;i<total;i++) await click(submittedOnly,'next');
  await click(submittedOnly,'confirmSubmit');
  const returned=page('?new=1&topic_id='+topicId); await until(()=>!$(returned,'setup').hidden,'submitted history');
  assert.equal($(returned,'historyList').querySelector('a').textContent,'查看上次答卷');
  assert.match($(returned,'historyList').textContent,/等待小机提交/);
  assert.equal(new URL($(returned,'historyList').querySelector('a').href).searchParams.get('round_id'),submittedOnlyId);
  const oldLink=page('?round_id='+submittedOnlyId); await until(()=>!$(oldLink,'round').hidden,'old submitted link');
  assert.equal($(oldLink,'waiting').textContent,'已提交，等待小机答题中；小机可查看你的回答');
  const readonlyWrites=writeCount; await jump(oldLink,0); await click(oldLink,'next'); await click(oldLink,'previous');
  assert.equal(writeCount,readonlyWrites); assert.equal($(oldLink,'ownRead').textContent,'已跳过');
  for(let i=0;i<22;i++) {
    if (i%2) {
      const result=await transport({url:'/api/deeptalk/action',method:'POST',headers:{Authorization:'Bearer '+tokens[101]},body:JSON.stringify({action:'create',topic_id:topicId,request_id:'history_page_'+i})});
      assert.equal(result.status,200); assert.equal(result.body.mode,'chat');
    } else {
      await transport({kind:'legacy_create',topic_id:topicId,request_id:'history_page_'+i});
    }
  }
  failHistory=true;
  const failedHistory=page('?new=1&topic_id='+topicId); await until(()=>!$(failedHistory,'setup').hidden,'history failure');
  assert.equal($(failedHistory,'moreHistory').textContent,'重试读取历史');
  assert.equal($(failedHistory,'create').disabled,false,'history failure does not block new rounds');
  await click(failedHistory,'moreHistory'); assert.equal($(failedHistory,'roundHistory').hidden,false); failedHistory.close();
  const historyPage=page('?new=1&topic_id='+topicId); await until(()=>!$(historyPage,'setup').hidden,'paginated history');
  assert.equal($(historyPage,'historyList').querySelector('a').textContent,'继续答题');
  assert.match($(historyPage,'historyList').textContent,/边聊边答/);
  assert.equal($(historyPage,'historyList').querySelectorAll('a').length,20);
  assert.equal($(historyPage,'roundHistory').open,false);
  assert.match($(historyPage,'historyList').textContent,/答完再看（旧模式）/);
  while(!$(historyPage,'moreHistory').hidden) await click(historyPage,'moreHistory');
  assert.ok([...$(historyPage,'historyList').querySelectorAll('a')].some(a=>a.textContent==='查看双方回答' && a.href.endsWith(rid)));
  assert.ok([...$(historyPage,'historyList').querySelectorAll('a')].some(a=>a.textContent==='查看上次答卷' && a.href.endsWith(submittedOnlyId)));
  assert.ok([...$(historyPage,'historyList').querySelectorAll('a')].some(a=>a.href.endsWith(aiFirstId)));
  assert.equal([...$(historyPage,'historyList').querySelectorAll('a')].some(a=>a.href.endsWith(legacySummary.round_id)),topicId===legacySummary.topic_id,'history filters by topic');
  const strangerHistory=page('?new=1&topic_id='+topicId,tokens[102]); await until(()=>!$(strangerHistory,'setup').hidden,'other human history');
  assert.equal($(strangerHistory,'roundHistory').hidden,true);
  for(const win of [aiFirst,submittedOnly,returned,oldLink,historyPage,strangerHistory]) win.close();
  let chat=page('?new=1&topic_id='+topicId); await until(()=>!$(chat,'setup').hidden,'chat setup');
  await click(chat,'create');
  const chatSearch=chat.location.search, chatId=new URLSearchParams(chatSearch).get('round_id');
  assert.equal(chat.eval('state.mode'),'chat');
  assert.equal($(chat,'peerAnswer').hidden,false);
  assert.equal($(chat,'peerText').textContent,'尚未回答');
  const copy=chat.document.body.cloneNode(true); copy.querySelectorAll('script').forEach(el=>el.remove());
  assert.doesNotMatch(copy.textContent,/MCP|topic_id|questions|服务器|token|面对面聊/);
  input(chat,'还在输入，不应公开'); $(chat,'answer').focus(); $(chat,'answer').setSelectionRange(2,4);
  assert.equal((await mcp('question',{round_id:chatId})).questions[0].partner,undefined);
  await mcp('answer',{round_id:chatId,question_id:topic.questions[0].id,text:'实时小机回答',revision:0,request_id:'live_ui_001'});
  await chat.eval('pollPeer()');
  assert.equal($(chat,'peerText').textContent,'实时小机回答');
  assert.equal($(chat,'answer').value,'还在输入，不应公开');
  assert.equal(chat.document.activeElement,$(chat,'answer'));
  assert.equal($(chat,'answer').selectionStart,2); assert.equal($(chat,'answer').selectionEnd,4);
  assert.equal(chat.eval('state.revision'),0);
  failQuestion=true; await chat.eval('pollPeer()'); assert.match($(chat,'liveHint').textContent,/重试/);
  failQuestion=false; await chat.eval('pollPeer()'); assert.equal($(chat,'liveHint').textContent,'');
  const reads=questionReads;
  Object.defineProperty(chat.document,'hidden',{configurable:true,value:true});
  await chat.eval('pollPeer()'); assert.equal(questionReads,reads,'hidden pages do not poll');
  Object.defineProperty(chat.document,'hidden',{configurable:true,value:false});
  failNextWrite=true; await click(chat,'next');
  assert.equal(chat.eval('index'),0); assert.equal($(chat,'answer').value,'还在输入，不应公开');
  await click(chat,'next');
  assert.equal(chat.eval('state.revision'),1); assert.equal(chat.eval('index'),1);
  assert.equal((await mcp('result',{round_id:chatId})).questions[0].partner.text,'还在输入，不应公开');
  await click(chat,'previous'); // untouched blank is skipped on backward navigation too
  assert.equal(chat.eval('state.questions[1].own.skipped'),true);
  // A delayed current-question response must never replace the next question.
  let release; delayQuestion=new Promise(resolve=>release=resolve);
  const pendingPoll=chat.eval('pollPeer()'); await until(()=>questionReads>reads,'delayed poll');
  await click(chat,'next'); release(); await pendingPoll; delayQuestion=null;
  assert.equal($(chat,'peerText').textContent,'尚未回答');
  chat.close(); chat=page(chatSearch); await until(()=>!$(chat,'round').hidden,'chat restore');
  assert.equal(chat.eval('state.mode'),'chat');
  $(chat,'questionJump').value='0'; $(chat,'questionJump').dispatchEvent(new chat.Event('change'));
  await until(()=>!chat.eval('busy'),'chat jump'); assert.equal($(chat,'answer').value,'还在输入，不应公开');
  assert.equal($(chat,'peerText').textContent,'实时小机回答');
  input(chat,'我这页的新回答');
  const concurrent=await transport({url:'/api/deeptalk/action',method:'POST',headers:{Authorization:'Bearer '+tokens[101]},body:JSON.stringify({action:'answer',round_id:chatId,question_id:topic.questions[0].id,text:'另一页面的回答',revision:chat.eval('state.revision'),request_id:'other_tab_001'})});
  assert.equal(concurrent.status,200);
  const oldRevision=chat.eval('state.revision');
  await chat.eval('pollPeer()'); assert.equal(chat.eval('state.revision'),oldRevision);
  assert.equal($(chat,'answer').value,'我这页的新回答');
  let conflictPrompt=''; chat.confirm=text=>(conflictPrompt=text,false);
  await click(chat,'next'); assert.match(conflictPrompt,/另一页面的回答/);
  assert.equal(chat.eval('index'),0); assert.equal($(chat,'answer').value,'我这页的新回答');
  chat.confirm=()=>true;
  await click(chat,'next'); assert.equal(chat.eval('state.revision'),oldRevision+2);
  for(let i=1;i<total;i++) await click(chat,'next');
  assert.equal($(chat,'confirmDialog').open,true); assert.doesNotMatch($(chat,'confirmText').textContent,/解锁/);
  await click(chat,'confirmSubmit'); assert.equal($(chat,'peerAnswer').hidden,false);
  for(let i=1;i<total;i++) await mcp('answer',{round_id:chatId,question_id:topic.questions[i].id,skip:true,revision:i,request_id:'chat_complete_'+i});
  await mcp('submit',{round_id:chatId,revision:total,confirm:true});
  await chat.eval('pollPeer()'); assert.equal($(chat,'records').hidden,false);
  assert.equal($(chat,'records').querySelectorAll('.record').length,total);
  assert.match($(chat,'records').textContent,/我这页的新回答|实时小机回答/);
  chat.close();
  // Navigation is an autosave boundary in both modes; polling is strictly read-only.
  for (const mode of ['blind','chat']) {
    const legacy = mode==='blind' ? await transport({kind:'legacy_create',topic_id:topicId,request_id:'legacy_navigation'}) : null;
    const nav=page(legacy ? '?round_id='+legacy.round_id : '?new=1&topic_id='+topicId);
    if (legacy) await until(()=>!$(nav,'round').hidden,'legacy navigation');
    else { await until(()=>!$(nav,'setup').hidden,'navigation setup'); await click(nav,'create'); }
    assert.equal(nav.eval('state.mode'),mode);
    const navId=new URLSearchParams(nav.location.search).get('round_id');
    input(nav,'选题跳转也会保存');
    await jump(nav,total-1);
    assert.equal(nav.eval('state.questions[0].own.text'),'选题跳转也会保存');
    assert.equal($(nav,'next').textContent,'提交答案');
    failBeforeWrite=true; await click(nav,'next');
    assert.equal(nav.eval('index'),total-1); assert.equal($(nav,'confirmDialog').open,false);
    questionStatus(nav,total-1,'○待回答');
    await click(nav,'next'); questionStatus(nav,total-1,'—已跳过');
    assert.equal(nav.eval('index'),1,'finish must return to the first missing question');
    assert.equal($(nav,'confirmDialog').open,false); assert.equal(nav.eval('state.submitted'),false);
    assert.equal($(nav,'completion'),null); assert.equal($(nav,'newRound').hidden,false);
    input(nav,'失败时保留选题草稿');
    failBeforeWrite=true; await jump(nav,3);
    assert.equal(nav.eval('index'),1); assert.equal($(nav,'questionJump').value,'1');
    assert.equal($(nav,'answer').value,'失败时保留选题草稿');
    assert.match($(nav,'message').textContent,/网络/);
    await jump(nav,3); assert.equal(nav.eval('state.questions[1].own.text'),'失败时保留选题草稿');
    input(nav,'上一题也会自动保存'); failBeforeWrite=true; await click(nav,'previous');
    assert.equal(nav.eval('index'),3); assert.equal($(nav,'answer').value,'上一题也会自动保存');
    await click(nav,'previous'); assert.equal(nav.eval('state.questions[3].own.text'),'上一题也会自动保存');
    input(nav,'长回答'.repeat(2000)); $(nav,'answer').focus(); $(nav,'answer').setSelectionRange(7,15);
    $(nav,'answer').scrollTop=120;
    const beforeWrites=writeCount, originalEditor=$(nav,'editor'), originalOption=$(nav,'questionJump').firstChild;
    let editorMutations=0;
    const observer=new nav.MutationObserver(records=>editorMutations+=records.length);
    observer.observe(originalEditor,{subtree:true,childList:true,characterData:true});
    for(let i=0;i<3;i++) await nav.eval('pollPeer()');
    await sleep(0); observer.disconnect();
    assert.equal(writeCount,beforeWrites); assert.equal(editorMutations,0);
    assert.equal($(nav,'editor'),originalEditor); assert.equal($(nav,'questionJump').firstChild,originalOption);
    assert.equal(nav.document.activeElement,$(nav,'answer')); assert.equal($(nav,'answer').selectionStart,7);
    assert.equal($(nav,'answer').selectionEnd,15); assert.equal($(nav,'answer').scrollTop,120);
    assert.equal($(nav,'answer').value,'长回答'.repeat(2000));
    await mcp('answer',{round_id:navId,question_id:topic.questions[2].id,text:'模式隐私检查',revision:0,request_id:'mode_privacy_'+mode});
    await nav.eval('pollPeer()'); assert.equal(nav.eval('state.progress.ai.done'),1);
    assert.equal($(nav,'peerText').textContent,mode==='chat'?'模式隐私检查':'');
    if(mode==='blind') assert.doesNotMatch(nav.document.body.textContent,/模式隐私检查/);
    await click(nav,'next'); assert.equal(nav.eval('state.questions[2].own.text'),'长回答'.repeat(2000));
    // A write racing another tab returns 409; retry explicitly reconciles the saved copy.
    input(nav,'冲突后保留');
    const conflict=await transport({url:'/api/deeptalk/action',method:'POST',headers:{Authorization:'Bearer '+tokens[101]},body:JSON.stringify({action:'answer',round_id:navId,question_id:topic.questions[3].id,text:'远端版本',revision:nav.eval('state.revision'),request_id:'nav_conflict_'+mode})});
    assert.equal(conflict.status,200);
    await click(nav,'next'); assert.match($(nav,'message').textContent,/再次换题/);
    assert.equal(nav.eval('index'),3); assert.equal($(nav,'answer').value,'冲突后保留');
    await click(nav,'next'); assert.equal(nav.eval('state.questions[3].own.text'),'冲突后保留');
    input(nav,'响应丢失的回答'); failNextWrite=true; await click(nav,'next');
    const afterLost=await transport({url:'/api/deeptalk/action',method:'POST',headers:{Authorization:'Bearer '+tokens[101]},body:JSON.stringify({action:'answer',round_id:navId,question_id:topic.questions[4].id,text:'响应丢失后远端又修改',revision:nav.eval('state.revision')+1,request_id:'lost_conflict_'+mode})});
    assert.equal(afterLost.status,200);
    await click(nav,'next'); assert.equal(nav.eval('index'),4);
    assert.equal($(nav,'answer').value,'响应丢失的回答'); assert.match($(nav,'message').textContent,/随后/);
    let replayPrompt=''; nav.confirm=text=>(replayPrompt=text,true);
    await click(nav,'next'); assert.match(replayPrompt,/响应丢失后远端又修改/);
    assert.equal(nav.eval('state.questions[4].own.text'),'响应丢失的回答');
    for(let i=5;i<total;i++) await click(nav,'next');
    assert.equal($(nav,'confirmDialog').open,true); await click(nav,'cancelSubmit');
    assert.equal($(nav,'newRound').hidden,false,'unsubmitted round can return to mode selection');
    // Returning to any completed question keeps the single-row submit entry available.
    await jump(nav,0); assert.equal($(nav,'next').textContent,'提交答案');
    assert.equal($(nav,'confirmDialog').open,false,'selection alone must not open confirmation');
    input(nav,'回看后修改'); failBeforeWrite=true; await click(nav,'next');
    assert.equal(nav.eval('index'),0); assert.equal($(nav,'answer').value,'回看后修改');
    assert.equal($(nav,'confirmDialog').open,false); assert.equal($(nav,'newRound').hidden,false);
    await click(nav,'next'); assert.equal(nav.eval('state.questions[0].own.text'),'回看后修改');
    questionStatus(nav,0,'✓已回答'); assert.equal($(nav,'confirmDialog').open,true);
    assert.equal(nav.eval('state.submitted'),false); await click(nav,'cancelSubmit');
    await click(nav,'next'); await click(nav,'cancelSubmit'); // repeated cancellation stays editable
    await jump(nav,1); assert.equal($(nav,'next').textContent,'提交答案');
    await click(nav,'previous'); assert.equal(nav.eval('index'),0);
    // Clearing a saved answer becomes a skip, still requiring explicit confirmation.
    input(nav,''); await click(nav,'next');
    assert.equal(nav.eval('state.questions[0].own.skipped'),true); questionStatus(nav,0,'—已跳过');
    assert.equal($(nav,'confirmDialog').open,true); assert.equal(nav.eval('state.submitted'),false);
    assert.equal($(nav,'newRound').hidden,false);
    const otherPage=page('?round_id='+navId); await until(()=>!$(otherPage,'round').hidden,'other tab');
    assert.equal($(otherPage,'next').textContent,'提交答案'); assert.equal($(otherPage,'newRound').hidden,false);
    input(otherPage,'提交前远端修改'); await click(otherPage,'next'); await click(otherPage,'cancelSubmit');
    await click(nav,'confirmSubmit');
    assert.equal($(nav,'confirmDialog').open,false); assert.equal(nav.eval('state.submitted'),false);
    assert.match($(nav,'message').textContent,/重新确认提交/);
    // A remote submit locks the form but keeps a local draft available for copying.
    input(otherPage,'尚未保存的本地草稿'); $(otherPage,'answer').focus();
    let aiRevision=1;
    for(let i=0;i<total;i++) if(i!==2) await mcp('answer',{round_id:navId,question_id:topic.questions[i].id,skip:true,revision:aiRevision++,request_id:'nav_ai_'+mode+'_'+i});
    await mcp('submit',{round_id:navId,revision:aiRevision,confirm:true});
    await click(nav,'next'); failNextView=true; await click(nav,'confirmSubmit');
    assert.equal(nav.eval('state.submitted'),true,'successful submit locks before any result read');
    assert.equal($(nav,'answer').hidden,true); assert.match($(nav,'liveHint').textContent,/稍后自动重试/);
    await nav.eval('pollPeer()'); assert.equal($(nav,'records').hidden,false);
    await otherPage.eval('pollPeer()');
    assert.equal($(otherPage,'answer').value,'尚未保存的本地草稿');
    assert.equal($(otherPage,'answer').hidden,false); assert.equal($(otherPage,'answer').readOnly,true);
    assert.equal($(otherPage,'previous').disabled,true); assert.equal($(otherPage,'next').disabled,true);
    assert.equal($(otherPage,'questionJump').disabled,true); assert.equal($(otherPage,'completion'),null);
    assert.match($(otherPage,'saveHint').textContent,/供复制/);
    assert.equal($(otherPage,'newRound').hidden,false,'remote submission also permits restarting');
    await otherPage.eval('pollPeer()'); assert.equal($(otherPage,'answer').value,'尚未保存的本地草稿');
    const lockedReload=page(otherPage.location.search,tokens[101],sessionCopy(otherPage));
    await until(()=>!$(lockedReload,'round').hidden && !lockedReload.eval('busy'),'locked draft reload');
    assert.equal($(lockedReload,'answer').value,'尚未保存的本地草稿');
    assert.equal($(lockedReload,'answer').hidden,false);
    assert.equal($(lockedReload,'answer').readOnly,true);
    assert.equal($(lockedReload,'quickAnswers').hidden,true);
    assert.equal($(lockedReload,'writeOwn'),null);
    assert.equal($(lockedReload,'next').disabled,true);
    lockedReload.close();
    assert.equal($(nav,'newRound').textContent,'重新答题'); assert.equal($(nav,'newRound').hidden,false);
    const beforeRestart=await transport({url:'/api/deeptalk?action=view&round_id='+navId,headers:{Authorization:'Bearer '+tokens[101]}});
    const restart=page(new URL($(nav,'newRound').href).search);
    await until(()=>!$(restart,'setup').hidden,'restart same topic setup');
    assert.equal(restart.eval('topic.topic_id'),topicId); assert.equal(restart.eval('roundId'),null);
    await click(restart,'create');
    assert.notEqual(restart.eval('roundId'),navId); assert.equal(restart.eval('state.progress.human.done'),0);
    assert.equal($(restart,'newRound').hidden,false);
    const afterRestart=await transport({url:'/api/deeptalk?action=view&round_id='+navId,headers:{Authorization:'Bearer '+tokens[101]}});
    assert.deepEqual(afterRestart.body,beforeRestart.body,'restarting preserves the submitted historical round');
    restart.close(); otherPage.close(); nav.close();
  }
  // All real package sources use readable links, without editorial/raw metadata.
  const sourceTopics=await transport({url:'/api/deeptalk?action=context',headers:{Authorization:'Bearer '+tokens[101]}});
  for(const {topic_id:tid} of sourceTopics.body.topics) {
    let quick=page('?new=1&topic_id='+tid); await until(()=>!$(quick,'setup').hidden,'quick setup');
    await click(quick,'create');
    const localQuestions=quick.eval('state.questions');
    const imported=JSON.parse(fs.readFileSync(path.join(root,'deeptalk/quick_answers',tid+'.json'),'utf8'));
    const freeTextOnly=Object.entries(imported).filter(([,item])=>item.free_text_only).map(([id])=>id);
    assert.deepEqual(Array.from(localQuestions.filter(q=>!q.quick_answers),q=>q.id),freeTextOnly,
      'only the 17 official free-only decisions omit suggestions');
    const indices=localQuestions.map((_,i)=>i); // Exercise all 490 imported questions.
    for(const idx of indices) {
      if (quick.eval('index')!==idx) await jump(quick,idx);
      input(quick,'');
      const buttons=Array.from($(quick,'quickOptions').children), options=localQuestions[idx].quick_answers;
      if (!options) {
        assert.match($(quick,'roundHint').textContent,/直接填写.*留空可跳过/);
        assert.equal($(quick,'quickAnswers').hidden,true);
        assert.equal(buttons.length,0);
        assert.equal($(quick,'answer').hidden,false);
        assert.equal($(quick,'answer').readOnly,false);
        assert.equal($(quick,'answer').disabled,false);
        $(quick,'answer').focus();
        assert.equal(quick.document.activeElement,$(quick,'answer'));
        const writes=writeCount, text='测试自填内容 '+idx;
        input(quick,text); assert.equal(writeCount,writes,'typing remains local');
        const freeSearch=quick.location.search, freeSession=sessionCopy(quick);
        quick.close(); quick=page(freeSearch,tokens[101],freeSession);
        await until(()=>!$(quick,'round').hidden && !quick.eval('busy'),'free-only draft reload');
        assert.equal(quick.eval('index'),idx);
        assert.equal($(quick,'answer').value,text);
        assert.equal($(quick,'quickAnswers').hidden,true);
        assert.equal($(quick,'quickOptions').children.length,0);
        await click(quick,'next'); await jump(quick,idx);
        assert.equal($(quick,'answer').value,text,'free text survives save and return');
        continue;
      }
      assert.equal(buttons.length,options.options.length);
      assert.equal($(quick,'roundHint').textContent.includes('可多选'), options.mode==='multiple');
      assert.match($(quick,'roundHint').textContent,/留空可跳过/);
      assert.deepEqual(buttons.map(button=>button.textContent),imported[localQuestions[idx].id].options);
      assert.equal(options.mode,imported[localQuestions[idx].id].mode);
      const writes=writeCount;
      buttons[0].click(); assert.equal($(quick,'answer').value,options.options[0]);
      assert.equal(buttons[0].getAttribute('aria-pressed'),'true');
      $(quick,'answer').focus();
      assert.equal(quick.document.activeElement,$(quick,'answer'));
      assert.equal($(quick,'answer').value,options.options[0]);
      input(quick,$(quick,'answer').value+'\n我还想补充一点');
      buttons[1].click();
      assert.match($(quick,'answer').value,/我还想补充一点/);
      assert.equal(buttons[0].getAttribute('aria-pressed'),String(options.mode==='multiple'));
      assert.equal(buttons[1].getAttribute('aria-pressed'),'true');
      buttons[1].click(); assert.equal(buttons[1].getAttribute('aria-pressed'),'false');
      assert.doesNotMatch($(quick,'answer').value,new RegExp(options.options[1].replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
      assert.equal(writeCount,writes,'choices and custom entry never send an answer');
      assert.equal(quick.eval('index'),idx,'choices never navigate');
      assert.equal(quick.eval('state.submitted'),false);
    }
    // The final question may be free-only and already saved above. Create an
    // actual unsaved choice draft before testing restoration and uncertain retry.
    await jump(quick,localQuestions.findIndex(q=>q.quick_answers));
    input(quick,'');
    $(quick,'quickOptions').firstElementChild.click();
    input(quick,$(quick,'answer').value+'\nUnsaved fixture supplement');
    const text=$(quick,'answer').value, at=quick.eval('index'), search=quick.location.search;
    const selected=quick.eval('JSON.stringify(selectedChoices)'), session=sessionCopy(quick);
    quick.close(); quick=page(search,tokens[101],session);
    await until(()=>!$(quick,'round').hidden && !quick.eval('busy'),'quick draft reload');
    assert.equal(quick.eval('index'),at); assert.equal($(quick,'answer').value,text);
    assert.equal(quick.eval('JSON.stringify(selectedChoices)'),selected);
    // A lost response followed by editing and refresh retries the old receipt first.
    const beforeRevision=quick.eval('state.revision');
    failNextWrite=true; await click(quick,'next');
    const originalRequest=quick.eval('pendingAnswer.body.request_id');
    input(quick,text+'\n响应丢失后继续编辑');
    const uncertain=sessionCopy(quick); quick.close(); quick=page(search,tokens[101],uncertain);
    await until(()=>!$(quick,'round').hidden && !quick.eval('busy'),'uncertain request reload');
    assert.equal(quick.eval('pendingAnswer.body.request_id'),originalRequest);
    await click(quick,'next');
    assert.equal(quick.eval('state.revision'),beforeRevision+2,'one replay plus one changed answer');
    assert.equal(quick.eval('state.questions['+at+'].own.text'),text+'\n响应丢失后继续编辑');
    assert.equal(quick.sessionStorage.getItem(quick.eval('draftKey()')),null,'acknowledged draft cleared');
    quick.close();
  }
  for(const {topic_id:tid} of sourceTopics.body.topics) {
    const sourcePage=page('?new=1&topic_id='+tid); await until(()=>!$(sourcePage,'setup').hidden,'source setup');
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
  const hostilePage=page('?new=1&topic_id='+topicId); await until(()=>!$(hostilePage,'setup').hidden,'hostile setup');
  $(hostilePage,'sources').open=true;
  await until(()=>$(hostilePage,'sourceBody').textContent.includes('note') && !hostilePage.eval('busy'),'hostile source');
  assert.equal($(hostilePage,'sourceBody').querySelector('a,img,script'),null);
  assert.equal(hostilePage.xss,undefined); hostileSource=false;
  noBindings=true;
  const unbound=page('?new=1&topic_id='+topicId); await until(()=>!$(unbound,'setup').hidden,'unbound setup');
  assert.equal($(unbound,'create').disabled,true);
  assert.equal($(unbound,'mode'),null);
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
  const latestHistory=await transport({url:'/api/deeptalk?action=history',headers:{Authorization:'Bearer '+tokens[101]}});
  const latestId=latestHistory.body.rounds[0].round_id;
  const historyLink=home.document.querySelector(`#historyContent a[href="/deeptalk?round_id=${latestId}"]`);
  assert.ok(historyLink); assert.equal(historyLink.textContent,latestHistory.body.rounds[0].status==='complete' ? '查看' : '继续');
  assert.doesNotMatch($(home,'historyContent').textContent,/小机私密回答|我的回答/);
  assert.equal(home.eval('games.filter(g => g.testGroup === "deeptalk").length'),10);
  assert.equal(home.eval('games.filter(g => g.id === "deeptalk").length'),0);
  // A hostile participant name is text, even in the existing HTML history renderer.
  const hostile=home.document.createElement('div');
  hostile.innerHTML=home.renderDeeptalkHistory({rounds:[{...summary,human_name:'<img src=x onerror="window.xss=1">'}]});
  assert.equal(hostile.querySelector('img'),null);
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: all 10 topics / 490 questions / 17 free-only drafts; single/multiple/cancel/custom-focus/no-auto-write/refresh/edited-uncertain-retry/locked-draft-refresh; create/blind/chat/one-sided-release/private-draft/readonly-review/polling/autosave/skip/conflict/confirmation/secrecy/unlock/XSS/topic-history/pagination/retry/auth (DOM + real handlers; not rendered layout).');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{for(const w of windows)w.close();child.stdin.end();});
