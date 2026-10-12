/* UI-only history fixtures: node scripts/check_history_fold_ui.js */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const round = n => ({round_id:`round-${n}`,title:`问答 ${n}`,human_name:'南杉',ai_name:'小机一',status:'complete',updated_at:'2026-10-12T12:00:00+08:00'});
const duel = (n,total=n) => ({available:true,total,matches:Array.from({length:n},(_,i)=>({game_type:'gomoku',outcome:i%2?'win':'loss',opponents:[`对手${i}`],time:'2026-10-12T12:30:00+08:00',result_detail:`结果详情${i}`}))});
const fixture = () => ({self:{username:'南杉',saves:{eco:{slots:[{slot:1,day:3},{slot:2,day:6}]}},duel_history:duel(11),deeptalk_history:{total:4,rounds:[round(1),round(2)]}},machines:[{username:'小机一',saves:{},duel_history:duel(3)},{username:'小机二',saves:{},duel_history:duel(7,31)},{username:'小机三',saves:{}}]});
let data=fixture(), pending, slow=false, fail=false;
const requests=[];
const dom = new JSDOM(html,{url:'https://history.test',runScripts:'dangerously',pretendToBeVisual:true,beforeParse(window){
  window.fetch=async url=>{
    if(url==='/api/auth/history')return {ok:true,json:async()=>structuredClone(data)};
    if(String(url).startsWith('/api/deeptalk?')){
      requests.push(url);
      if(slow)await new Promise(resolve=>{pending=resolve;});
      return {ok:!fail,json:async()=>fail?{error:'加载失败'}:{total:4,rounds:[round(3),round(4)]}};
    }
    return {ok:true,json:async()=>({count:0,announcements:[],unread_count:0})};
  };
}});
const w=dom.window,d=w.document;
const byId=id=>d.getElementById(id);
const fold=key=>Array.from(d.querySelectorAll('[data-history-fold]')).find(el=>el.dataset.historyFold===key);
const section=key=>Array.from(d.querySelectorAll('[data-history-section]')).find(el=>el.dataset.historySection===key);
const tab=id=>d.querySelector(`[data-history-tab="${id}"]`).click();
const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
const open=()=>w.openHistory();
(async()=>{
  await flush();w.eval('me={user:{username:"南杉"}}');w.localStorage.setItem('cedartoy_token','fixture-only');
  await open();
  assert.equal(d.querySelectorAll('[data-history-fold][aria-expanded="true"]').length,0);
  for(const button of d.querySelectorAll('[data-history-fold]')){
    assert.equal(button.tagName,'BUTTON');assert.equal(byId(button.getAttribute('aria-controls')).hidden,true);
  }
  assert.match(fold('duel').textContent,/共 11 局/);
  fold('duel').click();assert.equal(fold('duel').getAttribute('aria-expanded'),'true');
  assert.equal(section('duel').querySelectorAll('.history-duel-match').length,5);
  assert.match(section('duel').textContent,/五子棋[\s\S]*对手[\s\S]*时间[\s\S]*结果详情/);
  byId('duelHistoryMore').focus();byId('duelHistoryMore').click();
  assert.equal(section('duel').querySelectorAll('.history-duel-match').length,10);
  assert.equal(d.activeElement,byId('duelHistoryMore'));
  assert.match(byId('duelHistoryMore').textContent,/再显示 1 局/);
  byId('duelHistoryMore').click();
  assert.equal(section('duel').querySelectorAll('.history-duel-match').length,11);
  assert.equal(byId('duelHistoryMore'),null);
  assert.equal(d.activeElement,section('duel').querySelector('[role="status"]'));
  fold('game:eco').click();fold('deeptalk').click();
  const duelBody=section('duel').querySelector('.history-fold-body');
  await byId('deeptalkHistoryMore').onclick();
  assert.equal(section('deeptalk').querySelectorAll('.history-duel-match').length,4);
  assert.equal(fold('deeptalk').getAttribute('aria-expanded'),'true');
  assert.equal(fold('game:eco').getAttribute('aria-expanded'),'true');
  assert.equal(section('duel').querySelector('.history-fold-body'),duelBody,'other sections are not replaced');
  assert.match(requests.at(-1),/offset=2$/);
  tab('machine-0');assert.equal(fold('duel').getAttribute('aria-expanded'),'false');
  fold('duel').click();assert.equal(section('duel').querySelectorAll('.history-duel-match').length,3);
  assert.equal(byId('duelHistoryMore'),null);
  tab('machine-1');assert.equal(fold('duel').getAttribute('aria-expanded'),'false');
  fold('duel').click();byId('duelHistoryMore').click();
  assert.equal(section('duel').querySelectorAll('.history-duel-match').length,7);
  assert.match(section('duel').textContent,/仅提供最近 7 局，共 31 局/);
  assert.equal(byId('duelHistoryMore'),null);
  tab('self');assert.equal(fold('duel').getAttribute('aria-expanded'),'true');
  assert.equal(section('duel').querySelectorAll('.history-duel-match').length,11);
  fold('duel').click();assert.equal(fold('duel').getAttribute('aria-expanded'),'false');
  assert.equal(byId(fold('duel').getAttribute('aria-controls')).hidden,true);
  tab('machine-2');assert.match(byId('historyContent').textContent,/还没有游戏记录/);

  // Delayed pagination cannot rerender another identity, duplicate a request or reset folds.
  await open();fold('deeptalk').click();slow=true;
  const loading=byId('deeptalkHistoryMore').onclick();
  const requestCount=requests.length;
  await byId('deeptalkHistoryMore').onclick();assert.equal(requests.length,requestCount);
  tab('machine-0');fold('duel').click();const markup=byId('historyContent').innerHTML;
  pending();await loading;slow=false;
  assert.equal(byId('historyContent').innerHTML,markup);
  tab('self');assert.equal(fold('deeptalk').getAttribute('aria-expanded'),'true');
  assert.equal(section('deeptalk').querySelectorAll('.history-duel-match').length,4);

  // Switching away and back while a page is pending retains the disabled control.
  await open();fold('deeptalk').click();slow=true;
  const backLoading=byId('deeptalkHistoryMore').onclick();
  tab('machine-0');tab('self');assert.equal(byId('deeptalkHistoryMore').disabled,true);
  fold('deeptalk').click();pending();await backLoading;slow=false;
  assert.equal(fold('deeptalk').getAttribute('aria-expanded'),'false');
  assert.equal(byId(fold('deeptalk').getAttribute('aria-controls')).hidden,true);

  // A response from a previous modal session must not mutate the newly opened one.
  await open();fold('deeptalk').click();slow=true;
  const stale=byId('deeptalkHistoryMore').onclick();await open();
  const reopened=byId('historyContent').innerHTML;pending();await stale;slow=false;
  assert.equal(byId('historyContent').innerHTML,reopened);
  assert.equal(section('deeptalk').querySelectorAll('.history-duel-match').length,2);

  fail=true;fold('deeptalk').click();await byId('deeptalkHistoryMore').onclick();
  assert.equal(byId('deeptalkHistoryMore').disabled,false);
  assert.equal(fold('deeptalk').getAttribute('aria-expanded'),'true');
  assert.equal(section('deeptalk').querySelectorAll('.history-duel-match').length,2);
  fail=false;await byId('deeptalkHistoryMore').onclick();
  assert.equal(section('deeptalk').querySelectorAll('.history-duel-match').length,4);

  for(const h of [undefined,{available:false},duel(0),duel(0,31)]){
    data={self:{saves:{},duel_history:h},machines:[]};await open();
    if(h){fold('duel').click();assert.equal(byId('duelHistoryMore'),null);}
    else assert.match(byId('historyContent').textContent,/还没有游戏记录/);
  }
  const evil='<img src=x onerror="window.historyXSS=1">'+('VeryLongTitle'.repeat(12));
  data={self:{username:evil,saves:{[evil]:{slots:[]}},duel_history:{total:1,matches:[{game_type:evil,outcome:'win',opponents:[evil],result_detail:evil,time:evil}]},deeptalk_history:{rounds:[{...round(1),title:evil,human_name:evil,ai_name:evil}],total:1}}};
  await open();
  assert.equal(byId('historyContent').querySelector('img'),null);
  assert.equal(w.historyXSS,undefined);
  assert.match(fold(`game:${evil}`).textContent,/暂无存档/);
  for(const button of d.querySelectorAll('[data-history-fold]')){
    button.click();assert.equal(byId(button.getAttribute('aria-controls')).hidden,false);
  }
  console.log('PASS: syntax, default folds, 5→10→11, finite API history, per-subject state, DeepTalk pagination/failure/races, empty records, XSS, ARIA and focus.');
} )().catch(err=>{console.error(err);process.exitCode=1;}).finally(()=>w.close());
