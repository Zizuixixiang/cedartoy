'use strict';
// Real-browser recovery checks using real handlers over stdio and temporary databases.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cedar-resume-browser-'));
const shots = process.env.RESUME_SCREENSHOTS || path.join(os.tmpdir(), 'cedar-resume-screenshots');
const readline = require('node:readline');
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
const service=spawn('python3',['-u','-c',bridge],{cwd:root,env:{...process.env,SESSIONS_DB:path.join(temp,'sessions.db'),TURTLE_SOUP_DB:path.join(temp,'accounts.db')},stdio:['pipe','pipe','inherit']});
const queue=[];
readline.createInterface({input:service.stdout}).on('line',line=>queue.shift().resolve(JSON.parse(line)));
let browser;
const exited=new Promise(resolve=>service.once('exit',code=>{
  queue.splice(0).forEach(p=>p.reject(new Error('bridge exited '+code)));resolve();
}));
function request(url,body) {
  return new Promise((resolve,reject)=>{
    queue.push({resolve,reject});
    service.stdin.write(JSON.stringify({path:url,...(body===undefined?{}:{body})})+'\n');
  });
}
async function main() {
  const origin='https://fixture.invalid';
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  fs.mkdirSync(shots,{recursive:true});
  for(const width of [360,430,1280]) {
    const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'});
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==origin) return route.abort();
      const response=await request(url.pathname,route.request().method()==='POST'?route.request().postDataJSON():undefined);
      return route.fulfill({status:response.status,contentType:url.pathname.startsWith('/api/')?'application/json':'text/html',body:response.body});
    });
    const page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const player=`guest:webresumebrowser${width}`;
    const state=JSON.parse((await request('/api/romlon/start',{player_id:player,mode:'full_fast'})).body);
    const key=`cedartoy_romlon_web_draft_${player}`;
    const legacy={game:'romlon',edition:'full_fast',total:4,answers:[1,2,null,null],cursor:2};
    await page.addInitScript(({player,key,legacy})=>{
      if(!localStorage.getItem('fixture_seeded')) {
        localStorage.setItem('fixture_seeded','1');
        localStorage.setItem('cedartoy_guest_web_player_id',player);
        localStorage.setItem(key,JSON.stringify(legacy));
      }
    },{player,key,legacy});
    const ready=()=>page.waitForFunction(()=>!busy && activeState);
    await page.goto(origin+'/romlon');await ready();
    assert.equal(await page.locator('#progressLabel').innerText(),'3 / 4');
    assert.equal(await page.locator('#resumeDialog').isVisible(),false);
    await page.screenshot({path:path.join(shots,`${width}-restored.png`),fullPage:true});
    await page.locator('#previousButton').click();
    assert.equal(await page.locator('#answers button.selected').innerText(),'2');
    await page.locator('#answers button').nth(2).click();
    assert.equal(await page.locator('#progressLabel').innerText(),'3 / 4');
    await page.reload();await ready();
    assert.equal(await page.locator('#resumeDialog').isVisible(),false);
    assert.equal(await page.evaluate(()=>answers[1]),3);
    assert.equal(await page.evaluate(key=>localStorage.getItem(key),key),JSON.stringify(legacy),'legacy draft stays unchanged through edits and reload');
    // Create a genuinely different local branch without altering the server run.
    await page.evaluate(({key,state})=>localStorage.setItem(key,JSON.stringify({game:'romlon',edition:'full_fast',session_id:state.session_id,total:4,answers:[4,4,4,null],cursor:3})),{key,state});
    const conflictDrafts=await page.evaluate(()=>readResearchDrafts(activeState).map(e=>e.raw));
    await page.reload();await ready();
    await page.locator('#resumeDialog').waitFor({state:'visible'});
    assert.match(await page.locator('#resumeChoices').innerText(),/已答 2\/4.*第 1 题：1/s);
    assert.match(await page.locator('#resumeChoices').innerText(),/已答 3\/4.*第 1 题：4/s);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await page.locator('#resumeDialog').evaluate(n=>n.scrollWidth>n.clientWidth),false);
    for(const box of await page.locator('#resumeChoices button').evaluateAll(ns=>ns.map(n=>({w:n.getBoundingClientRect().width,h:n.getBoundingClientRect().height})))) {
      assert.ok(box.h>=44 && box.w>=44);
    }
    // Native Chromium may Tab out to browser chrome (BODY + !hasFocus).
    // That is different from focusing an interactive element behind the modal.
    for(const direction of ['Tab','Shift+Tab']) {
      await page.locator('#resumeChoices button').first().focus();
      const visited=new Set();
      for(let i=0;i<8;i++) {
        await page.keyboard.press(direction);
        const focus=await page.evaluate(()=>({
          inModal:!!document.activeElement.closest('#resumeDialog'),
          browserChrome:document.activeElement===document.body && !document.hasFocus(),
          index:[...document.querySelectorAll('#resumeChoices button')].indexOf(document.activeElement),
          modal:document.querySelector('#resumeDialog').matches(':modal')
        }));
        assert.equal(focus.modal,true);
        assert.ok(focus.inModal || focus.browserChrome,`${direction}: background page must remain inert`);
        if(focus.inModal) visited.add(focus.index);
      }
      assert.deepEqual([...visited].sort(),[0,1,2],`${direction}: all choices remain reachable`);
    }
    await page.locator('#resumeChoices button').first().focus();
    assert.equal(await page.evaluate(()=>{
      for(const node of document.querySelectorAll('a,button,input,summary,[tabindex]')) {
        if(node.closest('#resumeDialog')) continue;
        node.focus();
        if(!document.activeElement.closest('#resumeDialog')) return false;
      }
      return true;
    }),true,'background elements reject focus while modal is open');
    await page.screenshot({path:path.join(shots,`${width}-conflict.png`),fullPage:true});
    await page.locator('#resumeChoices button').filter({hasText:'已答 3/4'}).click();
    assert.equal(await page.locator('#progressLabel').innerText(),'4 / 4');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'questionText','auto-opened conflict returns focus to resumed question');
    await page.reload();await ready();
    assert.equal(await page.locator('#resumeDialog').isVisible(),false,'selected legacy copy does not re-prompt');
    assert.deepEqual(await page.evaluate(()=>answers),[4,4,4,null],'current slot wins even when an ignored legacy copy has the same signature');
    assert.equal(await page.locator('#resumeProgressButton').count(),0);
    assert.equal(await page.locator('#questionText').evaluate(n=>getComputedStyle(n).outlineStyle),'none');
    assert.ok(await page.evaluate(raws=>raws.every(raw=>Object.values({...localStorage}).includes(raw)),conflictDrafts),'all original conflicting drafts remain intact');
    // Trigger a new real conflict after resolving the first one; the deleted menu is not needed.
    await page.evaluate(({key,legacy})=>localStorage.setItem(key,JSON.stringify({...legacy,answers:[2,2,null,null]})),{key,legacy});
    await page.reload();await ready();
    await page.locator('#resumeDialog').waitFor({state:'visible'});
    const beforeRestartCancel=await page.evaluate(()=>JSON.stringify({sid:activeState.session_id,answers,storage:{...localStorage}}));
    await page.locator('#resumeDialog').getByRole('button',{name:'重新开始',exact:true}).click();
    assert.equal(await page.evaluate(()=>!!document.activeElement.closest('#resumeDialog')),true,'nested confirmation keeps modal focus');
    await page.screenshot({path:path.join(shots,`${width}-restart.png`),fullPage:true});
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>JSON.stringify({sid:activeState.session_id,answers,storage:{...localStorage}})),beforeRestartCancel,'restart cancellation leaves all progress intact');
    await page.locator('#resumeDialog').waitFor({state:'visible'});
    assert.equal(await page.evaluate(()=>!!document.activeElement.closest('#resumeDialog')),true,'unresolved conflict keeps focus after cancelling restart');
    await page.locator('#resumeDialog').getByRole('button',{name:'重新开始',exact:true}).click();
    await page.locator('#resumeDialog').getByRole('button',{name:'重新开始',exact:true}).click();
    await ready();
    assert.equal(await page.locator('#progressLabel').innerText(),'1 / 4');
    assert.notEqual(await page.evaluate(()=>activeState.session_id),state.session_id);
    // The same modal is also used on a completed result with a real opener.
    for(let i=0;i<4;i++) { await page.locator('#answers button').first().click();await ready(); }
    await page.locator('#saveAnswerButton').click();await ready();
    await page.locator('#resultPanel').waitFor();
    const completed=await page.evaluate(()=>JSON.stringify({state:activeState,storage:{...localStorage}}));
    await page.locator('#restartButton').click();
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'restartButton','result cancel restores retest opener');
    assert.equal(await page.evaluate(()=>JSON.stringify({state:activeState,storage:{...localStorage}})),completed,'Escape preserves completed result and drafts');
    await page.locator('#restartButton').click();
    await page.locator('#resumeChoices button').first().click();
    assert.equal(await page.evaluate(()=>document.activeElement.id),'restartButton','keep result restores retest opener');
    assert.equal(await page.evaluate(()=>JSON.stringify({state:activeState,storage:{...localStorage}})),completed,'keep result preserves completed result and drafts');
    await page.reload();await ready();
    await page.locator('#resultPanel').waitFor();
    assert.equal(await page.evaluate(()=>JSON.stringify({state:activeState,storage:{...localStorage}})),completed,'historical result survives reload');
    // Inspect the changed scale at narrow/wide phone and desktop widths.
    await page.goto(origin+'/mesx');await ready();
    const meanings=['不纳入道德关怀范围','最低程度关怀，不认为有责任','会关心但责任较低','高度关怀，愿意承担责任'];
    assert.deepEqual(await page.locator('.mesx-circle-key dd').allTextContents(),meanings);
    for(const definition of await page.locator('.mesx-circle-key dd').all()) assert.equal(await definition.isVisible(),true);
    assert.equal(await page.locator('.research-scale-key').getAttribute('open'),null);
    assert.equal(await page.locator('#progressLabel').innerText(),'1 / 10');
    assert.equal(await page.locator('#resumeProgressButton').count(),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(shots,`${width}-mesx-first.png`),fullPage:true});
    await page.locator('#answers button').nth(2).click();await ready();
    assert.equal(await page.locator('#progressLabel').innerText(),'2 / 10');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'questionText');
    assert.equal(await page.locator('#questionText').evaluate(n=>getComputedStyle(n).outlineStyle),'none');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.matches('#answers button')),true);
    assert.equal(await page.evaluate(()=>document.activeElement.matches(':focus-visible')),true);
    assert.equal(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle),'solid');
    await page.screenshot({path:path.join(shots,`${width}-mesx-keyboard.png`),fullPage:true});
    await page.reload();await ready();
    assert.equal(await page.locator('#progressLabel').innerText(),'2 / 10');
    await page.locator('.research-scale-key summary').click();
    assert.equal(await page.locator('.research-scale-key p').count(),4);
    await page.locator('.research-scale-key summary').click();
    await page.addStyleTag({content:'.mesx-circle-key {font-size:24px}'});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(shots,`${width}-mesx-enlarged.png`),fullPage:true});
    assert.deepEqual(errors,[]);
    await context.close();
  }
  console.log('PASS: 360/430/1280px legacy resume, answer edits, reload, conflict choice, progress retention, native modal isolation, bidirectional keyboard traversal, close/cancel focus restoration, unchanged drafts/results on cancel, touch sizing, explicit restart; MESx visible definitions, heading and keyboard focus, enlarged help. Screenshots: '+shots);
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  service.kill();await exited;
  fs.rmSync(temp,{recursive:true,force:true});
});
