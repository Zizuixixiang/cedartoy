/* DOM/state regression only; navigation sinks are captured, not browser-tested.
 * Separate JSDOM origins deliberately have separate localStorage. No real APIs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('index.html','utf8');
const origin = 'https://craft-handoff.test';
const tick = async () => { for(let i=0;i<6;i++) await new Promise(resolve=>setImmediate(resolve)); };
async function fixture(width, url=origin+'/', storedToken='') {
  const dom=new JSDOM(html,{url,runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,requests=[],navigations=[];
  const state={failLogin:false,sessionStatus:200,delayedSession:null};
  if(storedToken) w.localStorage.setItem('cedartoy_token',storedToken);
  w.matchMedia=q=>({matches:q.includes('599px')&&width<600,addEventListener(){},removeEventListener(){}});
  w.scrollTo=()=>{}; w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.__navigate=url=>navigations.push(url);
  w.fetch=async (url,options={})=>{
    requests.push({url,options,protocol:w.location.protocol});
    let data={},status=200;
    if(url==='/api/announcements') data={announcements:[],unread_count:0};
    if(url==='/api/auth/me') {
      if(storedToken==='expired') status=401;
      else data={user:{id:101,username:'验收账号'},bindings:[]};
    }
    if(url==='/api/auth/login') {
      status=state.failLogin?401:200;
      data=state.failLogin?{error:'密码错误'}:{token:'https-login-secret',user:{id:101,username:'验收账号'}};
    }
    if(url==='/shared-craft/author?access=1') data={allowed:false};
    if(url==='/shared-craft/session') {
      assert.equal(w.location.protocol,'https:');
      assert(!JSON.stringify(options).includes('http-only-secret'));
      if(state.delayedSession) return state.delayedSession;
      status=state.sessionStatus; data={error:'手作空间暂时不可用'};
    }
    return {ok:status===200,status,json:async()=>data};
  };
  for(const script of d.querySelectorAll('script:not([src])')) w.eval(script.textContent
    .replaceAll('location.assign(url.href);','window.__navigate(url.href);')
    .replace('location.assign("/shared-craft/crafts/");','window.__navigate("/shared-craft/crafts/");')
    .replace('window.location.href = game.url;','window.__navigate(game.url);'));
  await tick();
  const enter=async()=>{
    w.selectGame('shared_craft',true);
    d.getElementById(width<600?'drawerWatchButton':'watchButton').click(); await tick();
  };
  const login=async()=>{
    d.getElementById('loginUser').value='验收账号';
    d.getElementById('loginPass').value='fixture-password';
    await w.login(); await tick();
  };
  const sessions=()=>requests.filter(r=>r.url==='/shared-craft/session');
  const loginOpen=()=>d.getElementById('loginModal').classList.contains('show');
  return {w,d,requests,navigations,state,enter,login,sessions,loginOpen,close:()=>w.close()};
}
(async()=>{
  for(const width of [360,390,430,1280]) {
    // Every secondary control upgrades before session/auth; arbitrary query/hash
    // and HTTP storage are not copied. HTTP with no intent remains HTTP.
    for(const id of ['watchButton','guideWatchButton','drawerWatchButton']) {
      const f=await fixture(width,'http://craft-handoff.test/?discard=1#old','http-only-secret');
      assert.deepEqual(f.navigations,[]);
      f.w.selectGame('shared_craft',true); f.d.getElementById(id).click(); await tick();
      assert.deepEqual(f.navigations,[origin+'/#shared-craft']);
      assert.equal(f.sessions().length,0); assert(!f.loginOpen()); f.close();
    }
    // HTTPS anonymous: consume marker synchronously, retry failed login, resume once.
    let f=await fixture(width,origin+'/#shared-craft');
    assert.equal(f.w.location.href,origin+'/'); assert(f.loginOpen());
    assert.equal(f.w.localStorage.getItem('cedartoy_token'),null);
    f.state.failLogin=true; await f.login();
    assert.equal(f.d.getElementById('loginMsg').textContent,'密码错误');
    assert.equal(f.sessions().length,0); assert.deepEqual(f.navigations,[]);
    f.state.failLogin=false; await f.login();
    assert.equal(f.sessions().length,1);
    assert.equal(f.sessions()[0].options.headers.Authorization,'Bearer https-login-secret');
    assert.deepEqual(f.navigations,['/shared-craft/crafts/']); f.close();
    // Already logged in to HTTPS uses only HTTPS credentials, including after HTTP.
    f=await fixture(width,origin+'/#shared-craft','https-existing-secret');
    assert.equal(f.sessions().length,1);
    assert.equal(f.sessions()[0].options.headers.Authorization,'Bearer https-existing-secret');
    assert.deepEqual(f.navigations,['/shared-craft/crafts/']); f.close();
    // Reload/return of the consumed URL cannot replay an intent, with or without login.
    for(const token of ['', 'https-existing-secret']) for(let n=0;n<3;n++) {
      f=await fixture(width,origin+'/',token);
      assert(!f.loginOpen()); assert.equal(f.sessions().length,0);
      assert.deepEqual(f.navigations,[]); f.close();
    }
    // Direct HTTPS entry gets the same one-login continuation.
    f=await fixture(width); await f.enter(); assert(f.loginOpen());
    await f.login(); assert.equal(f.sessions().length,1); f.close();
    // Ordinary login and cancelled craft login must stay home.
    for(const cancel of ['ordinary','closeModals','logout']) {
      f=await fixture(width,origin+(cancel==='ordinary'?'/':'/#shared-craft'));
      if(cancel!=='ordinary') f.w[cancel]();
      f.w.openModal('loginModal'); await f.login();
      assert.equal(f.sessions().length,0); assert.deepEqual(f.navigations,[]); f.close();
    }
    // Expired login is cleared by existing loadMe and then resumes after new login.
    f=await fixture(width,origin+'/#shared-craft','expired');
    assert(f.loginOpen()); await f.login(); assert.equal(f.sessions().length,1); f.close();
    // Session failure doesn't retry by itself; an explicit subsequent click can retry.
    f=await fixture(width,origin+'/','https-existing-secret');
    f.state.sessionStatus=403; await f.enter(); await tick();
    assert.equal(f.sessions().length,1); assert.deepEqual(f.navigations,[]);
    assert.equal(f.d.getElementById('pixelToast').textContent,'手作空间暂时不可用');
    f.state.sessionStatus=200; await f.enter(); assert.equal(f.sessions().length,2); f.close();
    // A token expiring after initial load asks for login once and resumes on success.
    f=await fixture(width,origin+'/','https-existing-secret');
    f.state.sessionStatus=401; await f.enter(); assert(f.loginOpen());
    f.state.sessionStatus=200; await f.login();
    assert.equal(f.sessions().length,2); assert.deepEqual(f.navigations,['/shared-craft/crafts/']); f.close();
    // Repeated clicks do not duplicate a session request; logout cancels late navigation.
    f=await fixture(width,origin+'/','https-existing-secret');
    let resolveSession;
    f.state.delayedSession=new Promise(resolve=>{resolveSession=resolve;});
    await f.enter(); await f.enter(); assert.equal(f.sessions().length,1);
    f.w.logout(); resolveSession({ok:true,status:200}); await tick();
    assert.deepEqual(f.navigations,[]); f.close();
    console.log(`PASS ${width}px DOM/state: protocol, separate storage, login/retry, cancel/logout, reload/return, session failures/races`);
  }
  console.log('Browser navigation, layout and screenshots remain unverified by this DOM check.');
})().catch(error=>{console.error(error);process.exitCode=1;});
