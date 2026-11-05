'use strict';
// Offline DOM/interaction regression. This does not measure browser layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const {fixture} = require('./check_feedback_actions_ui');
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => structuredClone(data)});

(async () => {
  const state = fixture(), errors = [], vc = new VirtualConsole();
  vc.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(fs.readFileSync(process.env.FEEDBACK_ADMIN_HTML || path.join(__dirname, '../admin.html'), 'utf8'), {
    url: 'http://feedback.test/admin', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.localStorage.setItem('cedartoy_token', 'admin-fixture'); w.fetch = state.fetch; },
  });
  const w = dom.window, d = w.document, $ = id => d.getElementById(id);
  const idle = async () => {
    for (let i = 0; i < 100; i++) { await tick(); if (!w.eval('feedbackAdminBusy')) return; }
    throw new Error('request stuck');
  };
  const initial = () => ({ticket: {id: 1, category: 'bug', status: 'processing', internal_note: '', created_at_epoch: 1700000000},
    message_count: 4, next_before_id: null, messages: [
      {id: 1, role: 'player', content: '玩家原始反馈', created_at_epoch: 1700000000},
      {id: 2, role: 'player', content: '玩家追问', created_at_epoch: 1700000001},
      {id: 3, role: 'admin', content: '别人的回复', version: 1, can_manage: false, created_at_epoch: 1700000002},
      {id: 4, role: 'admin', content: '自己的回复', version: 1, can_manage: true, created_at_epoch: 1700000003},
    ]});
  let conversation = initial();
  const own = () => [...d.querySelectorAll('#feedbackAdminMessages .feedback-message')].at(-1);
  const action = (host, name) => [...host.querySelectorAll('button')].find(el => el.textContent === name);
  state.intercept = (url, options) => {
    const body = JSON.parse(options.body || '{}');
    if (url === '/api/admin/feedback/detail' && body.message_id) return response({history: [
      {version: 1, action: 'original', content: '<img src=x onerror=alert(1)> 原文', admin_label: '管理员Clio', created_at_epoch: 1700000003},
      {version: 2, action: 'edit', content: '更正后', admin_label: '管理员Clio', created_at_epoch: 1700000010},
    ], next_before_version: null});
    if (url === '/api/admin/feedback/edit' || url === '/api/admin/feedback/withdraw') {
      const message = conversation.messages.at(-1), withdrawn = url.endsWith('/withdraw');
      assert.equal(body.message_id, 4); assert.equal(body.expected_version, message.version); assert.equal(body.confirm, true);
      const next = {...message, content: withdrawn ? '此回复已撤回' : body.content, version: message.version + 1,
        can_manage: !withdrawn, ...(withdrawn ? {withdrawn: true} : {edited: true})};
      return response({ok: true, message: next});
    }
    return null;
  };
  try {
    await tick(); w.renderAdminFeedbackDetail(conversation);
    assert.equal(d.querySelectorAll('.feedback-message-actions button').length, 2);
    assert.equal(d.querySelectorAll('[data-role="player"] button').length, 0);
    $('feedbackAdminReply').value = '追加回复草稿'; $('feedbackAdminNote').value = '备注草稿';
    action(own(), '编辑').click();
    let input = own().querySelector('textarea'); assert.equal(d.activeElement, input); assert.equal(input.value, '自己的回复');
    input.value = '取消的修改'; input.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    assert.equal(own().querySelector('textarea'), null); assert.equal(d.activeElement.textContent, '编辑');
    action(own(), '编辑').click(); input = own().querySelector('textarea');
    input.value = '   '; own().querySelector('form').dispatchEvent(new w.Event('submit', {cancelable: true}));
    assert.equal(input.checkValidity(), false);
    input.value = '更正后'; input.dispatchEvent(new w.Event('input'));
    own().querySelector('form').requestSubmit(); await idle();
    assert.match(own().textContent, /已编辑/); assert.equal(own().querySelector('p').textContent, '更正后');
    assert.equal($('feedbackAdminReply').value, '追加回复草稿'); assert.equal($('feedbackAdminNote').value, '备注草稿');
    assert.equal(d.activeElement.textContent, '编辑');
    assert.equal(state.calls.filter(c => c.body.message_id && c.url.endsWith('/detail')).length, 0);
    own().querySelector('.feedback-audit').open = true; await idle();
    assert.match(own().textContent, /<img src=x onerror=alert\(1\)> 原文/);
    assert.equal(own().querySelectorAll('img,script').length, 0);
    let confirmText;
    w.confirm = text => { confirmText = text; return false; };
    action(own(), '撤回').click(); await idle();
    assert.equal(state.calls.filter(c => c.url.endsWith('/withdraw')).length, 0);
    w.confirm = text => { confirmText = text; return true; };
    action(own(), '撤回').click(); await idle();
    assert.match(confirmText, /已发邮件/); assert.ok(confirmText.length < 40);
    assert.match(own().textContent, /已撤回/); assert.equal(own().querySelector('.feedback-message-actions'), null);
    assert.equal(d.activeElement, own());

    conversation = initial(); w.renderAdminFeedbackDetail(conversation);
    const normal = state.intercept;
    let finish;
    state.intercept = (url, opts) => url.endsWith('/edit') ? new Promise(resolve => { finish = resolve; }) : normal(url, opts);
    action(own(), '编辑').click(); input = own().querySelector('textarea'); input.value = '网络失败草稿';
    const before = state.calls.filter(c => c.url.endsWith('/edit')).length;
    own().querySelector('form').requestSubmit(); own().querySelector('form').requestSubmit();
    assert.equal(state.calls.filter(c => c.url.endsWith('/edit')).length, before + 1);
    assert.equal(input.disabled, true);
    finish(response({error: '消息已变更，请刷新后重试'}, 409)); await idle();
    assert.equal(input.value, '网络失败草稿'); assert.equal(input.disabled, false);
    assert.match($('feedbackAdminStatus').textContent, /刷新后重试/);
    own().querySelector('form').requestSubmit();
    w.localStorage.setItem('cedartoy_token', 'next-admin');
    finish(response({ok: true, message: {...conversation.messages.at(-1), content: '不能展示的旧身份响应'}})); await idle();
    assert.equal($('feedbackAdminDetail').hidden, true);
    assert.doesNotMatch($('feedbackAdminDetail').textContent, /不能展示/);
    assert.deepEqual(errors, []);
    console.log('Admin message DOM PASS: own-only controls, cancel/Escape/focus, validation, edit, lazy audit/XSS, withdraw, drafts, busy guard, conflict and identity change. Browser layout NOT verified.');
  } finally { w.close(); }

  // The player renderer receives only state markers, never mutation controls.
  const publicDom = new JSDOM(fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8'), {
    url: 'http://feedback.test', runScripts: 'dangerously', virtualConsole: vc,
    beforeParse(w) { w.fetch = fixture().fetch; w.matchMedia = () => ({matches: false, addEventListener() {}}); },
  });
  try {
    await tick();
    const publicWindow = publicDom.window;
    for (const flag of ['edited', 'withdrawn']) {
      const element = publicWindow.feedbackMessageElement({role: 'admin', content: '当前公开正文', created_at_epoch: 1700000000, [flag]: true});
      assert.match(element.textContent, new RegExp(flag === 'edited' ? '已编辑' : '已撤回'));
      assert.equal(element.querySelectorAll('button,textarea,details').length, 0);
    }
    console.log('Player DOM PASS: edited/withdrawn markers with no editing, withdrawal or audit controls.');
  } finally { publicDom.window.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
