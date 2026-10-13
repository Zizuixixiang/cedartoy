'use strict';
// Execute the real homepage with mocked APIs; no real accounts or data access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM, VirtualConsole} = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const content = '首段\n\n第二段 https://example.test/?a=1&b=2\n<em>仍为纯文本</em>';
let items = [
  {id:'notice', title:'更新通知', content, unread:true},
  {id:'poll', title:'玩法投票', content:'请选择', type:'poll', multiple:true, allow_feedback:true, options:['合作','探索'], unread:true},
  {id:'voted', title:'已投票', type:'poll', options:['一','二'], my_vote:{options:[2],feedback:'原有意见'}, unread:false},
  {id:'long', title:'<img src=x onerror=alert(1)>'.repeat(10), content:'历史公告', unread:false},
].map(item=>({created_at:'2026-10-14 12:30:00', target_game:'all', ...item}));
const calls=[], errors=[];
let failVote=false, failLoad=false, pendingVote=null, resolveVote;
const virtualConsole=new VirtualConsole();
virtualConsole.on('jsdomError',err=>errors.push(err.message));
const reply=(data,ok=true)=>({ok,json:async()=>JSON.parse(JSON.stringify(data))});
const dom=new JSDOM(html,{url:'https://announcements.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole,
  beforeParse(w) {
    w.fetch=async(url,init={})=>{
      calls.push({url,init});
      if(url==='/api/announcements') return failLoad ? reply({error:'测试加载失败'},false)
        : reply({authenticated:!!init.headers?.Authorization, announcements:items, unread_count:items.filter(x=>x.unread).length});
      if(url==='/api/announcements/read') {
        const ids=JSON.parse(init.body).announcement_ids;
        items.forEach(item=>{if(ids.includes(item.id))item.unread=false;});
        return reply({ok:true});
      }
      if(url==='/api/announcements/vote') {
        if(pendingVote) await pendingVote;
        if(failVote) return reply({error:'测试提交失败'},false);
        const data=JSON.parse(init.body), vote={options:data.options.filter(n=>n>0),skipped:data.options.includes(0),feedback:data.feedback};
        items.find(x=>x.id===data.announcement_id).my_vote=vote;
        return reply({vote});
      }
      return reply({count:0});
    };
  }});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
const fold=id=>d.querySelector(`[data-announcement-toggle="${id}"]`);
const body=id=>$(fold(id).getAttribute('aria-controls'));
const form=()=>body('poll').querySelector('form');
const writes=()=>calls.filter(x=>x.init.method==='POST');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
function expanded(id) {
  assert.equal(d.querySelectorAll('[data-announcement-toggle][aria-expanded="true"]').length,id?1:0);
  assert.equal(d.querySelectorAll('.announcement-body:not([hidden])').length,id?1:0);
  if(id)assert.equal(body(id).hidden,false);
}
function input(selector,value) {
  const el=form().querySelector(selector);
  if(el.type==='textarea'){el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));}
  else el.click();
}
(async()=>{
  await tick(); w.localStorage.setItem('cedartoy_token','fixture-only');
  await w.loadAnnouncements({autoPopup:true}); expanded(null);
  assert.equal(d.querySelectorAll('.announcement-item').length,2);
  assert.equal(d.querySelectorAll('.announcement-unread').length,2);
  assert.deepEqual(JSON.parse(writes()[0].init.body),{announcement_ids:['notice','poll']});
  assert.match($('announcementHint').textContent,/已读不等于投票/);
  await w.openAnnouncementList(); expanded(null);
  assert.deepEqual([...d.querySelectorAll('.announcement-item')].map(x=>x.dataset.announcementItem),items.map(x=>x.id));
  assert.equal(d.querySelectorAll('.announcement-unread').length,0);
  assert.equal(d.querySelectorAll('#announcementList img').length,0);
  assert.equal(fold('long').querySelector('.announcement-title').textContent,items[3].title);
  for(const button of d.querySelectorAll('[data-announcement-toggle]')) {
    assert.equal(button.tagName,'BUTTON'); assert.equal(button.type,'button');
    assert.match(button.textContent,/2026-10-14 12:30:00/);
    assert.equal(button.querySelector('.announcement-arrow').getAttribute('aria-hidden'),'true');
  }
  fold('notice').click(); expanded('notice');
  assert.equal(body('notice').querySelector('.announcement-content').textContent,content);
  assert.equal(body('notice').querySelector('em'),null);
  fold('poll').focus(); fold('poll').click(); expanded('poll');
  assert.equal(d.activeElement,fold('poll'));
  const originalForm=form(); input('[value="1"]'); input('textarea','我的草稿');
  fold('poll').click(); expanded(null); fold('notice').click(); fold('poll').click(); expanded('poll');
  assert.equal(form(),originalForm); assert.equal(form().querySelector('[value="1"]').checked,true);
  assert.equal(form().querySelector('textarea').value,'我的草稿');
  assert.equal(writes().length,1,'folding sends neither read nor vote requests');
  input('[value="1"]'); await w.submitAnnouncementVote(form()); expanded('poll');
  assert.match(body('poll').textContent,/请选择至少一个选项/); assert.equal(writes().length,1);
  input('[value="1"]'); failVote=true; await w.submitAnnouncementVote(form()); expanded('poll');
  assert.match(body('poll').textContent,/测试提交失败/);
  assert.equal(form().querySelector('textarea').value,'我的草稿');
  failVote=false; input('[data-poll-skip]');
  assert.equal(form().querySelector('[value="1"]').checked,false);
  await w.submitAnnouncementVote(form()); expanded('poll');
  assert.match(body('poll').textContent,/已跳过本次投票/); assert.ok(form());
  input('[value="2"]'); assert.equal(form().querySelector('[data-poll-skip]').checked,false);
  // A delayed submit must preserve a newer fold choice and cannot submit twice.
  pendingVote=new Promise(resolve=>{resolveVote=resolve;});
  const saving=w.submitAnnouncementVote(form()); assert.equal(form().querySelector('[type=submit]').disabled,true);
  const count=writes().length; await w.submitAnnouncementVote(form()); assert.equal(writes().length,count);
  fold('notice').click(); resolveVote(); await saving; expanded('notice'); pendingVote=null;
  fold('poll').click(); expanded('poll'); assert.equal(form(),null);
  assert.match(body('poll').textContent,/本人已投[\s\S]*探索[\s\S]*我的草稿/);
  assert.deepEqual(JSON.parse(writes().at(-1).init.body),{announcement_id:'poll',options:[2],feedback:'我的草稿'});
  fold('voted').click(); expanded('voted'); assert.match(body('voted').textContent,/本人已投[\s\S]*原有意见/);
  await w.openAnnouncementList(); expanded(null); assert.equal($('announcementList').scrollTop,0);
  w.localStorage.removeItem('cedartoy_token'); const beforeGuest=writes().length;
  await w.openAnnouncementList(); fold('poll').click(); expanded('poll');
  assert.match(body('poll').textContent,/登录人类账号后可参与投票/); assert.equal(form(),null);
  assert.equal(writes().length,beforeGuest);
  items=[]; await w.openAnnouncementList(); assert.match($('announcementList').textContent,/暂无通知/);
  failLoad=true; await w.openAnnouncementList(); assert.match($('announcementList').textContent,/测试加载失败/);
  assert.deepEqual(errors,[]);
  console.log('PASS: syntax, default/exclusive folds, order/escaping/content, focus/ARIA, draft retention, read semantics, vote validation/failure/skip/success/race, reopen, guest/empty/error.');
})().catch(err=>{console.error(err);process.exitCode=1;}).finally(()=>w.close());
