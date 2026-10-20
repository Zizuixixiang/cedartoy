/* Behaviour only: this does not replace the browser visual comparison. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {bridge} = require('./shared_craft_test_bridge');

(async()=>{
  const {JSDOM} = await import('jsdom');
  const api = bridge();
  try {
    await api.ready;
    const html=Buffer.from((await api.request('crafts/')).body,'base64').toString();
    const js=Buffer.from((await api.request('assets/crafts.js')).body,'base64').toString();
    let sharePosts=0;
    const dom=new JSDOM(html,{url:'https://craft.test/shared-craft/crafts/',runScripts:'outside-only',pretendToBeVisual:true});
    const w=dom.window, doc=w.document;
    // Native dialog rendering/focus is covered by the separate browser test.
    w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
    w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
    w.fetch=async(url,options={})=>{
      const name=new URL(url,w.location).pathname.replace('/shared-craft/','');
      if(name.startsWith('api/shares')&&options.method==='POST') sharePosts++;
      const r=await api.request(name,options.method,options.body?JSON.parse(options.body):undefined);
      return {ok:r.status<400,json:async()=>JSON.parse(Buffer.from(r.body,'base64'))};
    };
    // jsdom has no layout or image decoder; only skip the wall's preload wait.
    w.eval(js.replace('await prepareWall();','drawWall();'));
    const until=async predicate=>{for(let i=0;i<150;i++){if(predicate())return;await new Promise(r=>setTimeout(r,20));}throw Error('state timeout');};
    await until(()=>doc.querySelector('#status').textContent.includes('1 件'));
    assert.equal(sharePosts,0);
    doc.querySelector('#open-all').click();
    assert.equal(doc.querySelectorAll('[data-share]').length,1);
    doc.querySelector('[data-share]').click();
    await until(()=>doc.querySelector('#share-dialog').open);
    assert.equal(doc.querySelector('#share-note').value,'谢谢你做了这间手作房');
    assert(doc.querySelector('#share-note').readOnly);
    doc.querySelector('#share-cancel').click();
    assert.equal(sharePosts,0);
    assert.equal(JSON.parse(Buffer.from((await api.request('author','GET',undefined,6)).body,'base64')).total,0);
    doc.querySelector('[data-share]').click();
    await until(()=>doc.querySelector('#share-dialog').open);
    doc.querySelector('#share-form').dispatchEvent(new w.Event('submit',{cancelable:true}));
    await until(()=>doc.querySelector('#status').textContent==='已分享给作者');
    assert.equal(sharePosts,1);
    assert.equal(JSON.parse(Buffer.from((await api.request('author','GET',undefined,6)).body,'base64')).total,1);
    doc.querySelector('[data-share]').click();
    await until(()=>doc.querySelector('#share-dialog').open);
    assert.equal(doc.querySelector('#share-note').value,'');
    assert(!doc.querySelector('#share-note').readOnly);
    dom.window.close();
    const index=fs.readFileSync('index.html','utf8');
    for(const match of index.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
    assert(index.includes('id: "shared_craft"'));
    assert(index.includes('id="craftAuthorModal"'));
    assert(!index.includes('公开作品大厅'));
    console.log('PASS: private by default, cancel, fixed pending snapshot/note, explicit confirm, author read, homepage syntax');
  } finally { await api.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
