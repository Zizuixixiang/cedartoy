// UUID compatibility and fail-closed UI checks; no production data or network.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {webcrypto} = require('node:crypto');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '../deeptalk.html'), 'utf8');
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
function page(cryptoApi) {
  return new JSDOM(html, {url:'http://deeptalk.test/deeptalk', runScripts:'dangerously',
    beforeParse(w) {
      Object.defineProperty(w, 'crypto', {value:cryptoApi, configurable:true});
      w.Math.random = () => { throw new Error('Weak randomness must not be used'); };
    }
  }).window;
}
(async () => {
  const expected = '01234567-89ab-4cde-8f01-23456789abcd';
  const native = {randomUUID() { assert.equal(this,native); return expected; },
    getRandomValues() { throw new Error('Native UUID should be preferred'); }};
  let w = page(native);
  assert.equal(w.eval('key()'),expected); w.close();
  for (const nativeValue of [undefined, null, 'unavailable', () => { throw new Error('WebView failure'); }]) {
    const api = {randomUUID:nativeValue, getRandomValues(bytes) {
      assert.equal(this,api); return webcrypto.getRandomValues(bytes);
    }};
    w = page(api);
    const values = new Set();
    for(let i=0;i<10000;i++) { const value=w.eval('key()'); assert.match(value,uuid); values.add(value); }
    assert.equal(values.size,10000,'independent calls must not reuse IDs');
    assert.equal(api.randomUUID,nativeValue,'do not mutate the global crypto API');
    w.close();
  }
  for (const fill of [0,255]) {
    w=page({getRandomValues(bytes) { bytes.fill(fill); return bytes; }});
    assert.equal(w.eval('key()'), fill ? 'ffffffff-ffff-4fff-bfff-ffffffffffff' : '00000000-0000-4000-8000-000000000000');
    w.close();
  }
  for (const api of [undefined, {}, {getRandomValues(){throw new Error('unavailable');}}]) {
    w=page(api); await new Promise(resolve=>setImmediate(resolve));
    assert.equal(w.document.getElementById('message').classList.contains('error'),false,'initial page does not generate IDs');
    let writes=0; w.fetch=async()=>{writes++; throw new Error('unexpected request');};
    w.eval("context={machines:[{}]};topic={topic_id:'test',available:true};");
    await w.document.getElementById('create').onclick();
    assert.equal(writes,0); assert.equal(w.sessionStorage.getItem('deeptalk_pending_create'),null);
    assert.match(w.document.getElementById('message').textContent,/尚未保存/);
    w.eval("state={mode:'chat',submitted:false,total:1,revision:0,progress:{human:{done:0}},questions:[{id:'q1'}]}; roundId='test'; dirty=true;");
    w.document.getElementById('answer').value='保留这份输入';
    await w.document.getElementById('next').onclick();
    assert.equal(writes,0); assert.equal(w.document.getElementById('answer').value,'保留这份输入');
    assert.equal(w.eval('dirty'),true); assert.equal(w.document.getElementById('next').disabled,false);
    assert.match(w.document.getElementById('message').textContent,/尚未保存/);
    w.close();
  }
  console.log('PASS: native/fallback/throwing UUID, v4 bits, 40,000 unique IDs, no weak randomness, fail-closed writes and retained input.');
})().catch(error=>{console.error(error);process.exitCode=1;});
