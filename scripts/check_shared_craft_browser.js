/* Original/adapted UI comparison. All APIs use disposable accounts and stores. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {bridge} = require('./shared_craft_test_bridge');
const original = path.resolve('vendor/shared-craft-room/src/shared_craft_room/static');
const output = process.env.SHARED_CRAFT_SCREENSHOTS || '/tmp/shared-craft-visual';
fs.mkdirSync(output,{recursive:true});

(async()=>{
  const api = bridge({visual:true});
  let browser;
  const report = [];
  try {
    await api.ready;
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    for (const width of [360,430,1280]) {
      const page = await browser.newPage({viewport:{width,height:900},hasTouch:width<600});
      const errors = [];
      const details = {};
      let sharePosts = 0;
      const measure = () => page.evaluate(() => {
        const rect = element => {
          const r=element.getBoundingClientRect();
          const s=getComputedStyle(element);
          return {x:r.x,y:r.y,width:r.width,height:r.height,font:s.font,gap:s.gap};
        };
        return {
          scrollWidth:document.documentElement.scrollWidth,
          elements:Object.fromEntries(['#board','.tools','.box-head','#stock-actor','#box-count','.work-grid','.work','.work .actions']
            .filter(s=>document.querySelector(s)).map(s=>[s,rect(document.querySelector(s))])),
          buttons:[...document.querySelectorAll('.work .actions button')].map(b=>({text:b.textContent,...rect(b)})),
        };
      });
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url()), p=url.pathname;
        if(url.hostname !== 'craft.test') return route.abort();
        if(p.startsWith('/room/reference/')) {
          const suffix=p.slice('/room/reference/'.length);
          const file=suffix.startsWith('assets/') ? suffix.slice(7) : suffix.replace(/\/$/,'')+'.html';
          return route.fulfill({path:path.join(original,file)});
        }
        const mapped=p.startsWith('/api/reference/') ? 'api/'+p.slice('/api/reference/'.length) : p.replace(/^\/shared-craft\//,'');
        if(mapped.startsWith('api/shares') && route.request().method()==='POST') sharePosts++;
        const raw=route.request().postData();
        const response=await api.request(mapped,route.request().method(),raw?JSON.parse(raw):undefined);
        return route.fulfill({status:response.status,contentType:response.contentType,body:Buffer.from(response.body,'base64')});
      });
      for(const name of ['sketch','beads','crafts']) {
        const geometry=[];
        for(const variant of ['original','adapted']) {
          const prefix=variant==='original'?'/room/reference/':'/shared-craft/';
          await page.goto(`https://craft.test${prefix}${name}/`);
          await page.waitForTimeout(700);
          const selector={sketch:'.paper-wrap',beads:'#boards',crafts:'.wall'}[name];
          await page.locator(selector).waitFor();
          geometry.push(await page.locator(selector).boundingBox());
          assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
          await page.screenshot({path:path.join(output,`${width}-${name}-${variant}.png`),fullPage:true});
          if(name==='beads') {
            await page.locator('.board-card').first().click();
            await page.locator('#board').waitFor();
            await page.waitForTimeout(300);
            details[`beads-${variant}`]=await measure();
            await page.screenshot({path:path.join(output,`${width}-beads-studio-${variant}.png`),fullPage:true});
            if(variant==='adapted') {
              assert.equal(await page.locator('#stock-actor option').count(),2);
              await page.selectOption('#stock-actor','3');
              assert.equal(await page.locator('#stock-actor').inputValue(),'3');
              const after=await measure();
              assert.deepEqual(after.elements['#board'],details['beads-adapted'].elements['#board'],'inventory selection moved board');
              assert.deepEqual(after.elements['.tools'],details['beads-adapted'].elements['.tools'],'inventory selection moved tools');
              await page.screenshot({path:path.join(output,`${width}-beads-studio-selected.png`),fullPage:true});
              // Opening the native chooser is transient; it must not resize the tray.
              await page.locator('#stock-actor').focus();
              await page.keyboard.press('Space');
              const opened=await measure();
              assert.deepEqual(opened.elements['#board'],after.elements['#board']);
              await page.keyboard.press('Escape');
              details.inventorySelectionStable=true;
            }
          }
          if(name==='crafts') {
            await page.locator('#open-all').click();
            await page.waitForTimeout(300);
            details[`crafts-${variant}`]=await measure();
            await page.screenshot({path:path.join(output,`${width}-crafts-drawer-${variant}.png`),fullPage:true});
            if(variant==='adapted') {
              const shares=await page.locator('[data-share]').evaluateAll(buttons=>buttons.map(button=>{
                const range=document.createRange(); range.selectNodeContents(button);
                const r=button.getBoundingClientRect(), parent=button.parentElement.getBoundingClientRect();
                return {lines:range.getClientRects().length,width:r.width,parentWidth:parent.width};
              }));
              for(const share of shares) {
                assert.equal(share.lines,1,'share label wraps inside gallery card');
                assert(Math.abs(share.width-share.parentWidth)<1,'share action must fill its grid row');
              }
              details.shareButtons=shares;
              assert.equal(await page.locator('#share-dialog[open]').count(),0);
              assert.equal(sharePosts,0,'opening the gallery must not share');
            }
          }
        }
        for(const key of ['x','y','width','height'])
          assert(Math.abs(geometry[0][key]-geometry[1][key])<2, `${name}/${width} changed ${key}`);
        if(name==='crafts') {
          assert.deepEqual(details['crafts-original'].elements['.work-grid'].width,
            details['crafts-adapted'].elements['.work-grid'].width);
          const author=async()=>JSON.parse(Buffer.from((await api.request('author','GET',undefined,6)).body,'base64'));
          assert.equal((await author()).total,0);
          await page.getByRole('button',{name:'确认分享',exact:true}).click();
          await page.locator('#share-dialog[open]').waitFor();
          await page.screenshot({path:path.join(output,`${width}-share-confirm.png`),fullPage:true});
          await page.locator('#share-cancel').click();
          assert.equal(await page.locator('#share-dialog[open]').count(),0);
          assert.equal(sharePosts,0,'cancel must not share');
          assert(await page.getByRole('button',{name:'确认分享',exact:true}).evaluate(b=>b===document.activeElement));
          await page.getByRole('button',{name:'分享给作者',exact:true}).click();
          await page.locator('#share-dialog[open]').waitFor();
          assert.equal(await page.locator('#share-note').inputValue(),'');
          assert.equal(await page.locator('#share-note').getAttribute('readonly'),null);
          await page.screenshot({path:path.join(output,`${width}-share-optional.png`),fullPage:true});
          await page.locator('#share-cancel').click();
          await page.reload();
          await page.locator('#open-all').click();
          assert.equal(await page.locator('.work').count(),2);
          if(width===1280) {
            await page.getByRole('button',{name:'确认分享',exact:true}).click();
            await page.locator('#share-dialog[open]').waitFor();
            await page.locator('#share-form button[type=submit]').click();
            await page.waitForFunction(()=>document.querySelector('#status').textContent==='已分享给作者');
            assert.equal(sharePosts,1);
            assert.equal((await author()).total,1);
            assert.equal((await author()).items[0].note,'谢谢你做了这间手作房');
            details.explicitHumanApproval=true;
          }
        }
      }
      assert.deepEqual(errors,[]);
      report.push({width,originalGeometryPreserved:true,errors,details});
      await page.close();
    }
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
    console.log(`PASS: 360/430/1280 original comparison, card grid, inventory selection, native consent; ${output}/report.json`);
  } finally { await browser?.close(); await api.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
