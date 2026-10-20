/* UI-only fixtures: all requests intercepted; no accounts, databases or saves.
 * PLAYWRIGHT_MODULE=/path/to/playwright PLAYWRIGHT_EXECUTABLE_PATH=/path/to/chromium node scripts/check_announcements_fold_browser.js
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const out = process.env.ANNOUNCEMENT_SCREENSHOTS || '/tmp/cedartoy-announcements-fold-review';
fs.mkdirSync(out, {recursive:true});
const content = '**加粗提示**\n- **一起画画**：共享画布\n- **一起拼豆**：制作装饰\n\n原有正文，保留换行。\n\n项目链接：https://example.test/guide?a=1&b=2\n<em>纯文本，不执行 HTML</em>\n' + '很长的公告内容，手机端应在弹窗内滚动。\n'.repeat(35);
const fixture = () => [
  {id:'notice', title:'新游戏与站点更新', type:'notice', content, unread:true},
  {id:'poll', title:'来选一选你希望下次优先上线的玩法', type:'poll', content:'请保留选择和补充意见。', unread:true, multiple:true, allow_feedback:true, options:['双人合作','解谜探索']},
  {id:'voted', title:'已经参与过的投票', type:'poll', content:'已读状态和投票状态独立。', unread:false, options:['选项一','选项二'], my_vote:{options:[2], feedback:'已有意见'}},
  {id:'long', title:'很长的标题需要合理换行，收起时保持紧凑，展开之后可以阅读完整标题。'.repeat(5), type:'notice', content:'完整公告正文。', unread:false},
  ...Array.from({length:6}, (_, i) => ({id:`old-${i}`, title:`往期通知 ${i+1}`, type:'notice', content:'历史正文', unread:false})),
].map(item => ({created_at:'2026-10-14 12:30:00', target_game:'all', ...item}));

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH, args:['--no-sandbox']});
  try {
    for (const width of [360,430,1280]) {
      const height = width === 360 ? 780 : 900;
      const page = await browser.newPage({viewport:{width,height},hasTouch:width<500});
      const errors = [], writes = [];
      let data = fixture(), failVote = false, failLoad = false;
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        // In production the existing marked CDN can load normally. Run once
        // without it (offline fallback), and optionally with a pinned local
        // copy supplied in ANNOUNCEMENT_MARKED_JS to verify the real parser.
        if (url.hostname === 'cdnjs.cloudflare.com' && url.pathname.endsWith('/marked.min.js') && process.env.ANNOUNCEMENT_MARKED_JS) {
          return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(process.env.ANNOUNCEMENT_MARKED_JS)});
        }
        if (url.hostname !== 'announcements.test') return route.abort();
        if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:html});
        const authenticated = !!req.headers().authorization;
        if (url.pathname === '/api/announcements') return route.fulfill(failLoad
          ? {status:503,json:{error:'测试加载失败'}}
          : {json:{authenticated, announcements:data, unread_count:data.filter(x=>x.unread).length}});
        if (url.pathname === '/api/announcements/read') {
          const body = req.postDataJSON(); writes.push({path:url.pathname,body});
          data.forEach(item => {if (body.announcement_ids.includes(item.id)) item.unread=false;});
          return route.fulfill({json:{ok:true}});
        }
        if (url.pathname === '/api/announcements/vote') {
          const body = req.postDataJSON(); writes.push({path:url.pathname,body});
          if (failVote) return route.fulfill({status:503,json:{error:'测试提交失败，请重试'}});
          const vote = {options:body.options.filter(n=>n>0), skipped:body.options.includes(0), feedback:body.feedback};
          data.find(item=>item.id===body.announcement_id).my_vote=vote;
          return route.fulfill({json:{vote}});
        }
        if (url.pathname.startsWith('/api/')) return route.fulfill({json:{count:0}});
        return route.fulfill({status:404,body:''});
      });
      await page.goto('http://announcements.test');
      await page.waitForFunction(() => announcementState.loaded);
      await page.evaluate(() => localStorage.setItem('cedartoy_token','local-fixture-only'));
      const fold = id => page.locator(`[data-announcement-toggle="${id}"]`);
      const body = id => page.locator(`[data-announcement-item="${id}"] .announcement-body`);
      const touch = locator => width<500 ? locator.tap() : locator.click();
      const open = () => page.evaluate(() => openAnnouncementList());
      const expanded = async id => {
        assert.equal(await page.locator('[data-announcement-toggle][aria-expanded="true"]').count(),id?1:0);
        assert.equal(await page.locator('.announcement-body:visible').count(),id?1:0);
        if (id) assert.equal(await fold(id).getAttribute('aria-expanded'),'true');
      };
      const geometry = async () => {
        const g = await page.evaluate(() => {
          const list=document.querySelector('#announcementList'), box=document.querySelector('#announcementModal .modal-box');
          const rect=box.getBoundingClientRect();
          return {top:rect.top,bottom:rect.bottom,left:rect.left,right:rect.right,width:innerWidth,height:innerHeight,overflow:list.scrollWidth>list.clientWidth+1,
            scroll:list.scrollTop, windowY:scrollY};
        });
        assert.ok(g.top>=0 && g.bottom<=g.height && g.left>=0 && g.right<=g.width,JSON.stringify(g));
        assert.equal(g.overflow,false); assert.equal(g.windowY,0);
        assert.equal(await page.locator('#announcementModal [data-close-modal]').isVisible(),true);
        return g;
      };
      const shot = name => page.locator('#announcementModal').screenshot({path:path.join(out,`${width}-${name}.png`)});

      // Automatic unread popup opens every notice in full; only the history
      // opened from the notification bell is an accordion.
      await page.evaluate(() => loadAnnouncements({autoPopup:true}));
      assert.equal(await page.locator('.announcement-item').count(),2);
      assert.equal(await page.locator('.announcement-body:visible').count(),2);
      assert.equal(await page.locator('[data-announcement-toggle]').count(),0);
      assert.equal(await page.locator('.announcement-arrow').count(),0);
      assert.equal(await page.locator('.announcement-unread').count(),2);
      assert.equal(await body('notice').locator('.announcement-content strong').first().textContent(),'加粗提示');
      assert.equal(await body('notice').locator('.announcement-content li').count(),2);
      assert.equal(await body('notice').locator('.announcement-content em').count(),0);
      assert.equal(await page.evaluate(() => Boolean(window.marked?.parse)),Boolean(process.env.ANNOUNCEMENT_MARKED_JS));
      assert.equal(writes.length,1); assert.deepEqual(writes[0].body.announcement_ids,['notice','poll']);
      assert.match(await page.locator('#announcementHint').innerText(),/已读不等于投票/);
      await geometry(); await shot('automatic-full');
      await open(); await expanded(null);
      assert.deepEqual(await page.locator('.announcement-item').evaluateAll(nodes=>nodes.map(n=>n.dataset.announcementItem)),data.map(x=>x.id));
      assert.equal(await page.locator('.announcement-unread').count(),0);
      assert.ok((await fold('notice').boundingBox()).height>=44);
      assert.ok((await fold('long').boundingBox()).height<110,'long title stays compact');
      await geometry(); await shot('collapsed');

      await touch(fold('notice')); await expanded('notice');
      const rendered=body('notice').locator('.announcement-content');
      assert.equal(await rendered.locator('strong').first().textContent(),'加粗提示');
      assert.equal(await rendered.locator('li').count(),2);
      assert.match(await rendered.textContent(),/原有正文[\s\S]*项目链接/);
      assert.match(await rendered.textContent(),/<em>纯文本，不执行 HTML<\/em>/);
      assert.equal(await rendered.locator('em').count(),0,'raw HTML must remain literal text');
      assert.equal(await rendered.locator('script,img').count(),0);
      await geometry(); await shot('expanded');
      // Clicking the next row after scrolling through long content keeps that row reachable.
      await fold('poll').scrollIntoViewIfNeeded();
      assert.ok((await geometry()).scroll>0);
      await touch(fold('poll')); await expanded('poll');
      const listRect=await page.locator('#announcementList').boundingBox(), rowRect=await fold('poll').boundingBox();
      assert.ok(rowRect.y>=listRect.y-1 && rowRect.y<listRect.y+listRect.height);
      assert.equal(writes.length,1,'folding does not mark read or submit votes');
      await body('poll').locator('[type=submit]').click();
      assert.match(await body('poll').innerText(),/请选择至少一个选项/); await expanded('poll');
      await body('poll').locator('[data-poll-option][value="1"]').check();
      await body('poll').locator('[data-poll-feedback]').fill('保留我的草稿');
      await fold('poll').focus(); await page.keyboard.press('Space'); await expanded(null);
      assert.equal(await fold('poll').evaluate(el=>el===document.activeElement),true);
      await page.keyboard.press('Enter'); await expanded('poll');
      await touch(fold('notice')); await touch(fold('poll'));
      assert.equal(await body('poll').locator('[value="1"]').isChecked(),true);
      assert.equal(await body('poll').locator('textarea').inputValue(),'保留我的草稿');
      assert.match(await fold('poll').ariaSnapshot(),/expanded/);
      failVote=true;
      await body('poll').locator('[type=submit]').click();
      await body('poll').getByRole('alert').filter({hasText:'测试提交失败'}).waitFor();
      await expanded('poll'); failVote=false;
      await body('poll').locator('[data-poll-skip]').check();
      assert.equal(await body('poll').locator('[value="1"]').isChecked(),false);
      await body('poll').locator('[type=submit]').click();
      await body('poll').getByText(/已跳过本次投票/).waitFor(); await expanded('poll');
      await body('poll').locator('[value="2"]').check();
      assert.equal(await body('poll').locator('[data-poll-skip]').isChecked(),false);
      await body('poll').locator('[type=submit]').click();
      await body('poll').getByText(/本人已投/).waitFor(); await expanded('poll');
      assert.equal(await body('poll').locator('form').count(),0);
      assert.deepEqual(writes.at(-1).body,{announcement_id:'poll',options:[2],feedback:'保留我的草稿'});
      await touch(fold('voted')); await expanded('voted');
      assert.match(await body('voted').innerText(),/选项二[\s\S]*已有意见/);
      await touch(fold('long')); await expanded('long');
      assert.equal(await fold('long').locator('.announcement-title').textContent(),data[3].title);
      await geometry();
      await touch(fold('long')); await expanded(null);
      await page.locator('#announcementModal [data-close-modal]').click();
      await open(); await expanded(null); assert.equal((await geometry()).scroll,0);

      // Enlarged text, short viewport, guest, empty and failed loading states.
      await page.addStyleTag({content:'#announcementModal :is(.announcement-title,.announcement-content,.announcement-meta,.modal-hint) { font-size: 200%; }'});
      await touch(fold('notice')); await geometry();
      await page.locator('style').last().evaluate(el=>el.remove());
      if (width===360) {await page.setViewportSize({width,height:500}); await geometry(); await shot('short-viewport'); await page.setViewportSize({width,height});}
      await page.evaluate(() => localStorage.removeItem('cedartoy_token'));
      const writesBeforeGuest=writes.length; await open(); await touch(fold('poll'));
      assert.match(await body('poll').innerText(),/登录人类账号后可参与投票/);
      assert.equal(await body('poll').locator('form').count(),0); assert.equal(writes.length,writesBeforeGuest);
      data=[]; await open(); assert.match(await page.locator('#announcementList').innerText(),/暂无通知/);
      failLoad=true; await open(); assert.match(await page.locator('#announcementList').innerText(),/测试加载失败/);
      assert.deepEqual(errors,[]); await page.close();
      console.log(`PASS ${width}px: automatic full notices, Markdown, history-only accordion, keyboard/touch, scroll, drafts, read/vote, guest/error`);
    }
  } finally {await browser.close();}
})().catch(err=>{console.error(err);process.exitCode=1;});
