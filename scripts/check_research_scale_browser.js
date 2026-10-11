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
      const allowed=url.origin===origin && (url.pathname==='/' || specs.some(s=>url.pathname==='/'+s.game || url.pathname.startsWith('/api/'+s.game+'/')));
      return allowed?route.continue():route.abort();
    });
    async function capture(name){
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,name+' horizontal overflow');
      await page.screenshot({path:path.join(screenshots,`${width}-${name}.png`),fullPage:true});
    }
    // Direct research pages only: no homepage or unrelated game work.
    for(const spec of specs){
      await page.goto(origin+'/'+spec.game);
      await page.locator('.answer-button').first().waitFor();
      assert.equal(await page.locator('.mode-button').count(),0);
      assert.equal(await page.locator('#researchDetails').getAttribute('open'),null);
      assert.equal(await page.locator('#pageDisclaimer').innerText(),'仅供娱乐/自我了解，不作诊断');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,spec.game+' overflow');
    }
    for(const game of ['rbi','ross','aidep','bsr']){
      await page.goto(origin+'/'+game);await page.locator('.answer-button').first().waitFor();
      await capture(game+'-question');
      const sizes=await page.evaluate(()=>({
        hint:parseFloat(getComputedStyle(document.getElementById('pageDisclaimer')).fontSize),
        question:parseFloat(getComputedStyle(document.getElementById('questionText')).fontSize),
        targets:[...document.querySelectorAll('.answer-button')].map(n=>n.getBoundingClientRect().height)
      }));
      assert.ok(sizes.hint<sizes.question && sizes.hint<=13);
      assert.ok(sizes.targets.every(h=>h>=44));
      if(game==='rbi'){
        await page.locator('#researchDetails summary').focus();await page.keyboard.press('Enter');
        assert.equal(await page.locator('#researchDetails').getAttribute('open'),'');
        await capture('rbi-details');
        await page.locator('#researchDetails summary').click();
        await page.locator('.answer-button').nth(2).click();
        await page.reload();await page.locator('.answer-button.selected').waitFor();
        assert.match(await page.locator('.answer-button.selected').innerText(),/^3/);
        await page.locator('#saveAnswerButton').focus();await page.keyboard.press('Enter');
        await page.waitForFunction(()=>document.getElementById('questionNumber').textContent==='QUESTION 2');
        await page.reload();await page.locator('.answer-button').first().waitFor();
        await page.locator('#previousButton').click();
        assert.equal(await page.locator('.answer-button.selected').isDisabled(),true);
        await capture('rbi-revisit');
        await page.locator('#nextSavedButton').click();
        // Reflow with enlarged text and reduced motion.
        await page.emulateMedia({reducedMotion:'reduce'});
        await page.addStyleTag({content:'.research-page .question-text {font-size: 200%}'});
        await capture('rbi-large-text');
      }
    }
    await page.goto(origin+'/bsfl');await page.locator('.answer-button').first().waitFor();
    for(let i=0;i<5;i++){
      await page.locator('.answer-button').first().click();
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
    await page.locator('#restartButton').click();await page.locator('.answer-button').first().waitFor();
    await capture('bsfl-retest');
    assert.deepEqual(errors,[]);
    await context.close();
  }
  console.log('PASS: Chromium 360 / 430 / 1280px; 18 direct research pages, folded notices, touch targets, keyboard/details/selection/reload/revisit, enlarged text, BSFL zero/report/retest; screenshots: '+screenshots);
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  service.kill('SIGTERM');await exited;
  fs.rmSync(temp,{recursive:true,force:true});
});
