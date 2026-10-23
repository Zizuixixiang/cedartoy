// Real browser + isolated HTTP handlers. Camping uses its real engine in a temp
// database; Detroit uses the checked-in upstream-shaped fixture and fake writes.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const out=process.env.SLOT_TEST_OUTPUT||fs.mkdtempSync('/tmp/cedartoy-page-slot-browser-');fs.mkdirSync(out,{recursive:true});
const realDetroit=Boolean(process.env.DETROIT_BROWSER_ASSETS);
(async()=>{
 const child=spawn(process.env.PYTHON||'python3',['-u',path.join(root,'tests_toy/game_page_slots_browser_bridge.py')],{cwd:root,stdio:['pipe','pipe','pipe']});
 let stderr='',browser;child.stderr.on('data',s=>stderr+=s);const report=[];
 try{
 const config=await new Promise((resolve,reject)=>{let buf='';child.stdout.on('data',s=>{buf+=s;for(const line of buf.split('\n')){try{const value=JSON.parse(line);if(value.base)resolve(value)}catch{}}});child.on('exit',code=>reject(Error('fixture '+code+' '+stderr)));});
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:1280,height:900}}),requests=[],errors=[];
 await context.route('**/*',route=>new URL(route.request().url()).origin===config.base?route.continue():route.abort());
 context.on('page',p=>{p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(r.url().includes('/api/'))requests.push({url:r.url(),method:r.method(),body:r.postData()})})});
 const pages=[await context.newPage(),await context.newPage(),await context.newPage()];
 async function enter(page,game,player){await page.goto(`${config.base}/${game}/?player=${encodeURIComponent(player)}&token=fixture`)}
 for(const game of ['detroit','camping-plaza']){
  const players=['101:5','101','202:2'];
  for(let i=0;i<pages.length;i++)await enter(pages[i],game,players[i]);
  // Deliberately poison the old global player cookie after every page is loaded.
  await context.addCookies([{name:game==='detroit'?'detroit_player':'camping_plaza_player',value:'202%3A2',url:config.base+'/'+game+'/'}]);
  for(let i=0;i<pages.length;i++){
   const p=pages[i],name=['Alpha','Beta','Gamma'][i];
   if(game==='detroit'){
    if(realDetroit){
     await p.locator('#continue').click();
     await p.waitForFunction(text=>document.querySelector('.scene-text')?.textContent===text,`Fixture ${players[i]} step 1`);
    }else{await p.locator('#value').fill(name);await p.locator('#save').click();await p.waitForFunction(n=>document.querySelector('#marker')?.textContent===n,name);}
   }else{
    await p.locator('#playerNameInput').fill(name);await p.locator('#playerNameSubmit').click();await p.locator('#onboardingScreen').waitFor({state:'hidden'});
   }
  }
  for(let i=0;i<pages.length;i++){
   const p=pages[i],expected=['Alpha','Beta','Gamma'][i];await p.reload();
   if(game==='detroit'){
    if(realDetroit){
     const expectedText=`Fixture ${players[i]} step 1`;
     await p.waitForFunction(text=>document.querySelector('.scene-text')?.textContent===text,expectedText);
     await p.locator('#home').click();await p.locator('[data-open="cedartoy-slot"]').click();
     await p.waitForFunction(text=>document.querySelector('.scene-text')?.textContent===text,expectedText);
     for(const button of ['export','backup']){
      const [download]=await Promise.all([p.waitForEvent('download'),p.locator('#'+button).click()]);
      const file=path.join(out,`detroit-${i}-${button}.json`);await download.saveAs(file);
      assert.deepEqual(JSON.parse(fs.readFileSync(file,'utf8')),{player:players[i],revision:1});
     }
    }else{
    await p.waitForFunction(n=>document.querySelector('#marker')?.textContent===n,expected);
    await p.locator('#home').click();await p.locator('#continue').click();await p.waitForFunction(n=>document.querySelector('#marker')?.textContent===n,expected);
    await p.locator('#internal').click();await p.waitForFunction(n=>document.querySelector('#marker')?.textContent===n,expected);
    assert.equal(new URL(await p.locator('#export').getAttribute('href')).searchParams.get('player'),players[i]);
    }
   }else{
    await p.locator('#onboardingScreen').waitFor({state:'hidden'});
    const state=await p.evaluate(async()=>{const r=await fetch('/camping-plaza/api/state?session_id=attacker');return r.json()});assert.equal(state.player_name,expected);
   }
   assert.equal(new URL(p.url()).searchParams.get('player'),players[i]);
   report.push({game,player:players[i],readWriteReloadContinue:'passed'});
  }
  for(const width of [360,430,1280]){await pages[0].setViewportSize({width,height:900});await pages[0].screenshot({path:path.join(out,`${game}-${width}.png`)});}
 }
 // Native browser requests bypass the bridge here to verify the server rejects
 // absent/invalid/unbound targets rather than recovering from a cookie.
 for(const game of ['detroit','camping-plaza'])for(const player of ['', '303','101:0','101:6','101:bad']){
  const url=`${config.base}/${game}/api/${game==='detroit'?'session':'state'}?player=${encodeURIComponent(player)}`;
  assert.equal((await context.request.get(url)).status(),403);
 }
 const anonymous=await browser.newContext();assert.equal((await anonymous.request.get(config.base+'/detroit/api/sessions?player=101')).status(),401);await anonymous.close();
 assert.deepEqual(errors,[]);
 for(const r of requests){if(r.url.includes('/detroit/api/')||r.url.includes('/camping-plaza/api/'))assert(new URL(r.url).searchParams.get('player'),r.url)}
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({report,errors,requests},null,2));console.log(JSON.stringify({report,errors,output:out}));
 }finally{if(browser)await browser.close();if(child.exitCode===null){child.stdin.end('\n');await new Promise(resolve=>child.once('exit',resolve))}fs.writeFileSync(path.join(out,'fixture.log'),stderr)}
})().catch(e=>{console.error(e);process.exitCode=1});
