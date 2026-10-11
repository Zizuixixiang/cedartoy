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
    await page.goto(origin);await page.locator('[data-filter="test"]').click();
    for(const s of specs) assert.equal(await page.locator(`.game-card[data-game="${s.game}"]`).isVisible(),true,s.game);
    await capture('hall');
    const bsrCard=page.locator('.game-card[data-game="bsr"]');
    if(width<600){
      await bsrCard.click();await capture('hall-bsr');
      await page.locator('#drawerEnter').click();
    } else {
      await bsrCard.hover();await capture('hall-bsr');
      await bsrCard.click();
    }
    await page.waitForURL(origin+'/bsr');
    await page.locator('.mode-button').first().waitFor();
    const bsr=specs.find(s=>s.game==='bsr');
    const response=new Map(bsr.items.map((q,i)=>[q.text,i<10?1:5]));
    await capture('bsr-modes');
    for(const mode of [0,1]){
      if(mode===1) await page.locator('#restartButton').click();
      await page.locator('.mode-button').nth(mode).click();
      await page.locator('.answer-button').first().waitFor();
      await capture('bsr-question-'+mode);
      const seen=[];
      for(let i=0;i<20;i++){
        await page.waitForFunction(n=>document.getElementById('questionNumber').textContent===`QUESTION ${n}`,i+1);
        const text=await page.locator('#questionText').innerText();seen.push(text);
        const height=await page.locator('.answer-button').first().evaluate(n=>n.getBoundingClientRect().height);
        assert.ok(height>=44,'BSR touch target');
        if(i===7){
          await page.reload();await page.locator('.answer-button').first().waitFor();
          assert.equal(await page.locator('#questionText').innerText(),text,'BSR restored question');
        }
        const answer=page.locator('.answer-button').nth(response.get(text)-1);
        if(i===0){await answer.focus();await page.keyboard.press('Enter');}
        else await answer.click();
      }
      assert.equal(new Set(seen).size,20);
      if(mode===1) await page.locator('#submitButton').click();
      await page.locator('#resultPanel').waitFor();
      const result=await page.locator('#resultText').innerText();
      assert.match(result,/伪深刻语句评价均分：1\.00/);
      assert.match(result,/励志格言均分：5\.00/);
      assert.match(result,/差值（励志格言 − 伪深刻语句）：4\.00/);
      await capture('bsr-result-'+mode);
      await page.reload();await page.locator('#resultPanel').waitFor();
      assert.equal(await page.locator('#resultText').innerText(),result);
    }
    for(const game of ['ross','irrc','aidep']){
      await page.goto(origin+'/'+game);await page.locator('.mode-button').first().waitFor();
      assert.equal(await page.locator('.mode-button').count(),2);
      await capture(game+'-modes');
      await page.locator('.mode-button').first().click();await page.locator('.answer-button').first().waitFor();
      const boxes=await page.locator('.answer-button').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().height));
      assert.ok(boxes.every(h=>h>=44),'touch targets');
      await capture(game+'-question');
    }
    await page.goto(origin+'/bsfl');await page.locator('.mode-button').nth(1).click();
    for(let i=0;i<5;i++) await page.locator('.answer-button').first().click();
    await page.locator('#submitButton').click();await page.locator('#resultPanel').waitFor();
    assert.match(await page.locator('#resultText').innerText(),/0\.00/);
    assert.match(await page.locator('#resultText').innerText(),/4399.*描述性/);
    await capture('bsfl-result');
    await page.reload();await page.locator('#resultPanel').waitFor();
    assert.match(await page.locator('#resultText').innerText(),/0\.00/);
    assert.deepEqual(errors,[]);
    await context.close();
  }
  console.log('PASS: Chromium 360 / 430 / 1280px; 18 homepage cards, BSR both modes/order/reload/scoring/keyboard, mode/long-question/result layouts, touch targets, BSFL zero/history; screenshots: '+screenshots);
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  service.kill('SIGTERM');await exited;
  fs.rmSync(temp,{recursive:true,force:true});
});
