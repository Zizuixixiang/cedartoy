/* Non-rendering fallback: actual homepage scripts, identical DOM and navigation.
 * This does not substitute for the companion Playwright visual check.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM, VirtualConsole} = require('jsdom');
const current = fs.readFileSync('index.html','utf8');
const reversed = current.replaceAll(/\/api\/auth\/saves\?game=(workkk|moonlit|ai_life|detroit|camping_plaza)/g,'/api/auth/saves');
const baseline = process.env.FILTERED_PICKER_BASELINE ? fs.readFileSync(process.env.FILTERED_PICKER_BASELINE,'utf8') : reversed;
assert.equal(reversed,baseline,'Only five fetch URLs may change');
for (const [,code] of current.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new vm.Script(code);
const bindings=[{id:101,username:'测试小机甲'},{id:202,username:'测试小机乙'}];
const games=[['workkk','Workkk','workkk-machine-option','workkk-slot','workkk'],
  ['moonlit','Moonlit','moonlit-machine','moonlit-slot','moonlit'],
  ['ai_life','AiLife','ai-life-machine','ai-life-slot','ai-life'],
  ['detroit','Detroit','detroit-machine','detroit-slot','detroit'],
  ['camping_plaza','CampingPlaza','camping-machine','camping-slot','camping-plaza']];
(async()=>{
  let cases=0;
  for (const width of [360,428,1280]) for(const [game,fn,machineAttr,slotAttr,url] of games){
    const versions=[];
    for(const [version,source] of [['baseline',baseline],['current',current]]){
      let state='multiple';const requests=[],errors=[];
      const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
      const dom=new JSDOM(source.replaceAll('window.location.href = url;', 'window.__navigation = url;'),{
        url:'http://picker.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
        beforeParse(w){
          w.innerWidth=width;w.localStorage.setItem('cedartoy_token','fixture-token');
          w.matchMedia=()=>({matches:false,addListener(){},addEventListener(){}});
          w.fetch=async address=>{
            const url=new URL(address,w.location.href);
            if(url.pathname==='/api/auth/saves'){
              requests.push(url.pathname+url.search);
              if(state==='expired') return {status:401,ok:false,json:async()=>({})};
              if(state==='failed') return {status:400,ok:false,json:async()=>({error:'存档摘要读取失败，请稍后再试'})};
              const save=slot=>({slot,day:7,kind:'current_decision',action_count:9,turn:4,name:'测试存档',chapter:3,player_name:'测试营地',balance:120});
              const machines=bindings.map((user,i)=>({user,saves:state==='empty'||(state==='single'&&i===1)?{}:
                {[game]:{slots:state==='single'?[save(5)]:i===0?[save(1),save(5)]:[save(2)]}}}));
              return {status:200,ok:true,json:async()=>({machines})};
            }
            return {status:200,ok:true,json:async()=>url.pathname==='/api/auth/me'?{user:{id:1,username:'测试人类'},bindings}:{}};
          };
        }
      });
      try{
        const w=dom.window,d=w.document;
        await new Promise(resolve=>setTimeout(resolve,30));
        const pictures={};
        const capture=name=>{pictures[name]=d.querySelector('#bankPicker').outerHTML;};
        const open=async()=>{w.eval('selected='+JSON.stringify(game));await w['open'+fn+'Picker'](bindings);};
        await open();capture('machines');
        d.querySelector(`[data-${machineAttr}="101"]`).click();capture('slots');
        assert(d.querySelector('#bankPickerOptions').textContent.includes('槽5'));
        d.querySelector(`[data-${slotAttr}="5"]`).click();
        assert.equal(w.__navigation,`/${url}/?player=101%3A5&token=fixture-token`);
        assert(!d.querySelector('#bankPicker').classList.contains('show'));
        state='single';w.__navigation=null;await open();
        if(game==='workkk')d.querySelector(`[data-${machineAttr}="101"]`).click();
        assert.equal(w.__navigation,`/${url}/?player=101%3A5&token=fixture-token`);
        for(const mode of ['empty','expired','failed']){
          state=mode;await open();capture(mode);
          if(mode==='expired')assert(d.querySelector('#bankPickerOptions').textContent.includes('登录已失效'));
          if(mode==='empty'&&game!=='workkk')assert.equal(d.querySelectorAll('#bankPickerOptions button').length,0);
        }
        assert.deepEqual(errors,[]);
        assert.equal(requests.length,5);
        assert(requests.every(u=>u===`/api/auth/saves${version==='current'?'?game='+game:''}`));
        versions.push(pictures);
      }finally{dom.window.close();}
    }
    assert.deepEqual(versions[1],versions[0]);cases++;
  }
  console.log(`PASS: ${cases} game/viewport configurations; identical DOM in 5 states, existing-slot navigation, filtered requests, JS syntax. No rendered-layout claim.`);
})().catch(e=>{console.error(e);process.exit(1)});
