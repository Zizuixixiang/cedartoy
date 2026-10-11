"use strict";
// Uses an existing Playwright/Chromium installation; no dependency downloads.
// Serves only a temporary test process and temporary account/session databases.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cedar-scale-browser-'));
const screenshots = process.env.SCALE_SCREENSHOTS || path.join(os.tmpdir(), 'cedar-scale-screenshots');
const specs = fs.readdirSync(path.join(root,'research_scales/data')).filter(n=>n.endsWith('.json'))
  .map(n=>JSON.parse(fs.readFileSync(path.join(root,'research_scales/data',n),'utf8'))).filter(s=>!s.blocked);
const service = spawn('python3', ['-u','-c', `
from http.server import ThreadingHTTPServer
from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import server
for config in server.HUMAN_TEST_GAMES.values():
    config['handler'].DB_PATH = str(server.SESSIONS_DB_PATH)
httpd=ThreadingHTTPServer(('127.0.0.1',0),server.CedarToyHandler)
print(httpd.server_address[1],flush=True)
httpd.serve_forever()
`], {cwd:root,env:{...process.env,SESSIONS_DB:path.join(temp,'sessions.db'),TURTLE_SOUP_DB:path.join(temp,'accounts.db')},stdio:['ignore','pipe','pipe']});
let browser;
let stderr='';service.stderr.on('data',data=>{stderr+=data;});
const exited = new Promise(resolve=>service.once('exit',resolve));
async function main(){
  const port=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Test server startup timeout: '+stderr)),20000);
    service.once('error',reject);
    service.once('exit',code=>{clearTimeout(timer);reject(new Error('Test server exited '+code+': '+stderr));});
    service.stdout.once('data',data=>{clearTimeout(timer);resolve(Number(String(data).trim()));});
  });
  const origin='http://127.0.0.1:'+port;
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  fs.mkdirSync(screenshots,{recursive:true});
  for(const width of [360,430,1280]){
    const context=await browser.newContext({viewport:{width,height:900}});
    const page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.origin===origin && url.pathname==='/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'index.html'),'utf8')});
      const allowed=url.origin===origin && [...specs.map(s=>s.game),'mbti','ecr'].some(game=>url.pathname==='/'+game || url.pathname.startsWith('/api/'+game+'/'));
      if(allowed) return route.continue();
      // Homepage names/statistics need only synthetic aggregates, never live data.
      if(url.origin===origin && url.pathname.includes('/api/')) return route.fulfill({contentType:'application/json',body:url.pathname.includes('rooms')?'[]':'{}'});
      return route.abort();
    });
    async function capture(name){
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,name+' horizontal overflow');
      await page.screenshot({path:path.join(screenshots,`${width}-${name}.png`),fullPage:true});
    }
    await page.goto(origin+'/');
    await page.locator('[data-filter="test"]').click();
    const names=await page.evaluate(()=>Object.fromEntries(researchScales.map(([id,name])=>[id,name])));
    assert.equal(Object.keys(names).length,18);
    for(const spec of specs) {
      assert.equal(await page.locator(`[data-game="${spec.game}"] .game-title`).innerText(),names[spec.game]);
      assert.doesNotMatch(names[spec.game],/描述版|娱乐改编版/);
    }
    await page.locator('[data-game="rbi"]').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(screenshots,`${width}-home-titles.png`)});
    await page.evaluate(()=>openPlatformStats());
    for(const spec of specs) assert.equal(await page.locator(`.platform-dist-${spec.game} summary`).innerText(),`${names[spec.game]} · 分数分布`);
    await page.locator('.platform-dist-irrc').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(screenshots,`${width}-stats-titles.png`)});
    // Only research pages and the original MBTI/ECR references hit test handlers.
    for(const spec of specs){
      await page.goto(origin+'/'+spec.game);
      await page.locator('#answers button').first().waitFor();
      assert.equal(await page.locator('#title').innerText(),names[spec.game]);
      assert.equal(await page.title(),`${names[spec.game]} · CEDAR TOY`);
      assert.equal(await page.locator('.mode-button').count(),0);
      assert.equal(await page.locator('#researchDetails').getAttribute('open'),null);
      assert.equal(await page.locator('#pageDisclaimer').innerText(),'中文题目未经验证；仅供自我了解，不作诊断。');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,spec.game+' overflow');
      assert.equal(await page.locator('#researchSaveHint').count(),0);
      assert.equal(await page.locator('#saveAnswerButton').innerText(),'下一题');
      assert.equal(await page.locator('#saveAnswerButton').isDisabled(),true);
      const labels=await page.locator('#answers button').allTextContents();
      for(const [i,option] of spec.options.entries()) {
        if(String(option.value)===String(option.text)) assert.equal(labels[i],String(option.value));
      }
      await page.locator('#researchDetails summary').click();
      assert.equal(await page.locator('#source a').first().getAttribute('href'),spec.source);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,spec.game+' expanded source overflow');
      await page.locator('#researchDetails summary').click();
      const bounds=await page.locator('#answers button').evaluateAll(nodes=>nodes.map(n=>({width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height})));
      assert.ok(bounds.every(b=>b.height>=44 && b.width>=24),spec.game+' touch targets');
      if(['irrc','jrrs','rss'].includes(spec.game)) await capture(spec.game+'-title');
    }
    for(const game of ['rbi','ross','aidep','bsr']){
      await page.goto(origin+'/'+game);await page.locator('#answers button').first().waitFor();
      await capture(game+'-question');
      const sizes=await page.evaluate(()=>({
        hint:parseFloat(getComputedStyle(document.getElementById('pageDisclaimer')).fontSize),
        question:parseFloat(getComputedStyle(document.getElementById('questionText')).fontSize),
        targets:[...document.querySelectorAll('#answers button')].map(n=>n.getBoundingClientRect().height)
      }));
      assert.ok(sizes.hint<sizes.question && sizes.hint<=13);
      assert.ok(sizes.targets.every(h=>h>=44));
      if(game==='rbi'){
        await page.locator('#researchDetails summary').focus();await page.keyboard.press('Enter');
        assert.equal(await page.locator('#researchDetails').getAttribute('open'),'');
        await capture('rbi-details');
        await page.locator('#researchDetails summary').click();
        // Keyboard selection retains focus after rendering the selected state.
        await page.locator('#answers button').nth(1).focus();await page.keyboard.press('Space');
        assert.equal(await page.locator('#answers button.selected').evaluate(n=>n===document.activeElement),true);
        await page.locator('#answers button').nth(2).click();
        await page.reload();await page.locator('#answers button.selected').waitFor();
        assert.match(await page.locator('#answers button.selected').innerText(),/^3/);
        await page.locator('#saveAnswerButton').focus();await page.keyboard.press('Enter');
        await page.waitForFunction(()=>document.getElementById('questionNumber').textContent==='QUESTION 2');
        await page.reload();await page.locator('#answers button').first().waitFor();
        await page.locator('#previousButton').click();
        assert.equal(await page.locator('#answers button.selected').isDisabled(),true);
        await capture('rbi-revisit');
        await page.locator('#nextSavedButton').click();
        // A failed write stays on the same question, preserving the selection.
        await page.locator('#answers button').first().click();
        let release;
        const gate=new Promise(resolve=>{release=resolve;});
        const fail=async route=>{await gate;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'保存失败'})});};
        await page.route('**/api/rbi/answer',fail);
        await page.locator('#saveAnswerButton').click();
        assert.equal(await page.locator('#questionNumber').innerText(),'QUESTION 2');
        assert.equal(await page.locator('#saveAnswerButton').isDisabled(),true);
        release();await page.locator('#error').waitFor();
        assert.equal(await page.locator('#questionNumber').innerText(),'QUESTION 2');
        await capture('rbi-save-error');
        await page.unroute('**/api/rbi/answer',fail);
        await page.locator('#saveAnswerButton').click();
        await page.waitForFunction(()=>document.getElementById('questionNumber').textContent==='QUESTION 3');
        // Reflow with enlarged text and reduced motion.
        await page.emulateMedia({reducedMotion:'reduce'});
        await page.addStyleTag({content:'.research-page .question-text {font-size: 200%}'});
        await capture('rbi-large-text');
      }
    }
    await page.goto(origin+'/bsfl');await page.locator('#answers button').first().waitFor();
    for(let i=0;i<5;i++){
      assert.equal(await page.locator('#saveAnswerButton').innerText(),i===4?'查看结果':'下一题');
      await page.locator('#answers button').first().click();
      await page.locator('#saveAnswerButton').click();
      await page.waitForFunction(n=>document.getElementById('questionNumber').textContent===`QUESTION ${n}` || !document.getElementById('resultPanel').hidden,i+2);
    }
    await page.locator('#resultPanel').waitFor();
    assert.match(await page.locator('#resultText').innerText(),/0\.00/);
    await capture('bsfl-result');
    await page.reload();await page.locator('#resultPanel').waitFor();
    page.once('dialog',dialog=>dialog.dismiss());
    await page.locator('#restartButton').click();
    assert.equal(await page.locator('#resultPanel').isVisible(),true);
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#restartButton').click();await page.locator('#answers button').first().waitFor();
    await capture('bsfl-retest');
    // The established layouts remain the visual comparison, with auto-advance.
    for(const game of ['mbti','ecr']) {
      await page.goto(origin+'/'+game);
      if(game==='mbti') await page.locator('.mode-button').first().click();
      await page.locator('.scale-step').first().waitFor();
      assert.equal(await page.locator('#researchDetails').isVisible(),false);
      await capture(game+'-reference');
      await page.locator('.scale-step').first().click();
      assert.equal(await page.locator('#questionNumber').innerText(),'QUESTION 2');
    }
    assert.deepEqual(errors,[]);
    await context.close();
  }
  console.log('PASS: Chromium 360 / 430 / 1280px; 18 direct research pages, numeric labels, linked/folded notices, touch targets, keyboard/selection/reload/revisit, failed save/retry, enlarged text, BSFL zero/report/retest, MBTI/ECR reference; screenshots: '+screenshots);
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  service.kill('SIGTERM');await exited;
  fs.rmSync(temp,{recursive:true,force:true});
});
