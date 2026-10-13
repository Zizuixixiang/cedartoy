// Rendered DeepTalk checks using disposable Python auth/session fixtures over stdio.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
// HTTP on a non-localhost origin exercises the real insecure-context API surface.
const base = process.env.DEEPTALK_UUID_COMPAT ? 'http://deeptalk.test' : 'https://deeptalk.test';
const home = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const topicTitles = JSON.parse(home.match(/const deeptalkTopics = (\[[\s\S]*?\n    \]);/)[1]);
const child = spawn('python3', ['-u', 'scripts/deeptalk_test_server.py'], {cwd:root, stdio:['pipe','pipe','inherit']});
const queue = [];
let ready;
const tokensReady = new Promise(resolve => ready = resolve);
readline.createInterface({input:child.stdout}).on('line', line => {
  const value = JSON.parse(line);
  if (value.tokens) ready(value.tokens); else queue.shift()(value);
});
const transport = request => new Promise(resolve => { queue.push(resolve); child.stdin.write(JSON.stringify(request)+'\n'); });
const artifacts = process.env.SCREENSHOT_DIR || '/tmp/deeptalk-ui-review';
let browser;
(async () => {
  const tokens = await tokensReady;
  browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const page = await browser.newPage({reducedMotion:'reduce'});
  const errors=[];
  page.on('pageerror', err => errors.push(err.message));
  await page.addInitScript(token => localStorage.setItem('cedartoy_token', token), tokens[101]);
  let noBindings=false, hostile=false;
  await page.route('**/*', async route => {
    const request=route.request(), url=new URL(request.url());
    if(url.pathname==='/deeptalk') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'deeptalk.html'),'utf8')});
    if(!url.pathname.startsWith('/api/deeptalk')) return route.abort();
    const result=await transport({url:url.pathname+url.search,method:request.method(),headers:{...request.headers(), Authorization:request.headers().authorization || ''},body:request.postData()||''});
    if(noBindings && url.searchParams.get('action')==='context') result.body.machines=[];
    if(hostile && url.searchParams.get('action')==='source') result.body={title:'<img src=x onerror="window.xss=1">',entries:[{name:'长网址测试 '+ 'https://example.invalid/'+'x'.repeat(300),author:'<script>window.xss=1</script>',source_url:'javascript:alert(1)',license:'TEST',license_url:'data:text/html,unsafe'}],note:'测试声明'};
    await route.fulfill({status:result.status,contentType:'application/json',body:JSON.stringify(result.body)});
  });
  fs.mkdirSync(artifacts,{recursive:true});
  async function settled(){ await page.waitForFunction(()=>!busy); }
  async function check(label,width){
    await settled();
    const metrics=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      body:parseFloat(getComputedStyle(document.body).fontSize),hint:parseFloat(getComputedStyle(document.querySelector('.hint')).fontSize)}));
    assert.equal(metrics.scroll,metrics.width,label+' no horizontal overflow');
    assert.ok(metrics.hint<=metrics.body);
    for(const control of await page.locator('button:visible, select:visible, summary:visible').all()) {
      const box=await control.boundingBox(); assert.ok(box.height>=44,label+' touch target');
    }
    if(await page.locator('#editor').isVisible()) {
      const previous=await page.locator('#previous').boundingBox(), next=await page.locator('#next').boundingBox();
      assert.equal(previous.y,next.y,label+' navigation is one row');
      assert.ok(previous.width>=90 && next.width>=90,label+' usable navigation width');
    }
    await page.screenshot({path:path.join(artifacts,`${label}-${width}.png`),fullPage:true});
  }
  for(const width of [360,430,1280]) {
    await page.setViewportSize({width,height:900});
    for (const [, topicId, title] of topicTitles) {
      await page.goto(base+'/deeptalk?topic_id='+topicId);
      await page.locator('#setup').waitFor({state:'visible'}); await settled();
      assert.equal(await page.locator('#topicTitle').innerText(),title);
      assert.equal(await page.title(),title+' · CEDAR TOY');
      await check('title-'+topicId,width);
    }
    await page.goto(base+'/deeptalk?topic_id=relations_communication');
    await page.locator('#setup').waitFor({state:'visible'}); await settled();
    if (process.env.DEEPTALK_UUID_COMPAT) {
      assert.deepEqual(await page.evaluate(() => [isSecureContext, typeof crypto.randomUUID, typeof crypto.getRandomValues]), [false, 'undefined', 'function']);
    }
    assert.equal(await page.locator('#topic, #machine').count(),0);
    assert.equal(await page.locator('#sources').getAttribute('open'),null);
    await check('setup',width);
    await page.locator('#sources summary').click();
    await page.locator('#sourceBody a').first().waitFor();
    assert.equal(await page.locator('#sourceBody .source-entry').count(),3);
    assert.doesNotMatch(await page.locator('#sourceBody').innerText(),/OC|adaptation_note|question_ids|docs\//);
    await check('sources',width);
    await page.locator('#sources summary').click();
    await page.locator('#create').click(); await page.locator('#round').waitFor({state:'visible'}); await settled();
    assert.equal(await page.locator('#newRound').getAttribute('href'),'/deeptalk?topic_id=relations_communication');
    assert.equal(await page.locator('#save, #skip, #refresh').count(),0);
    assert.equal(await page.locator('#completion, #submit').count(),0);
    assert.equal(await page.locator('#newRound').isVisible(),false);
    const answerBox=await page.locator('#answer').boundingBox();
    assert.ok(answerBox.height>=110 && answerBox.height<=140,'compact initial textarea');
    const initialMetrics=await page.evaluate(()=>({
      title:getComputedStyle(document.getElementById('roundTitle')).fontSize,
      hint:getComputedStyle(document.getElementById('roundDescription')).fontSize,
      controls:Array.from(document.querySelectorAll('.question-actions button'),el=>({width:el.offsetWidth,height:el.offsetHeight})),
      status:document.getElementById('questionJump').selectedOptions[0].textContent
    }));
    assert.equal(initialMetrics.status,'第1题 · ○待回答');
    assert.equal(await page.locator('#editor button').count(),2,'only the navigation row');
    console.log(`METRICS ${width}px: ${JSON.stringify(initialMetrics)}`);
    await check('first-question',width);
    await page.locator('#answer').fill('浏览器布局测试草稿');
    await page.locator('#next').click(); await settled();
    await check('round',width);
    await page.reload(); await page.locator('#round').waitFor({state:'visible'}); await settled();
    await page.locator('#questionJump').selectOption('0'); await settled();
    assert.equal(await page.locator('#answer').inputValue(),'浏览器布局测试草稿');
    await page.locator('#sources summary').focus(); await page.keyboard.press('Enter');
    await page.locator('#sourceBody a').first().waitFor();
    await check('restored',width);
    await page.goto(base+'/deeptalk?topic_id=relations_communication');
    await page.locator('#setup').waitFor({state:'visible'}); await settled();
    await page.locator('#mode').selectOption('chat');
    await page.locator('#create').click(); await page.locator('#round').waitFor({state:'visible'}); await settled();
    const rid=await page.evaluate(()=>roundId), qid=await page.evaluate(()=>state.questions[0].id);
    await page.locator('#answer').fill('正在输入的回答'); await page.locator('#answer').focus();
    const mcp=await transport({kind:'mcp',token:tokens[201],payload:{jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'play',arguments:{game:'deeptalk',action:'answer',params:{round_id:rid,question_id:qid,text:'小机此刻的回答',revision:0,request_id:'browser_live_1'}}}}});
    assert.equal(mcp.result.isError,false);
    await page.waitForFunction(()=>document.getElementById('peerText').textContent==='小机此刻的回答',{},{timeout:12000});
    assert.equal(await page.locator('#answer').inputValue(),'正在输入的回答');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'answer');
    await check('chat-live',width);
    await page.locator('#next').click(); await settled();
    // Long answers remain scrollable and the textarea keeps its resize affordance.
    await page.locator('#answer').fill('可写很长的回答。'.repeat(500));
    assert.equal(await page.locator('#answer').evaluate(el=>getComputedStyle(el).resize),'vertical');
    await page.locator('#answer').evaluate(el=>el.style.height='260px');
    await check('long-answer',width);
    await page.locator('#next').click(); await settled();
    // Complete through the public UI, then inspect cancel/confirm and locked states.
    const remaining=await page.evaluate(()=>state.total-index);
    for(let i=0;i<remaining;i++) { await page.locator('#next').click(); await settled(); }
    await page.locator('#confirmDialog').waitFor({state:'visible'});
    assert.equal(await page.evaluate(()=>state.submitted),false);
    await check('confirm-submit',width);
    await page.locator('#cancelSubmit').click();
    assert.equal(await page.locator('#newRound').isVisible(),false);
    assert.equal(await page.locator('#next').innerText(),'提交答案');
    assert.match(await page.locator('#questionJump').inputValue(),/^12$/);
    await check('ready-to-submit',width);
    await page.locator('#questionJump').selectOption('0'); await settled();
    assert.equal(await page.locator('#next').innerText(),'提交答案');
    await page.locator('#answer').fill('回看后修改，仍由单排按钮提交');
    await check('review-edited',width);
    await page.locator('#next').click(); await settled();
    await page.locator('#confirmSubmit').click(); await settled();
    assert.equal(await page.locator('#answer').isVisible(),false);
    assert.equal(await page.locator('#newRound').isVisible(),true);
    await check('locked-waiting',width);
    assert.equal(await page.locator('#newRound').innerText(),'重新答题');
    const submittedUrl=page.url();
    await page.locator('#newRound').click(); await page.locator('#setup').waitFor({state:'visible'}); await settled();
    assert.equal(new URL(page.url()).searchParams.get('topic_id'),'relations_communication');
    await page.locator('#create').click(); await page.locator('#round').waitFor({state:'visible'}); await settled();
    assert.notEqual(await page.evaluate(()=>roundId),rid);
    assert.equal(await page.evaluate(()=>state.progress.human.done),0);
    assert.equal(await page.locator('#newRound').isVisible(),false);
    await page.goto(submittedUrl); await page.locator('#round').waitFor({state:'visible'}); await settled();
    assert.equal(await page.evaluate(()=>state.submitted),true);
    assert.equal(await page.evaluate(()=>state.questions[0].own.text),'回看后修改，仍由单排按钮提交');
    console.log(`PASS rendered ${width}px: fixed topic, shared round, autosave/reload, one-row navigation, long input, final confirmation/lock, sources, keyboard, touch targets, no overflow`);
  }
  await page.setViewportSize({width:360,height:900});
  await page.goto(base+'/deeptalk?topic_id=learning_goals_problem_solving');
  await page.locator('#setup').waitFor({state:'visible'}); await settled();
  await page.locator('#sources summary').click(); await page.locator('#sourceBody a').first().waitFor();
  await check('learning-sources',360);
  hostile=true;
  await page.reload(); await page.locator('#setup').waitFor({state:'visible'}); await settled();
  await page.locator('#sources summary').click(); await page.locator('#sourceBody .source-entry').waitFor();
  assert.equal(await page.locator('#sourceBody a, #sourceBody img, #sourceBody script').count(),0);
  assert.equal(await page.evaluate(()=>window.xss),undefined);
  await check('hostile-long-source',360); hostile=false;
  noBindings=true;
  await page.reload(); await page.locator('#setup').waitFor({state:'visible'}); await settled();
  await page.locator('#mode').selectOption('chat');
  assert.equal(await page.locator('#create').isDisabled(),true);
  await page.locator('#mode').selectOption('blind');
  assert.equal(await page.locator('#create').isDisabled(),true);
  await check('no-binding',360);
  await page.goto(base+'/deeptalk');
  await page.locator('#chooseTopic').waitFor({state:'visible'});
  assert.equal(await page.locator('#setup').isVisible(),false);
  await check('no-topic',360);
  await page.setViewportSize({width:1280,height:900});
  await page.goto(base+'/deeptalk?topic_id=relations_communication');
  await page.locator('#setup').waitFor({state:'visible'}); await settled();
  await page.evaluate(()=>document.body.style.zoom='2'); await check('zoom200',1280);
  assert.deepEqual(errors,[]);
  console.log('PASS: no-topic/no-binding states, source XSS & long content, 200% zoom; screenshots: '+artifacts);
})().catch(err=>{console.error(err);process.exitCode=1;}).finally(async()=>{if(browser) await browser.close();child.stdin.end();});
