// DOM + real Python HTTP/MCP handlers over stdio; does not claim rendered layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const topicId = process.env.DEEPTALK_TOPIC || 'learning_goals_problem_solving';
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
  let failResume = false, failView = false, afterResume = null;
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
        window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new window.Event('close'));};
        window.fetch = async (url, options={}) => {
          if (failResume && String(url).includes('action=resume&')) throw new TypeError('resume offline');
          if (failView && String(url).includes('action=view&')) throw new TypeError('view offline');
          const result = await transport({url, ...options});
          if (afterResume && String(url).includes('action=resume&')) {
            const hook=afterResume; afterResume=null; await hook(result.body.round_id);
          }
          return {ok:result.status<400,status:result.status,json:async()=>result.body};
        };
      }
    });
    windows.push(dom.window); return dom.window;
  }
  const $ = (w,id)=>w.document.getElementById(id);
  async function click(w,id) { assert.equal($(w,id).disabled,false,id+' enabled'); $(w,id).click(); await until(()=>!w.eval('busy'),id); }
  async function jump(w,index) { $(w,'questionJump').value=String(index); $(w,'questionJump').dispatchEvent(new w.Event('change')); await until(()=>!w.eval('busy'),'jump'); }
  function input(w,text) { $(w,'answer').value=text; $(w,'answer').dispatchEvent(new w.Event('input')); }
  function sessionCopy(w) { return Object.fromEntries(Array.from({length:w.sessionStorage.length}, (_,i) => {
    const key=w.sessionStorage.key(i); return [key,w.sessionStorage.getItem(key)];
  })); }
  async function read(action, params={}, user=101) {
    const result=await transport({url:'/api/deeptalk?'+new URLSearchParams({action,...params}),headers:{Authorization:'Bearer '+tokens[user]}});
    assert.equal(result.status,200,JSON.stringify(result.body)); return result.body;
  }
  async function write(action, params={}, user=101) {
    const result=await transport({url:'/api/deeptalk/action',method:'POST',headers:{Authorization:'Bearer '+tokens[user]},body:JSON.stringify({action,...params})});
    assert.equal(result.status,200,JSON.stringify(result.body)); return result.body;
  }
  async function aiWrite(action, params) {
    const result=await transport({kind:'mcp',token:tokens[201],payload:{jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'play',arguments:{game:'deeptalk',action,params}}}});
    assert.equal(result.result.isError,false,JSON.stringify(result));
  }
  async function opened(search, visible, token=tokens[101], session={}) {
    const w=page(search,token,session);
    await until(()=>!w.eval('busy'),'page settled'); assert.equal($(w,visible).hidden,false,visible); return w;
  }
  const entry='?topic_id='+topicId, setup='?new=1&topic_id='+topicId;
  let w=await opened(entry,'setup');
  assert.equal($(w,'roundHistory').hidden,true);
  await click(w,'create'); const rid=w.eval('roundId');
  input(w,'first saved answer'); await click(w,'next');
  assert.equal(w.eval('index'),1);
  const before=await read('history',{topic_id:topicId});
  let resumed=await opened(entry,'round');
  assert.equal(resumed.eval('roundId'),rid); assert.equal(resumed.eval('index'),1);
  assert.equal(new URL(resumed.location.href).searchParams.get('round_id'),rid);
  assert.equal((await read('history',{topic_id:topicId})).total,before.total,'entry creates nothing');
  assert.equal(resumed.eval('state.mode'),'chat');
  assert.equal($(resumed,'progress').hidden,false,'chat shows both sides progress');
  assert.equal($(resumed,'newRound').textContent,'题库 / 历史');
  const selection=await opened(new URL($(resumed,'newRound').href).search,'setup');
  assert.equal($(selection,'roundHistory').open,false);
  assert.equal($(selection,'historyList').children.length,1,'single round lives inside collapsed history');
  assert.equal($(selection,'recentRound'),null);
  assert.equal($(selection,'mode'),null); await click(selection,'create'); const emptyId=selection.eval('roundId');
  assert.notEqual(emptyId,rid,'explicit create bypasses continuation');
  assert.equal($(selection,'progress').hidden,false);
  const again=await opened(entry,'round'); assert.equal(again.eval('roundId'),rid,'real progress beats newer empty');
  // A private local draft on another round is considered only after server ownership checks.
  await jump(selection,3); input(selection,'unsaved draft on question four');
  const localSession=sessionCopy(selection);
  const local=await opened(entry,'round',tokens[101],localSession);
  assert.equal(local.eval('roundId'),emptyId); assert.equal(local.eval('index'),3);
  assert.equal($(local,'answer').value,'unsaved draft on question four');
  assert.equal(local.eval('state.questions[3].own'),null,'local restoration does not save');
  const refreshed=await opened('?round_id='+emptyId,'round',tokens[101],sessionCopy(local));
  assert.equal($(refreshed,'answer').value,'unsaved draft on question four');
  assert.equal(refreshed.eval('index'),3);
  const stranger=await opened(entry,'setup',tokens[102],localSession);
  assert.equal($(stranger,'roundHistory').hidden,true); assert.equal($(stranger,'answer').value,'');
  // AI-first chat answers are visible while human keeps editing the resumed round.
  const ids=(await read('view',{round_id:rid})).questions.map(q=>q.id);
  for (let i=0;i<ids.length;i++) await aiWrite('answer',{round_id:rid,question_id:ids[i],revision:i,text:'AI submitted '+i,request_id:'resume_ai_'+i});
  await aiWrite('submit',{round_id:rid,revision:ids.length,confirm:true});
  let aiFirst=await opened(entry,'round'); assert.equal(aiFirst.eval('roundId'),rid);
  assert.equal(aiFirst.eval('state.submitted'),false); assert.equal($(aiFirst,'answer').readOnly,false);
  assert.equal($(aiFirst,'peerText').textContent,'AI submitted 1');
  for (let i=1;i<ids.length;i++) await click(aiFirst,'next');
  await click(aiFirst,'confirmSubmit');
  const submitted=await opened('?round_id='+rid,'round');
  assert.equal(submitted.eval('state.submitted'),true); assert.equal($(submitted,'records').hidden,false);
  const nextRound=await opened(entry,'round'); assert.equal(nextRound.eval('roundId'),emptyId);
  const history=await opened(setup,'setup');
  assert.equal($(history,'roundHistory').open,false);
  assert.ok([...$(history,'historyList').querySelectorAll('a')].some(a=>a.textContent==='查看双方回答' && a.href.endsWith(rid)));
  // Cancel causes no deletion. Preview contains metadata only, and confirmation names whole-round loss.
  async function preview(win,id) {
    const link=[...$(win,'historyList').querySelectorAll('a')].find(a=>a.href.endsWith(id));
    const button=link.parentElement.querySelector('button'); button.click();
    await until(()=>!win.eval('busy'),'delete preview'); assert.equal($(win,'deleteDialog').open,true);
  }
  await preview(history,emptyId);
  assert.match($(history,'deleteText').textContent,/双方答案.*无法恢复/);
  assert.doesNotMatch(JSON.stringify(history.eval('pendingDelete')),/first saved answer|AI submitted|questions|answers/);
  await click(history,'cancelDelete'); assert.equal((await read('view',{round_id:emptyId})).round_id,emptyId);
  // A peer write after opening confirmation blocks deletion, disables stale confirmation.
  await preview(history,emptyId);
  const eq=(await read('view',{round_id:emptyId})).questions[0].id;
  await aiWrite('answer',{round_id:emptyId,question_id:eq,text:'new AI write',revision:0,request_id:'delete_peer_race'});
  await click(history,'confirmDelete');
  assert.equal($(history,'deleteDialog').open,true); assert.match($(history,'deleteError').textContent,/新的作答或提交/);
  assert.equal($(history,'confirmDelete').disabled,true);
  await click(history,'cancelDelete'); await preview(history,emptyId); await click(history,'confirmDelete');
  assert.equal($(history,'deleteDialog').open,false);
  assert.equal((await read('history',{topic_id:topicId})).total,1);
  assert.equal($(history,'historyList').children.length,1);
  // An active tab sees an explicit error and keeps its unsaved text available for copying.
  await local.eval('pollPeer()');
  assert.equal(local.eval('roundMissing'),true); assert.equal($(local,'round').hidden,false);
  assert.equal($(local,'answer').value,'unsaved draft on question four'); assert.equal($(local,'answer').readOnly,true);
  assert.equal($(local,'next').disabled,true); assert.match($(local,'message').textContent,/删除或无法访问/);
  assert.ok(local.sessionStorage.getItem('deeptalk_draft:'+emptyId));
  const gone=page('?round_id='+emptyId); await until(()=>!gone.eval('busy'),'deleted old link');
  assert.match($(gone,'message').textContent,/不存在或无权/); assert.equal($(gone,'setup').hidden,true);
  // With only submitted rounds remaining, topic entry stays at setup with history folded.
  const onlySubmitted=await opened(entry,'setup'); assert.equal($(onlySubmitted,'roundHistory').open,false);
  assert.equal($(onlySubmitted,'historyList').querySelector('a').textContent,'查看双方回答');
  await preview(history,rid); await click(history,'confirmDelete');
  assert.equal($(history,'roundHistory').hidden,true); assert.equal((await read('history',{topic_id:topicId})).total,0);
  // Fully submitted readers also notice deletion rather than silently retaining a live-looking round.
  await submitted.eval('pollPeer()'); assert.equal(submitted.eval('roundMissing'),true);
  assert.equal($(submitted,'records').hidden,false);
  failResume=true; const offline=await opened(entry,'setup');
  assert.match($(offline,'message').textContent,/无法自动续答/); assert.equal($(offline,'create').disabled,false);
  failResume=false;
  // An uncertain old creation is resolved by continuation, so explicit new stays new.
  const body={topic_id:topicId,mode:'chat',request_id:'pending_auto_resume'};
  const pendingRound=await write('create',body);
  failView=true; const viewOffline=await opened(entry,'setup');
  assert.match($(viewOffline,'message').textContent,/无法读取上次答卷/); assert.equal($(viewOffline,'create').disabled,false);
  failView=false;
  const withPending=await opened(entry,'round',tokens[101],{deeptalk_pending_create:JSON.stringify({body,fingerprint:JSON.stringify({topic_id:topicId,mode:'chat'})})});
  assert.equal(withPending.eval('roundId'),pendingRound.round_id);
  assert.equal(withPending.sessionStorage.getItem('deeptalk_pending_create'),null);
  const explicit=await opened(setup,'setup',tokens[101],sessionCopy(withPending));
  await click(explicit,'create'); assert.notEqual(explicit.eval('roundId'),pendingRound.round_id);
  // Selection racing with submit/delete returns to setup without creating or editing.
  for(const item of (await read('history',{topic_id:topicId})).rounds) {
    const preview=await read('delete_preview',{round_id:item.round_id});
    await write('delete',{round_id:item.round_id,delete_version:preview.delete_version,confirm:true});
  }
  const race=await write('create',{topic_id:topicId,request_id:'resume_submit_race'});
  const raceQs=(await read('view',{round_id:race.round_id})).questions;
  for(let i=0;i<raceQs.length;i++) await write('answer',{round_id:race.round_id,question_id:raceQs[i].id,revision:i,skip:true,request_id:'race_human_'+i});
  afterResume=id=>write('submit',{round_id:id,revision:raceQs.length,confirm:true});
  const raceSubmitted=await opened(entry,'setup');
  assert.equal($(raceSubmitted,'round').hidden,true);
  assert.equal($(raceSubmitted,'historyList').querySelector('a').textContent,'查看上次答卷');
  const toDelete=await write('create',{topic_id:topicId,request_id:'resume_delete_race'});
  afterResume=async id=>{assert.equal(id,toDelete.round_id);const p=await read('delete_preview',{round_id:id});await write('delete',{round_id:id,delete_version:p.delete_version,confirm:true});};
  await opened(entry,'setup'); assert.equal((await read('history',{topic_id:topicId})).total,1);
  // Default entry can also resume a pre-existing blind round, without exposing drafts.
  const legacy=await transport({kind:'legacy_create',topic_id:topicId,request_id:'resume_legacy_blind'});
  const legacyQuestions=(await read('view',{round_id:legacy.round_id})).questions;
  await write('answer',{round_id:legacy.round_id,question_id:legacyQuestions[0].id,
    revision:0,text:'legacy human draft',request_id:'resume_legacy_human'});
  await aiWrite('answer',{round_id:legacy.round_id,question_id:legacyQuestions[0].id,
    revision:0,text:'legacy private AI draft',request_id:'resume_legacy_ai'});
  const legacyResumed=await opened(entry,'round');
  assert.equal(legacyResumed.eval('roundId'),legacy.round_id);
  assert.equal(legacyResumed.eval('state.mode'),'blind');
  assert.equal(legacyResumed.eval('index'),1);
  assert.equal($(legacyResumed,'progress').hidden,true);
  assert.equal($(legacyResumed,'peerAnswer').hidden,true);
  assert.doesNotMatch(legacyResumed.document.body.textContent,/legacy private AI draft/);
  const legacyHistory=await opened(setup,'setup');
  assert.equal($(legacyHistory,'roundHistory').open,false);
  assert.match($(legacyHistory,'historyList').textContent,/答完再看（旧模式）/);
  assert.deepEqual(errors,[]);
  console.log('PASS: automatic continuation / local draft & refresh / real-progress ranking / chat-only & explicit new round / identity isolation / AI-first visibility / folded complete & incomplete history / delete cancellation, stale peer write, confirmation, active-tab retention, submitted-reader deletion / no implicit create. Temporary DBs + real HTTP/MCP handlers.');
})().catch(err=>{console.error(err);process.exitCode=1;}).finally(()=>{for(const w of windows) w.close(); child.stdin.end();});
