/* Original UI: synthetic fixture by default; host acceptance passes live temp-server config. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const live = process.env.NOWHERE_BROWSER_CONFIG ? JSON.parse(fs.readFileSync(process.env.NOWHERE_BROWSER_CONFIG, 'utf8')) : null;
const player = live?.player || '101:2';
const origin = live?.origin || 'https://nowhere.test';
const output = process.env.NOWHERE_SCREENSHOTS || '/tmp/nowhere-visual';
fs.mkdirSync(output, {recursive:true});
const rendered = spawnSync('python3', ['-c', 'from nowhere_adapter.web import render_page; import sys;sys.stdout.buffer.write(render_page())'], {cwd:root});
assert.equal(rendered.status, 0, rendered.stderr.toString());
const card = {id:1,text:'在这里停了一会儿，给你寄一张明信片。',stamp:{place:'北京',lat:39.9,lon:116.4,local_time:'2026-10-02T15:20:00+08:00',elevation:44,weather:'晴'},replies:[],sent_at:'2026-10-02T15:20:00+08:00'};
const fixtures = {
  state:{pos:[39.9,116.4],path:[],mode:'land',local_time:'2026-10-02T15:20:00+08:00',last_text:'你沿着街道往北走。风吹过树梢，路旁有一串脚印。这里的故事才刚刚开始。',env:{elevation:44,temp_c:21,wind_ms:2,surface:'urban'},radio:null},
  history:{landings:[],path:[],footprints:[{action:'walk',place:'北京',at:'2026-10-02T15:20:00+08:00',text:'沿着街道往北走，风吹过树梢。'}]},
  marks:[],sightings:[],postcards:[card],
};
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const report=[];
  try {
    for(const width of [360,428,1280]){
      const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<600,extraHTTPHeaders:live?{Authorization:'Bearer '+live.token}:{}});
      const errors=[],requests=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',async route=>{
        const url=new URL(route.request().url());
        if(url.origin!==origin)return route.abort();
        if(live){
          if(!/\.(js|css|jpg|png)$/.test(url.pathname)&&url.pathname!=='/nowhere/')
            requests.push({path:url.pathname,method:route.request().method(),player:url.searchParams.get('player'),body:route.request().postData()});
          return route.continue();
        }
        const p=url.pathname;
        if(p==='/nowhere/')return route.fulfill({body:rendered.stdout,contentType:'text/html'});
        if(p==='/nowhere/platform.js'||p==='/nowhere/platform.css')return route.fulfill({body:fs.readFileSync(path.join(root,'nowhere_adapter/assets',path.basename(p))),contentType:p.endsWith('.js')?'text/javascript':'text/css'});
        if(p==='/nowhere/static/world110m.js')return route.fulfill({body:fs.readFileSync(path.join(root,'vendor/nowhere/nowhere/static/world110m.js')),contentType:'text/javascript'});
        requests.push({path:p,method:route.request().method(),player:url.searchParams.get('player'),body:route.request().postData()});
        const fixture=fixtures[p.replace('/nowhere/','')];
        if(fixture)return route.fulfill({json:fixture});
        if(p==='/nowhere/postcard/1/reply')return route.fulfill({json:{ok:true}});
        return route.fulfill({status:404,json:{error:'not found'}});
      });
      await page.goto(origin+'/nowhere/?player='+encodeURIComponent(player));
      await page.locator('#now .text').waitFor();
      await page.waitForFunction(()=>document.querySelector('#now .text')?.textContent.trim().length>0);
      await page.waitForFunction(()=>document.querySelector('#platform-status')?.hidden!==false);
      assert.equal(await page.locator('#platform-credit').count(),0);
      assert.equal(await page.locator('#wallbtn').evaluate(node=>getComputedStyle(node).writingMode),'vertical-rl');
      await page.screenshot({path:path.join(output,`${width}-map.png`)});
      await page.locator('#zoomrange').evaluate(node => { node.value = '2'; });
      await page.locator('#zoomrange').dispatchEvent('input');
      await page.locator('#wallbtn').click();
      // Live HTTP acceptance already replied to this card, so it belongs to
      // the upstream "寄回家的" tab (including on subsequent viewport runs).
      if(live)await page.locator('#tabReplied').click();
      await page.locator('.mini').first().click();
      await page.locator('#replyin').fill('收到，慢慢走。');
      await page.screenshot({path:path.join(output,`${width}-postcard.png`)});
      await page.getByRole('button',{name:'寄回',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('#replyin').value==='');
      assert(requests.some(r=>/^\/nowhere\/postcard\/\d+\/reply$/.test(r.path)&&r.player===player&&JSON.parse(r.body).content==='收到，慢慢走。'));
      await page.locator('.flipbar span').filter({hasText:'合上'}).click();
      await page.locator('#wallback').click();
      await page.locator('#trailopen').click();
      await page.waitForFunction(()=>{
        const trail=document.querySelector('#trail'),box=trail.getBoundingClientRect();
        return trail.classList.contains('open')&&Math.abs(box.left)<.1;
      });
      await page.screenshot({path:path.join(output,`${width}-trail.png`)});
      const measurements=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,viewport:innerWidth,map:document.querySelector('#map').getBoundingClientRect().width,bodyFont:getComputedStyle(document.body).fontFamily,textFont:getComputedStyle(document.querySelector('#now .text')).fontSize}));
      assert.equal(measurements.scroll,width);
      assert(requests.every(r=>r.path.startsWith('/nowhere/')&&r.player===player));
      assert.deepEqual(errors,[]);
      report.push({mode:live?'live-real-engine':'synthetic-fixture',width,...measurements,requests:requests.length,errors});
      await page.close();
    }
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
