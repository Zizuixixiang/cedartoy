'use strict';
// No network or production credentials. DOM behavior only, not visual proof.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM,VirtualConsole}=require('jsdom');
const errors=[],requests=[];
let allowed=false,fail=false,holdAccess=null;
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
const source=fs.readFileSync('index.html','utf8');
// JSDOM cannot navigate documents; spy on the single literal navigation statement.
const navigation='window.location.href = "/autochess/#management";';
assert.equal(source.split(navigation).length,2);
const dom=new JSDOM(source.replace(navigation,'window.testNavigation = "/autochess/#management";'),{
 url:'http://home.test/',runScripts:'dangerously',pretendToBeVisual:true,
 virtualConsole:new VirtualConsole().on('jsdomError',e=>errors.push(e.message)),
 beforeParse(w){
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  w.localStorage.setItem('cedartoy_token','fixture-author');
  w.fetch=async(url,options={})=>{
   if(url==='/api/auth/me')return response({user:{id:101,username:'测试用户',is_admin:true},bindings:[]});
   if(url==='/api/games/stats')return response({autochess:{metric_label:'存档数',metric:16}});
   if(url==='/autochess/api/stats'){
    const body=JSON.parse(options.body);requests.push({body,options});
    assert.deepEqual(body,{op:'access'},'Homepage never requests detailed stats');
    if(holdAccess)return new Promise(resolve=>{holdAccess=()=>resolve(response({allowed:true}));});
    return fail?response({},503):response({allowed});
   }
   return response({});
  };
 }
});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
const tick=()=>new Promise(r=>setTimeout(r,10));
async function until(check,hint){for(let i=0;i<100;i++){if(check())return;await tick();}throw Error(hint+' '+errors.join(';'));}
const visible=()=>[...d.querySelectorAll('[data-autochess-stats-entry]')].every(el=>el.style.display!=='none');
(async()=>{try{
 await until(()=>w.eval('me?.user?.id')===101,'login');
 w.selectGame('autochess',false);await tick();
 assert.equal($('detailMetricLabel').textContent,'存档数');assert.equal($('detailMetric').textContent,'16');
 assert.equal($('autochessStatsModal'),null);
 assert.equal(visible(),false,'site admin alone has no grant');
 d.querySelector('[data-autochess-stats]').click();await tick();assert.equal(w.testNavigation,undefined);
 allowed=true;await w.refreshAutochessStatsAccess();assert(visible());
 assert.equal(d.querySelectorAll('[data-autochess-stats-entry]').length,2);
 for(const entry of d.querySelectorAll('[data-autochess-stats]')){
  assert.equal(entry.textContent,'管理统计');w.testNavigation=undefined;
  entry.click();await tick();assert.equal(w.testNavigation,'/autochess/#management');
 }
 // Revalidate on click, so revoked access or errors cannot navigate with a stale grant.
 w.testNavigation=undefined;allowed=false;d.querySelector('[data-autochess-stats]').click();await tick();
 assert(!visible());assert.equal(w.testNavigation,undefined);
 allowed=true;await w.refreshAutochessStatsAccess();fail=true;d.querySelector('[data-autochess-stats]').click();await tick();
 assert(!visible());assert.equal(w.testNavigation,undefined);fail=false;
 // Cross-tab logout while the click's access request is pending must cancel navigation.
 await w.refreshAutochessStatsAccess();holdAccess=true;d.querySelector('[data-autochess-stats]').click();await tick();
 const lateClick=holdAccess;holdAccess=null;
 w.localStorage.removeItem('cedartoy_token');w.dispatchEvent(new w.StorageEvent('storage',{key:'cedartoy_token'}));
 assert(requests.at(-1).options.signal.aborted);lateClick();await tick();
 assert(!visible());assert.equal(w.testNavigation,undefined);
 // Delayed allow from a previous identity cannot grant the new identity.
 w.localStorage.setItem('cedartoy_token','fixture-author');holdAccess=true;void w.refreshAutochessStatsAccess();await tick();
 const lateAccess=holdAccess;holdAccess=null;allowed=false;
 w.localStorage.setItem('cedartoy_token','fixture-other');w.dispatchEvent(new w.StorageEvent('storage',{key:'cedartoy_token'}));
 lateAccess();await tick();assert(!visible());
 allowed=true;await w.refreshAutochessStatsAccess();
 w.localStorage.removeItem('cedartoy_token');await w.loadMe();assert(!visible());
 d.querySelector('[data-autochess-stats]').click();await tick();assert.equal(w.testNavigation,undefined);
 w.selectGame('soup',false);await tick();assert(!visible());
 assert.deepEqual(errors,[]);
 console.log('Homepage DOM passed: save count, authorized management navigation, no modal, revocation, failure, identity races and logout.');
}finally{w.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
