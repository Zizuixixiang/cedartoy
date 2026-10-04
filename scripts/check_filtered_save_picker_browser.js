/* Compare the unchanged pickers to their unfiltered baseline with identical data.
 * Requests are intercepted; no live account or game service is used.
 * PLAYWRIGHT_MODULE and FILTERED_PICKER_BASELINE can point to local installations.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const current = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const baseline = process.env.FILTERED_PICKER_BASELINE
  ? fs.readFileSync(process.env.FILTERED_PICKER_BASELINE, 'utf8')
  : current.replaceAll(/\/api\/auth\/saves\?game=(workkk|moonlit|ai_life|detroit|camping_plaza)/g, '/api/auth/saves');
assert.equal(current.replaceAll(/\/api\/auth\/saves\?game=(workkk|moonlit|ai_life|detroit|camping_plaza)/g, '/api/auth/saves'), baseline,
  'Frontend diff must contain only the five fetch URL changes');
for (const [, code] of current.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new vm.Script(code);
const output = process.env.FILTERED_PICKER_OUTPUT || '/tmp/cedartoy-filtered-picker-browser';
fs.mkdirSync(output, {recursive:true});
const games = [
  {id:'workkk', fn:'openWorkkkPicker', machine:'data-workkk-machine-option', slot:'data-workkk-slot', url:'workkk'},
  {id:'moonlit', fn:'openMoonlitPicker', machine:'data-moonlit-machine', slot:'data-moonlit-slot', url:'moonlit'},
  {id:'ai_life', fn:'openAiLifePicker', machine:'data-ai-life-machine', slot:'data-ai-life-slot', url:'ai-life'},
  {id:'detroit', fn:'openDetroitPicker', machine:'data-detroit-machine', slot:'data-detroit-slot', url:'detroit'},
  {id:'camping_plaza', fn:'openCampingPlazaPicker', machine:'data-camping-machine', slot:'data-camping-slot', url:'camping-plaza'},
];
const bindings = [{id:101,username:'测试小机甲'}, {id:202,username:'测试小机乙'}];
const save = slot => ({slot,day:7,saved:true,kind:'current_decision',action_count:9,turn:4,
  status:'playing',draft_round:2,score:10,name:'测试存档',chapter:3,player_name:'测试营地',balance:120});
(async()=>{
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const report=[];
  try {
    for (const width of [360,428,1280]) {
      for (const game of games) {
        const snapshots=[];
        for (const version of ['baseline','current']) {
          const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<600});
          let state='multiple';
          const requests=[],errors=[],pictures={};
          page.on('pageerror',error=>errors.push(error.message));
          await page.addInitScript(()=>localStorage.setItem('cedartoy_token','fixture-token'));
          await page.route('**/*',async route=>{
            const url=new URL(route.request().url());
            if (url.hostname!=='picker.test') return route.abort();
            if (url.pathname==='/') return route.fulfill({contentType:'text/html',body:version==='current'?current:baseline});
            if (url.pathname==='/api/auth/saves') {
              requests.push(url.pathname+url.search);
              if(state==='expired') return route.fulfill({status:401,json:{error:'expired'}});
              if(state==='failed') return route.fulfill({status:400,json:{error:'存档摘要读取失败，请稍后再试'}});
              const machines=bindings.map((user,i)=>({user,saves: state==='empty' || (state==='single'&&i===1) ? {} :
                {[game.id]:{slots:state==='single'?[save(5)]:i===0?[save(1),save(5)]:[save(2)]}}}));
              return route.fulfill({json:{machines}});
            }
            if(url.pathname==='/api/auth/me') return route.fulfill({json:{user:{id:1,username:'测试人类'},bindings}});
            if(url.pathname.startsWith('/api/')) return route.fulfill({json:{}});
            if(url.pathname===`/${game.url}/`) return route.fulfill({contentType:'text/html',body:'<title>Captured existing save navigation</title>'});
            const file=path.join(root,url.pathname);
            if(file.startsWith(root+'/')&&fs.existsSync(file)&&fs.statSync(file).isFile()) return route.fulfill({path:file});
            return route.abort();
          });
          async function home(){
            await page.goto('http://picker.test/');
            await page.waitForFunction(()=>Boolean(me?.user));
          }
          async function open(){
            await page.evaluate(async ({game,bindings})=>{selected=game.id;await window[game.fn](bindings);},{game,bindings});
          }
          async function capture(name){
            const picker=page.locator('#bankPickerPanel');
            await picker.waitFor({state:'visible'});
            pictures[name]={html:await picker.innerHTML(),metrics:await picker.evaluate(el=>[el,...el.querySelectorAll('button,span')].map(node=>{
              const s=getComputedStyle(node),r=node.getBoundingClientRect();
              return {x:r.x,y:r.y,width:r.width,height:r.height,font:s.fontFamily,size:s.fontSize,lineHeight:s.lineHeight,
                border:s.borderWidth,padding:s.padding,shadow:s.boxShadow};
            }))};
            pictures[name].png=await picker.screenshot({animations:'disabled',path:path.join(output,`${width}-${game.id}-${version}-${name}.png`)});
          }
          await home();await open();await capture('machines');
          await page.locator(`[${game.machine}="101"]`).click();await capture('slots');
          assert((await page.locator('#bankPickerOptions').innerText()).includes('槽5'));
          await page.locator(`[${game.slot}="5"]`).click();
          await page.waitForURL(url=>url.pathname===`/${game.url}/`);
          assert.equal(new URL(page.url()).searchParams.get('player'),'101:5');
          assert.equal(new URL(page.url()).searchParams.get('token'),'fixture-token');
          state='single';await home();await open();
          if(game.id==='workkk') await page.locator(`[${game.machine}="101"]`).click();
          await page.waitForURL(url=>url.pathname===`/${game.url}/`);
          assert.equal(new URL(page.url()).searchParams.get('player'),'101:5');
          for(const mode of ['empty','expired','failed']) {
            state=mode;await home();await open();await capture(mode);
            if(mode==='expired') assert((await page.locator('#bankPickerOptions').innerText()).includes('登录已失效'));
            if(mode==='empty'&&game.id!=='workkk') assert.equal(await page.locator('#bankPickerOptions button').count(),0);
          }
          assert.deepEqual(errors,[]);
          assert(requests.length===5);
          assert(requests.every(url=>url===`/api/auth/saves${version==='current'?'?game='+game.id:''}`));
          snapshots.push(pictures);
          await page.close();
        }
        assert.deepEqual(snapshots[1],snapshots[0],`${game.id}@${width}: identical HTML, geometry, typography and pixels`);
        report.push({width,game:game.id,states:5,pixelIdentical:true,navigation:'101:5',fetchOnly:true});
      }
    }
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));
  } finally {await browser.close();}
})().catch(err=>{console.error(err);process.exit(1)});
