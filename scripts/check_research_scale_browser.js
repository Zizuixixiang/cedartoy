"use strict";
// Uses an existing Playwright/Chromium installation; no dependency downloads.
// Serves only a temporary test process and temporary account/session databases.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const {checkHistoryTitles} = require('./history_title_cases');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cedar-scale-browser-'));
const screenshots = process.env.SCALE_SCREENSHOTS || path.join(os.tmpdir(), 'cedar-scale-screenshots');
const widths = process.env.SCALE_WIDTHS ? process.env.SCALE_WIDTHS.split(',').map(Number) : [360,430,1280];
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
  fs.mkdirSync(screenshots,{recursive:true});
  for(const width of widths){
    browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
    const context=await browser.newContext({viewport:{width,height:900}});
    const page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const writes=[];page.on('request',r=>{if(r.url().endsWith('/save_answer')) writes.push(r.postDataJSON());});
    const settled=()=>page.waitForFunction(()=>!busy);
    async function choose(index=0){await page.locator('#answers button').nth(index).click();await settled();}
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
    for(const query of ['', '?tab=mini', '?tab=invalid', '?tab=test']) {
      await page.goto(origin+'/'+query);
      assert.equal(await page.locator('.tab-button.active').getAttribute('data-filter'),query==='?tab=test'?'test':'mini');
    }
    assert.equal(await page.evaluate(()=>games.filter(g=>g.testGroup==='deeptalk').length),10);
    const names=await page.evaluate(()=>Object.fromEntries(researchScales.map(([id,name])=>[id,name])));
    assert.equal(Object.keys(names).length,18);
    for(const spec of specs) {
      assert.equal(await page.locator(`[data-game="${spec.game}"] .game-title`).innerText(),names[spec.game]);
      assert.doesNotMatch(names[spec.game],/描述版|娱乐改编版/);
    }
    for (const query of ['ADHD', 'adhd']) {
      await page.locator('#searchInput').fill(query);
      assert.deepEqual(await page.locator('.game-card:not(.hidden)').evaluateAll(nodes=>nodes.map(n=>n.dataset.game)), ['ipip_adhd']);
    }
    await page.locator('#searchInput').fill('');
    assert.equal(await page.locator('.game-title').evaluateAll(nodes=>nodes.some(n=>n.scrollWidth>n.clientWidth)),false,'long card titles wrap');
    await page.locator('[data-game="rbi"]').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(screenshots,`${width}-home-titles.png`)});
    await page.evaluate(checkHistoryTitles);
    await page.evaluate(()=>openModal('historyModal'));
    await page.locator('.history-card-title').filter({hasText:names.ipip_self_monitoring}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(screenshots,`${width}-account-history-titles.png`)});
    assert.equal(await page.locator('#historyContent').evaluate(n=>n.scrollWidth>n.clientWidth),false,'history long names wrap');
    await page.evaluate(()=>closeModals());
    await page.evaluate(()=>openPlatformStats());
    for(const spec of specs) assert.equal(await page.locator(`.platform-dist-${spec.game} summary`).innerText(),`${names[spec.game]} · 分数分布`);
    await page.locator('.platform-dist-irrc').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(screenshots,`${width}-stats-titles.png`)});
    // Only research pages and the original MBTI/ECR references hit test handlers.
    for(const spec of specs){
      await page.goto(origin+'/'+spec.game);
      await page.locator('#answers button').first().waitFor();
      assert.doesNotMatch(await page.locator('#questionIntro').textContent(),/自动保存|不可修改|续答旧版/);
      assert.equal(await page.locator('#title').innerText(),names[spec.game]);
      assert.equal(await page.title(),`${names[spec.game]} · CEDAR TOY`);
      assert.equal(await page.locator('.mode-button').count(),0);
      assert.equal(await page.locator('#researchDetails').getAttribute('open'),null);
      assert.equal(await page.locator('#pageDisclaimer').innerText(),
        ['rbi','irrc','jrrs','ross','rss','ppts','bsfl'].includes(spec.game) ? '非官方评分，仅供娱乐参考。'
        : spec.game==='bsr' ? '题目经改编，仅供娱乐参考。'
        : spec.game==='aidep' ? '仅供人类，按原量表计分，仅供娱乐参考。' : '按原量表计分，仅供娱乐参考。');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,spec.game+' overflow');
      assert.equal(await page.locator('#researchSaveHint').count(),0);
      assert.equal(await page.locator('#saveAnswerButton').innerText(),'下一题');
      assert.equal(await page.locator('#saveAnswerButton').isDisabled(),true);
      const labels=await page.locator('#answers button').allTextContents();
      assert.deepEqual(labels,spec.options.map(o=>String(o.value)),spec.game+' all numeric steps');
      assert.equal(await page.locator('.research-scale-track').count(),1);
      assert.equal(await page.locator('.research-scale-selection').innerText(),'');
      const stepGeometry=await page.locator('#answers button').evaluateAll(nodes=>nodes.map(n=>{
        const r=n.getBoundingClientRect();return {top:r.top,right:r.right,left:r.left};
      }));
      if(spec.options.length<=7) assert.equal(new Set(stepGeometry.map(r=>r.top)).size,1,spec.game+' 4–7 steps stay on one row');
      else if(width<=430) assert.ok(new Set(stepGeometry.map(r=>r.top)).size>1,spec.game+' long scale wraps');
      assert.ok(stepGeometry.every(r=>r.left>=0 && r.right<=width));
      const labelFor=o=>String(o.value)===String(o.text).trim() ? String(o.value) : `${o.value} · ${o.text}`;
      assert.deepEqual(await page.locator('.research-scale-ends span').allTextContents(),
        [labelFor(spec.options[0]),labelFor(spec.options.at(-1))]);
      const key=page.locator('.research-scale-key');
      if(await key.count()) {
        await key.locator('summary').focus();await page.keyboard.press('Enter');
        assert.equal(await key.getAttribute('open'),'');
        assert.deepEqual(await key.locator('p').allTextContents(),spec.options.map(labelFor));
        await capture(spec.game+'-all-meanings');
        await key.locator('summary').click();
      }
      for(const [i,option] of spec.options.entries()) {
        const button=page.locator('#answers button').nth(i);
        assert.equal(await button.getAttribute('aria-label'),String(option.text)===String(option.value) ? null : `${option.value}，${option.text}`);
        const before=writes.length;
        await button.click();await settled();
        assert.equal(await page.evaluate(()=>cursor),1,'selection advances only after saving');
        assert.equal(writes.length,before+1);
        assert.equal(writes.at(-1).finish,false);
        assert.equal(await page.locator('#questionText').evaluate(n=>n===document.activeElement),true);
        await page.locator('#previousButton').click();
        assert.equal(await page.locator('.research-scale-selection').innerText(),`已选：${labelFor(option)}`);
        assert.equal(await page.locator('#answers [aria-pressed="true"]').count(),1);
        assert.equal(await page.locator('#answers button.selected').innerText(),String(option.value));
      }
      // Keyboard activation saves, then focuses the next question. Returning retains the meaning.
      await page.locator('#answers button').first().focus();await page.keyboard.press('Tab');await page.keyboard.press('Space');await settled();
      assert.equal(await page.evaluate(()=>cursor),1);
      assert.equal(await page.locator('#questionText').evaluate(n=>n===document.activeElement),true);
      await page.locator('#previousButton').click();
      assert.equal(await page.locator('#answers button.selected').innerText(),String(spec.options[1].value));
      await page.locator('#answers button').nth(2).focus();await page.keyboard.press('Enter');await settled();
      await page.locator('#previousButton').click();
      await capture(spec.game+'-selected-middle');
      assert.equal(await page.locator('.research-scale-selection').evaluate(n=>n.scrollWidth>n.clientWidth),false);
      await page.locator('#researchDetails summary').click();
      assert.equal(await page.locator('#source a').first().getAttribute('href'),spec.source);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,spec.game+' expanded source overflow');
      assert.doesNotMatch(await page.locator('#researchDetails').innerText(),/小机作答|4399|不作诊断|原研究对象|常模|效度/);
      assert.equal(await page.locator('#source dd').count(),4);
      const metadata=await page.locator('#source dd').evaluateAll(nodes=>nodes.map(n=>({size:getComputedStyle(n).fontSize,line:getComputedStyle(n).lineHeight})));
      assert.ok(metadata.every(n=>n.size==='12px' && n.line===metadata[0].line));
      await capture(spec.game+'-source');
      await page.locator('#researchDetails summary').click();
      const bounds=await page.locator('#answers button').evaluateAll(nodes=>nodes.map(n=>({width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height})));
      assert.ok(bounds.every(b=>b.height>=44 && b.width>=24),spec.game+' touch targets');
      if(['irrc','jrrs','rss'].includes(spec.game)) await capture(spec.game+'-title');
      if(spec.game==='ipip_empathy') {
        await page.locator('#saveAnswerButton').click();
        await page.waitForFunction(()=>document.getElementById('questionNumber').textContent==='QUESTION 2');
        assert.equal(await page.locator('#questionText').innerText(),spec.items[1].text);
        await choose(2);
        await page.locator('#previousButton').click();
        await page.reload();await page.locator('#answers button.selected').waitFor();
        assert.equal(await page.locator('.research-scale-selection').innerText(),'已选：3 · 介于符合与不符合之间');
        await capture('ipip_empathy-question-2-selected-3');
        await page.emulateMedia({reducedMotion:'reduce'});
        await page.addStyleTag({content:'.research-scale-ends, .research-scale-middle, .research-scale-selection, .research-scale-key {font-size:24px !important}'});
        await capture('ipip_empathy-large-labels');
      }
      const beforeForward=writes.length;
      const revision=await page.evaluate(()=>activeState.answer_revision);
      await page.locator('#saveAnswerButton').click();await settled();
      assert.equal(writes.length,beforeForward,'saved answer Next is navigation only');
      assert.equal(await page.evaluate(()=>activeState.answer_revision),revision);
      while(await page.evaluate(()=>cursor<activeState.total-1)) {
        const index=await page.evaluate(()=>cursor);
        await choose(index%spec.options.length);
        assert.equal(await page.evaluate(()=>cursor),index+1);
      }
      await choose(2);
      assert.equal(await page.evaluate(()=>activeState.complete),false);
      assert.equal(await page.evaluate(()=>activeState.progress),spec.items.length);
      assert.equal(await page.locator('#saveAnswerButton').innerText(),'查看结果');
      await capture(spec.game+'-last-saved');
      await page.locator('#previousButton').click();
      const beforeLastNext=writes.length;
      await page.locator('#saveAnswerButton').click();
      assert.equal(writes.length,beforeLastNext);
      await page.locator('#saveAnswerButton').click();await settled();
      assert.equal(writes.at(-1).finish,true);
      assert.equal(await page.locator('#resultPanel').isVisible(),true);
      console.log(`PASS ${width}px ${spec.game}: save-confirmed advance, read-only Next, explicit final result`);
    }
    for(const game of ['rbi','ross','aidep','bsr']){
      await page.evaluate(id=>localStorage.setItem('cedartoy_guest_web_player_id',id),`guest:webautonext${width}${game}`);
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
        await page.locator('#answers button').nth(2).focus();await page.keyboard.press('Space');await settled();
        assert.equal(await page.evaluate(()=>cursor),1);
        await page.reload();await page.locator('#answers button').first().waitFor();await settled();
        await page.locator('#previousButton').click();
        assert.equal(await page.locator('#answers button.selected').innerText(),'3');
        await capture('rbi-revisit');
        const receipt=await page.evaluate(()=>activeState.answer_revision);
        const before=writes.length;
        await page.locator('#saveAnswerButton').click();
        assert.equal(await page.evaluate(()=>cursor),1);
        assert.equal(writes.length,before,'unchanged Next only navigates');
        assert.equal(await page.evaluate(()=>activeState.answer_revision),receipt);
        await page.locator('#previousButton').click();
        await choose(3);
        assert.equal(await page.evaluate(()=>cursor),1);
        // A failed write stays on the same question, preserving the selection.
        let release;
        const gate=new Promise(resolve=>{release=resolve;});
        const fail=async route=>{await gate;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'保存失败'})});};
        await page.route('**/api/rbi/save_answer',fail);
        const beforeFailure=writes.length;
        await page.locator('#answers button').first().click();
        // Programmatic rapid clicks reach the same disabled controls as repeated pointer activation.
        await page.evaluate(()=>{document.querySelectorAll('#answers button')[1].click();document.getElementById('saveAnswerButton').click();});
        assert.equal(await page.locator('#questionNumber').innerText(),'QUESTION 2');
        assert.equal(await page.locator('#saveAnswerButton').isDisabled(),true);
        assert.equal(writes.length,beforeFailure+1);
        await capture('rbi-saving-no-navigation');
        release();await page.locator('#error').waitFor();await settled();
        assert.equal(await page.locator('#questionNumber').innerText(),'QUESTION 2');
        assert.equal(await page.locator('#answers button.selected').innerText(),'1');
        assert.equal(await page.locator('#saveAnswerButton').isEnabled(),true);
        await capture('rbi-save-error');
        await page.unroute('**/api/rbi/save_answer',fail);
        await page.reload();await page.locator('#answers button.selected').waitFor();await settled();
        await page.locator('#saveAnswerButton').click();await settled();
        assert.equal(await page.evaluate(()=>cursor),2);
        await choose(4);
        assert.equal(await page.evaluate(()=>cursor),3);
        await page.locator('#previousButton').click();await page.locator('#previousButton').click();await page.locator('#previousButton').click();
        await choose(1);
        assert.equal(await page.evaluate(()=>cursor),1);
        await page.locator('#saveAnswerButton').click();
        assert.equal(await page.evaluate(()=>cursor),2);
        assert.equal(await page.locator('#answers button.selected').innerText(),'5');
        const saved=await page.evaluate(()=>[activeState.saved_answers,activeState.answer_revision]);
        await page.reload();await page.locator('#answers button.selected').waitFor();await settled();
        assert.deepEqual(await page.evaluate(()=>[activeState.saved_answers,activeState.answer_revision]),saved);
        await capture('rbi-edited-third-retained');
        // Reflow with enlarged text and reduced motion.
        await page.emulateMedia({reducedMotion:'reduce'});
        await page.addStyleTag({content:'.research-page .question-text {font-size: 200%}'});
        await capture('rbi-large-text');
      }
    }
    await page.evaluate(id=>localStorage.setItem('cedartoy_guest_web_player_id',id),`guest:webautolast${width}`);
    await page.goto(origin+'/bsfl');await page.locator('#answers button').first().waitFor();
    for(let i=0;i<5;i++){
      assert.equal(await page.locator('#saveAnswerButton').innerText(),i===4?'查看结果':'下一题');
      if(i===4) await page.locator('.research-scale-key summary').click();
      await choose();
      assert.equal(await page.evaluate(()=>cursor),Math.min(i+1,4));
    }
    assert.equal(await page.evaluate(()=>activeState.complete),false);
    assert.equal(await page.evaluate(()=>activeState.progress),5);
    assert.equal(await page.locator('.research-scale-key').getAttribute('open'),'');
    await capture('bsfl-last-saved-not-finished');
    await page.locator('#previousButton').click();
    const beforeLast=writes.length;
    await page.locator('#saveAnswerButton').click();
    assert.equal(writes.length,beforeLast);
    assert.equal(await page.evaluate(()=>cursor),4);
    await page.reload();await page.locator('#answers button.selected').waitFor();await settled();
    await page.locator('#saveAnswerButton').click();await settled();
    assert.equal(writes.at(-1).finish,true);
    await page.locator('#resultPanel').waitFor();
    assert.equal(await page.locator('.research-score dd').innerText(),'0.00 / 4');
    await capture('bsfl-result');
    await page.reload();await page.locator('#resultPanel').waitFor();
    page.once('dialog',dialog=>dialog.dismiss());
    await page.locator('#restartButton').click();
    assert.equal(await page.locator('#resultPanel').isVisible(),true);
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#restartButton').click();await page.locator('#answers button').first().waitFor();
    await capture('bsfl-retest');
    // Real completed reports restored from isolated saved records for all 18 scales.
    for (const spec of specs) {
      await page.evaluate(id=>localStorage.setItem('cedartoy_guest_web_player_id',id),`guest:webresearchresult${width}${spec.game.replaceAll('_','')}`);
      await page.goto(origin+'/'+spec.game);
      await page.locator('#answers button').first().waitFor();
      const expected=await page.evaluate(async values=>{
        renderState(await api('start',{mode:'full_fast',restart:true,confirm:true,session_id:activeState.session_id}));
        const state=await api('answer_batch',{answers:values});
        renderState(state);
        return Object.values(state.result_data.scores).map(n=>n.toFixed(2));
      },spec.items.map((_,i)=>spec.options[i%spec.options.length].value));
      await page.reload();await page.locator('#resultPanel').waitFor();
      assert.equal(await page.locator('#resultText').isVisible(),false);
      assert.deepEqual(await page.locator('.research-score-value').allTextContents(),expected);
      const ranges=await page.evaluate(()=>activeState.result_data.score_ranges);
      for(const [key,bounds] of Object.entries(ranges)) {
        const idx=Object.keys(await page.evaluate(()=>activeState.result_data.scores)).indexOf(key);
        const actual=await page.locator('.research-score dd').nth(idx).innerText();
        assert.ok(actual.includes(bounds.kind==='difference'?`范围 ${bounds.min}～${bounds.max}`:`/ ${bounds.max}`),spec.game+'/'+key);
      }
      assert.equal(await page.locator('.research-report-footnote').count(),0);

      assert.equal(await page.locator('.research-result a').getAttribute('href'),spec.source);
      const report = await page.evaluate(() => activeState.result_data.interpretation || null);
      assert.ok(report && typeof report.summary==='string' && report.summary.length>25,
        spec.game+' missing completed result report');
      assert.equal(await page.locator('.research-interpretation').count(),1);
      assert.equal(await page.locator('.research-dimension').count(),report.dimension_notes.length);
      assert.equal(await page.locator('.research-actions li').count(),report.recommendations.length);
      assert.equal(await page.locator('.rbi-interpretation').count(),0,'RBI uses the shared server/MCP report');
      assert.doesNotMatch(await page.locator('#resultStructured').innerText(),/存档身份|小机作答|原研究对象/);
      await capture(spec.game+'-result');
      if (spec.game==='irrc') {
        // The shared 17-scale report contract must render safely on all viewports,
        // including the largest seven-dimensional report and untrusted-looking text.
        await page.evaluate(() => renderResult({...activeState.result_data, interpretation: {
          summary:'查看分数的相对组合：表达方式与困难感可能并存。<img src=x onerror=alert(1)>',
          dimension_notes:Object.entries(activeState.result_data.scores).slice(0,7).map(([key,score],i)=>({key,score,label:'解读维度'+(i+1),text:'选择一个具体场景，留意已经能做到的事，以及需要练习的一步。'})),
          recommendations:['和信任的人交流自己期待的回应','选一次分歧练习只提一个可实现的请求'],
          source_note:'作者论文介绍构念，以下是平台反思建议。', boundary:'不能将本分数用作关系质量或诊断判定。'
        }},''));
        assert.equal(await page.locator('.research-interpretation h3').count(),3);
        assert.equal(await page.locator('.research-dimension').count(),7);
        assert.equal(await page.locator('.research-actions li').count(),2);
        assert.equal(await page.locator('.research-interpretation img').count(),0);
        await capture('irrc-interpretation-rendered');
      }
      if (spec.game==='rbi') {
        for(const [name,values] of Object.entries({example:[2.86,3.57,4.5],equal:[3,3,3],minimum:[1,1,1],maximum:[5,5,5],ties:[5,1,5],reversed:[5,3,1]})) {
          await page.evaluate(([sai,fod,openness])=>renderResult({...activeState.result_data,interpretation:null,scores:{sai,fod,openness}},''),values);
          assert.equal(await page.locator('.rbi-interpretation h4').count(),4);
          const geometry=await page.locator('.rbi-interpretation').evaluate(el=>({
            font:getComputedStyle(el).fontSize,
            overflow:[el,...el.querySelectorAll('*')].some(n=>n.scrollWidth>n.clientWidth),
            right:el.getBoundingClientRect().right
          }));
          assert.equal(geometry.font,'14px');
          assert.equal(geometry.overflow,false);
          assert.ok(geometry.right<=width);
          await capture('rbi-interpretation-'+name);
        }
        await page.evaluate(()=>renderResult({...activeState.result_data,interpretation:null,scores:{sai:2.86,fod:3.57,openness:4.5}},''));
        await page.addStyleTag({content:'.research-meta, .research-result .research-notice, .research-result .research-help {font-size:24px} .research-score dt, .rbi-interpretation, .rbi-interpretation h3, .rbi-interpretation h4 {font-size:28px} .research-score dd {font-size:40px}'});
        await page.locator('#researchDetails summary').click();
        await capture('rbi-result-large-text');
      }
    }
    await page.evaluate(id=>localStorage.setItem('cedartoy_guest_web_player_id',id),`guest:webresearchresult${width}rbi`);
    for(const selector of ['.brand', '.back-link', '#resultPanel .actions a']) {
      await page.goto(origin+'/rbi');await page.locator('#resultPanel').waitFor();
      await Promise.all([
        page.waitForURL(origin+'/?tab=test',{waitUntil:'load'}),
        page.locator(selector).click()
      ]);
      assert.equal(await page.locator('.tab-button.active').getAttribute('data-filter'),'test');
    }
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
    await browser.close();browser=null;
  }
  console.log('PASS: Chromium '+widths.join(' / ')+'px; all 18 full one-click save-confirmed flows, unchanged Next without writes, last-answer review and explicit finalize; numeric rows and original meanings, touch targets, keyboard/focus/reload/revisit, delayed failure/retry/double click, enlarged text, 18 reports, BSFL zero/retest, MBTI/ECR reference; screenshots: '+screenshots);
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  service.kill('SIGTERM');await exited;
  fs.rmSync(temp,{recursive:true,force:true});
});
