// Copy-only lobby regression using the existing HTML/CSS and exact hint functions.
// No application backend, credentials, production requests or new dependencies.
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const staticDir = path.join(root, 'vendor/duel/app/static');
const baseline = process.argv.includes('--before');
const out = path.join(root, 'artifacts/token-opt/ui', baseline ? 'before' : 'after');
fs.mkdirSync(out, {recursive:true});
const script = fs.readFileSync(process.env.TOKEN_HINT_SOURCE || path.join(staticDir,'app.js'),'utf8');
const functions = script.slice(script.indexOf('function gameTokenEstimateLabel('),script.indexOf('function sortedGamesForCategory('));
const html = fs.readFileSync(path.join(staticDir,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
const games = {monopoly:'大富翁 / 2–6人',rummikub:'拉密 / 2–4人',bomb_plane:'炸飞机 / 2人',carcassonne:'卡卡颂 / 2–5人'};
(async()=>{
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const measurements=[];
  try {
    for (const width of [360,430,1280]) {
      const page = await browser.newPage({viewport:{width,height:1000}});
      await page.route('**/*',route=>{
        const url = new URL(route.request().url());
        if (url.hostname !== 'token.test') return route.abort();
        if (url.pathname==='/') return route.fulfill({contentType:'text/html',body:html});
        const target = path.join(staticDir,url.pathname.replace(/^\/static\//,''));
        if (!target.startsWith(staticDir+path.sep) || !fs.existsSync(target)) return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:target.endsWith('.css')?'text/css':'application/octet-stream',body:fs.readFileSync(target)});
      });
      await page.goto('http://token.test/');
      await page.addScriptTag({content:`const $ = id => document.getElementById(id);\n${functions}`});
      await page.evaluate(games=>{
        document.getElementById('loadingView').classList.add('hidden');
        document.getElementById('lobbyView').classList.remove('hidden');
        document.getElementById('heroPair').textContent='本地 Token 提示检查';
        document.getElementById('aiPlayer').innerHTML='<option>示例小机</option>';
        document.getElementById('gameType').innerHTML=Object.entries(games).map(([id,name])=>`<option value="${id}">${name}</option>`).join('');
        document.getElementById('gameType').addEventListener('change',()=>updateGameTokenEstimate());
      },games);
      let lastHeight;
      for (const game of Object.keys(games)) {
        await page.selectOption('#gameType',game);
        const measurement=await page.evaluate(()=>{
          const el=document.getElementById('gameTokenEstimate');
          const rect=el.getBoundingClientRect();
          const style=getComputedStyle(el);
          const select=document.getElementById('gameType');
          const selectStyle=getComputedStyle(select);
          return {text:el.textContent,title:el.title,font:style.fontFamily,fontSize:style.fontSize,
            labelHeight:rect.height,labelRight:rect.right,formHeight:document.querySelector('.create-form').getBoundingClientRect().height,
            selectHeight:select.getBoundingClientRect().height,selectFont:selectStyle.fontSize,border:selectStyle.borderWidth,
            gap:getComputedStyle(el.closest('.pixel-field')).gap,overflow:document.documentElement.scrollWidth>innerWidth};
        });
        if (measurement.overflow || measurement.labelRight>width || measurement.selectHeight<44) throw Error(JSON.stringify(measurement));
        if (lastHeight!==undefined && lastHeight!==measurement.formHeight) throw Error('Hint switch changed form height');
        lastHeight=measurement.formHeight;
        if (!baseline && !measurement.title.includes('正常轮次MCP增量估算')) throw Error('Missing scope');
        measurements.push({width,game,...measurement});
      }
      await page.selectOption('#gameType','monopoly');
      await page.locator('.create-panel').screenshot({path:path.join(out,`lobby-${width}.png`)});
      await page.close();
    }
    fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measurements,null,2)+'\n');
    console.log(`${baseline?'before':'after'}: ${measurements.length} width/game checks passed`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
