/* Local UI-only fixtures. No production API, credentials or database access.
 * PLAYWRIGHT_MODULE=/path/to/playwright PLAYWRIGHT_EXECUTABLE_PATH=/path/to/chromium node scripts/check_history_fold_browser.js
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const html = fs.readFileSync(process.env.HISTORY_HTML || path.join(__dirname, '../index.html'), 'utf8');
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const out = process.env.HISTORY_SCREENSHOTS || '/tmp/cedartoy-history-fold-review-20261012';
fs.mkdirSync(out, {recursive:true});
const round = n => ({round_id:String(n), title:`第 ${n} 轮 · 日常与心事`, human_name:'南杉', ai_name:'小机一', status:n % 2 ? 'complete' : 'active', updated_at:'2026-10-12T12:00:00+08:00'});
const duel = (count, total=count) => ({available:true, total, matches:Array.from({length:count}, (_, n) => ({game_type:['gomoku','chess','reversi'][n%3], outcome:['win','loss','draw'][n%3], opponents:[`小机对手 ${n+1}`], time:`2026-10-${String(12-n).padStart(2,'0')}T12:30:00+08:00`, result_detail:`第 ${n+1} 局结果详情`} ))});
const fixture = () => ({self:{username:'南杉', saves:{mbti:{slots:[{slot:1,latest_result:'INTJ',completed_at:'2026-10-12'}]}, eco:{slots:[{slot:1,day:16,pond_score:30},{slot:2,day:3}]}, forest:{slots:[{slot:1,current_line:88}]}},duel_history:duel(11),deeptalk_history:{total:4,rounds:[round(1),round(2)]}}, machines:[{username:'小机一',saves:{eco:{slots:[{slot:1,day:2}]}},duel_history:duel(3)},{username:'小机二',saves:{},duel_history:duel(7,31)},{username:'小机三',saves:{}}]});
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH, args:['--no-sandbox']});
  try {
    for (const width of [360,430,1280]) {
      const page = await browser.newPage({viewport:{width,height:width===360?780:900},hasTouch:width<500});
      const errors = [], requests = [];
      let data = fixture(), pending = null, defer = false, fail = false;
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname !== 'history.test') return route.abort();
        if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:html});
        if (url.pathname === '/api/auth/history') return route.fulfill({json:data});
        if (url.pathname === '/api/deeptalk') {
          requests.push(url.search);
          if (defer) await new Promise(resolve => {pending=resolve;});
          return route.fulfill(fail ? {status:503,json:{error:'测试加载失败'}} : {json:{rounds:[round(3),round(4)],total:4}});
        }
        if (url.pathname.startsWith('/api/')) return route.fulfill({json:{count:0,announcements:[],unread_count:0}});
        return route.fulfill({status:404,body:''});
      });
      await page.goto('http://history.test');
      await page.evaluate(() => {me={user:{username:'南杉'}};localStorage.setItem('cedartoy_token','local-fixture-only');});
      const open = () => page.evaluate(() => openHistory());
      const fold = key => page.locator(`[data-history-fold="${key}"]`);
      const section = key => page.locator(`[data-history-section="${key}"]`);
      const tab = id => page.locator(`[data-history-tab="${id}"]`);
      const touch = async locator => width<500 ? locator.tap() : locator.click();
      const shot = name => page.locator('#historyModal').screenshot({path:path.join(out,`${width}-${name}.png`)});
      const geometry = () => page.evaluate(() => {
        const box=document.querySelector('#historyModal .modal-box').getBoundingClientRect();
        const content=document.querySelector('#historyContent');
        return {top:box.top,height:box.height,bottom:box.bottom,scroll:content.scrollTop,windowY:scrollY,overflow:content.scrollWidth>content.clientWidth+1};
      });
      await open();
      await shot(process.env.HISTORY_BASELINE ? 'before' : 'collapsed');
      if (process.env.HISTORY_BASELINE) {await page.close();continue;}
      assert.equal(await page.locator('.history-fold-toggle[aria-expanded="true"]').count(),0);
      assert.equal(await page.locator('.history-duel-match:visible').count(),0);
      assert.match(await fold('duel').innerText(),/共 11 局/);
      const initial=await geometry();assert.equal(initial.overflow,false);
      assert.ok(initial.bottom <= (width===360?780:900));
      const size=await fold('duel').boundingBox(); assert.ok(size.height>=44);
      await touch(fold('duel'));
      assert.equal(await section('duel').locator('.history-duel-match:visible').count(),5);
      assert.equal(await fold('duel').getAttribute('aria-expanded'),'true');
      const ax = await fold('duel').ariaSnapshot(); assert.match(ax,/expanded/);
      await shot('expanded-5');
      assert.equal((await geometry()).top,initial.top);
      assert.match(await section('duel').innerText(),/对手[\s\S]*时间[\s\S]*结果详情/);
      await page.locator('#duelHistoryMore').scrollIntoViewIfNeeded();
      const beforeMore=await geometry();
      await touch(page.locator('#duelHistoryMore'));
      assert.equal(await section('duel').locator('.history-duel-match:visible').count(),10);
      assert.ok(Math.abs((await geometry()).scroll-beforeMore.scroll)<2,'pagination retains internal scroll');
      await touch(page.locator('#duelHistoryMore'));
      assert.equal(await section('duel').locator('.history-duel-match:visible').count(),11);
      assert.equal(await page.locator('#duelHistoryMore').count(),0);
      await shot('expanded-11');
      await fold('duel').focus();await page.keyboard.press('Space');
      assert.equal(await fold('duel').getAttribute('aria-expanded'),'false');
      await touch(fold('game:eco'));
      assert.equal(await section('game:eco').locator('.history-card-row:visible').count(),2);
      await touch(fold('deeptalk'));
      await page.locator('#deeptalkHistoryMore').scrollIntoViewIfNeeded();
      const beforeDT=await geometry();
      await touch(page.locator('#deeptalkHistoryMore'));
      await page.waitForFunction(() => !document.querySelector('#deeptalkHistoryMore'));
      assert.equal(await fold('deeptalk').getAttribute('aria-expanded'),'true');
      assert.equal(await fold('game:eco').getAttribute('aria-expanded'),'true');
      assert.equal(await section('deeptalk').locator('.history-duel-match').count(),4);
      assert.ok(Math.abs((await geometry()).scroll-beforeDT.scroll)<2);
      assert.match(requests.at(-1),/offset=2/);
      await shot('deeptalk-loaded');
      await touch(tab('machine-0'));
      assert.equal(await page.locator('[aria-expanded="true"]').count(),0);
      await fold('duel').focus();await page.keyboard.press('Enter');
      assert.equal(await section('duel').locator('.history-duel-match:visible').count(),3);
      assert.equal(await page.locator('#duelHistoryMore').count(),0);
      await touch(tab('machine-1'));
      assert.equal(await fold('duel').getAttribute('aria-expanded'),'false');
      await touch(fold('duel'));
      await touch(page.locator('#duelHistoryMore'));
      assert.equal(await section('duel').locator('.history-duel-match:visible').count(),7);
      assert.match(await section('duel').innerText(),/仅提供最近 7 局，共 31 局/);
      assert.equal(await page.locator('#duelHistoryMore').count(),0);
      await touch(tab('self'));
      assert.equal(await fold('game:eco').getAttribute('aria-expanded'),'true');
      assert.equal(await fold('duel').getAttribute('aria-expanded'),'false');
      await touch(fold('duel'));
      assert.equal(await section('duel').locator('.history-duel-match:visible').count(),11);
      await touch(tab('machine-2'));
      assert.match(await page.locator('#historyContent').innerText(),/还没有游戏记录/);

      // Slow response during identity switch: no cross-subject rerender or lost state.
      await open();await touch(fold('deeptalk'));defer=true;
      await touch(page.locator('#deeptalkHistoryMore'));
      await page.waitForFunction(() => document.querySelector('#deeptalkHistoryMore')?.disabled);
      await touch(tab('machine-0'));await touch(fold('duel'));
      const machineMarkup=await page.locator('#historyContent').innerHTML();
      assert.ok(pending);pending();defer=false;
      await page.waitForFunction(() => historyState.data.self.deeptalk_history.rounds.length===4);
      assert.equal(await page.locator('#historyContent').innerHTML(),machineMarkup);
      await touch(tab('self'));
      assert.equal(await fold('deeptalk').getAttribute('aria-expanded'),'true');
      assert.equal(await section('deeptalk').locator('.history-duel-match').count(),4);

      // Failure remains expanded and retryable.
      await open();fail=true;await touch(fold('deeptalk'));await touch(page.locator('#deeptalkHistoryMore'));
      await page.waitForFunction(() => document.querySelector('#deeptalkHistoryMore')?.textContent==='加载更多');
      assert.equal(await fold('deeptalk').getAttribute('aria-expanded'),'true');
      assert.equal(await section('deeptalk').locator('.history-duel-match').count(),2);
      fail=false;await touch(page.locator('#deeptalkHistoryMore'));
      await page.waitForFunction(() => !document.querySelector('#deeptalkHistoryMore'));

      // Empty, unavailable, truncated-with-no-rows, missing API and escaped long names.
      for (const h of [undefined,{available:false},duel(0),duel(0,31)]) {
        data={self:{username:'南杉',saves:{},duel_history:h},machines:[]};await open();
        if(h){await touch(fold('duel'));assert.equal(await page.locator('#duelHistoryMore').count(),0);}
        else assert.match(await page.locator('#historyContent').innerText(),/还没有游戏记录/);
        assert.equal((await geometry()).overflow,false);
      }
      const evil='<img src=x onerror="window.historyXSS=1">'+('VeryLongTitle'.repeat(12));
      data={self:{username:evil,saves:{[evil]:{slots:[]}},duel_history:{...duel(1),matches:[{game_type:evil,outcome:evil,opponents:[evil],result_detail:evil,time:'unknown'}]},deeptalk_history:{rounds:[{...round(1),title:evil}],total:1}},machines:[]};
      await open();await touch(fold('duel'));
      assert.equal(await page.locator('#historyContent img').count(),0);
      assert.equal(await page.evaluate(() => window.historyXSS),undefined);
      assert.equal((await geometry()).overflow,false);
      await shot('long-titles');
      await page.emulateMedia({reducedMotion:'reduce'});
      await page.addStyleTag({content:'#historyContent button, #historyContent span, #historyContent p, #historyContent .history-duel-game {font-size:200% !important}'});
      assert.equal((await geometry()).overflow,false);
      await shot('enlarged');
      assert.equal((await geometry()).windowY,initial.windowY);
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({width,passed:true,geometry:initial,screenshots:out}));
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e => {console.error(e);process.exitCode=1;});
