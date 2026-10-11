// Rendered DeepTalk checks using disposable Python auth/session fixtures over stdio.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
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
    await page.screenshot({path:path.join(artifacts,`${label}-${width}.png`),fullPage:true});
  }
  for(const width of [360,430,1280]) {
    await page.setViewportSize({width,height:900});
    await page.goto('https://deeptalk.test/deeptalk?topic_id=relations_communication');
    await page.locator('#setup').waitFor({state:'visible'}); await settled();
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
    await page.locator('#answer').fill('浏览器布局测试草稿');
    await page.locator('#save').click(); await settled();
    await check('round',width);
    await page.reload(); await page.locator('#round').waitFor({state:'visible'}); await settled();
    await page.locator('#questionJump').selectOption('0'); await settled();
    assert.equal(await page.locator('#answer').inputValue(),'浏览器布局测试草稿');
    await page.locator('#sources summary').focus(); await page.keyboard.press('Enter');
    await page.locator('#sourceBody a').first().waitFor();
    await check('restored',width);
    await page.goto('https://deeptalk.test/deeptalk?topic_id=relations_communication');
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
    await page.locator('#save').click(); await settled();
    console.log(`PASS rendered ${width}px: fixed topic, shared round, save/reload, sources, keyboard, touch targets, no overflow`);
  }
  await page.setViewportSize({width:360,height:900});
  await page.goto('https://deeptalk.test/deeptalk?topic_id=learning_goals_problem_solving');
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
  await page.goto('https://deeptalk.test/deeptalk');
  await page.locator('#chooseTopic').waitFor({state:'visible'});
  assert.equal(await page.locator('#setup').isVisible(),false);
  await check('no-topic',360);
  await page.setViewportSize({width:1280,height:900});
  await page.goto('https://deeptalk.test/deeptalk?topic_id=relations_communication');
  await page.locator('#setup').waitFor({state:'visible'}); await settled();
  await page.evaluate(()=>document.body.style.zoom='2'); await check('zoom200',1280);
  assert.deepEqual(errors,[]);
  console.log('PASS: no-topic/no-binding states, source XSS & long content, 200% zoom; screenshots: '+artifacts);
})().catch(err=>{console.error(err);process.exitCode=1;}).finally(async()=>{if(browser) await browser.close();child.stdin.end();});
