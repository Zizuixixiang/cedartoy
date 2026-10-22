"use strict";
// In-memory stubs only: no server, database, real credentials or external requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
const reply = (data, status = 200) => ({ok: status < 400, status, json: async () => structuredClone(data)});
function fixture() {
  const state = {recovery: 0, feedback: 1, status: 'pending', calls: [], intercept: null};
  const ticket = () => ({id: 1, category: 'bug', status: state.status, preview: '测试反馈', contact: '', internal_note: '', message_count: 1, created_at_epoch: 1700000000, last_message_role: 'player'});
  const conversation = () => ({ticket: ticket(), message_count: 1, next_before_id: null, messages: [{id: 1, role: 'player', content: '测试反馈', created_at_epoch: 1700000000}]});
  state.fetch = async (url, options = {}) => {
    const body = JSON.parse(options.body || '{}');
    state.calls.push({url, body, options});
    if (state.intercept) { const result = state.intercept(url, options); if (result) return result; }
    if (url.startsWith('/api/admin/recovery?')) return reply({pending_count: state.recovery, tickets: [], total: 0});
    if (url === '/api/admin/feedback/list') return reply({total: body.status === 'pending' ? state.feedback : 1, tickets: body.status === 'pending' && !state.feedback ? [] : [ticket()]});
    if (url === '/api/admin/feedback/review') { state.status = body.status || 'processing'; state.feedback = 0; return reply({ok: true}); }
    if (url === '/api/admin/feedback/detail' || url === '/api/feedback/query' || url === '/api/feedback/detail') return reply(conversation());
    if (url === '/api/feedback/submit') return reply({ticket_id: 1, ...(!options.headers?.Authorization ? {query_code: 'x'.repeat(43)} : {})});
    if (url === '/api/feedback/message') { state.status = 'pending'; state.feedback = 1; return reply({ok: true}); }
    if (url === '/api/feedback/list') return reply({total: 1, tickets: [ticket()]});
    if (url.startsWith('/api/admin/users')) return reply({users: [], total: 0, page: 1, page_size: 50});
    return reply({announcements: [], unread_count: 0});
  };
  return state;
}
function page(file, state, token = '') {
  const errors = [], vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(fs.readFileSync(path.join(root, file), 'utf8'), {
    url: 'https://feedback.test/' + (file === 'admin.html' ? 'admin' : ''), runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { if (token) w.localStorage.setItem('cedartoy_token', token); w.fetch = state.fetch; },
  });
  return {w: dom.window, $: id => dom.window.document.getElementById(id), errors};
}
async function checkAdmin() {
  const state = fixture(), {w, $, errors} = page('admin.html', state, 'admin-stub');
  const badge = n => { assert.equal($('recoveryCount').textContent, n > 99 ? '99+' : String(n)); assert.equal($('recoveryCount').hidden, n === 0); };
  const counts = async (r, f) => { state.recovery = r; state.feedback = f; await w.loadRecoveryCount(); badge(r + f); };
  try {
    await tick(); badge(1); // feedback-only must work on initial load, before entering the panel.
    assert.equal($('recoveryTab').firstChild.textContent, '工单');
    assert.equal(w.getComputedStyle($('recoveryCount')).position, 'absolute');
    for (const pair of [[3, 0], [0, 1], [3, 2], [0, 0], [98, 2]]) await counts(...pair);
    await counts(2, 3);
    for (const failed of ['recovery', 'feedback']) {
      const isFailed = url => url.includes('/admin/' + failed);
      for (const failure of ['http', 'network', 'json', 'invalid']) {
        state.intercept = url => {
          if (!isFailed(url)) return null;
          if (failure === 'network') return Promise.reject(new Error('offline'));
          if (failure === 'json') return {ok: true, json: async () => { throw new Error('bad JSON'); }};
          return failure === 'http' ? reply({}, 403) : reply({total: -1, pending_count: 'invalid'});
        };
        if (failed === 'recovery') state.feedback = 5; else state.recovery = 4;
        await w.loadRecoveryCount(); badge(failed === 'recovery' ? 7 : 9);
        assert.equal(w.token(), 'admin-stub');
      }
      state.intercept = null; await counts(2, 5);
    }
    // Queue failure never clears a successful feedback count.
    state.intercept = url => url.includes('/admin/recovery?') ? reply({error: 'failed'}, 500) : null;
    await w.loadRecoveryTickets(); await tick(); badge(7);
    state.intercept = null;
    // Newer refresh wins even when both old responses arrive last.
    const held = [];
    state.intercept = url => /\/admin\/(recovery\?|feedback\/list)/.test(url) ? new Promise(resolve => held.push({url, resolve})) : null;
    const old = w.loadRecoveryCount(); assert.equal(held.length, 2);
    state.intercept = null; await counts(1, 1);
    held.forEach(({url, resolve}) => resolve(reply(url.includes('recovery') ? {pending_count: 88} : {total: 88})));
    await old; badge(2);
    // A pending response from an old identity cannot repopulate the badge.
    for (const notify of [true, false]) {
      const delayed = [];
      state.intercept = () => new Promise(resolve => delayed.push(resolve));
      const pending = w.loadRecoveryCount();
      w.localStorage.setItem('cedartoy_token', 'next-' + notify);
      if (notify) w.dispatchEvent(new w.StorageEvent('storage', {key: 'cedartoy_token'}));
      delayed.forEach(resolve => resolve(reply({pending_count: 77, total: 77})));
      await pending; badge(0);
      state.intercept = null; await counts(0, 1);
    }
    // Stale 401 from the prior login must not log out the new identity.
    let finishUnauthorized;
    state.intercept = () => new Promise(resolve => { finishUnauthorized = resolve; });
    const unauthorized = w.request('/api/admin/recovery?view=pending&page=1').catch(() => {});
    w.localStorage.setItem('cedartoy_token', 'new-admin');
    state.intercept = null; await counts(0, 1);
    finishUnauthorized(reply({}, 401)); await unauthorized; badge(1); assert.equal(w.token(), 'new-admin');
    // Actual UI review handlers refresh counts for reply, processing and closure.
    $('recoveryTab').click(); await tick();
    w.document.querySelector('[data-ticket-kind="feedback"]').click(); await tick();
    for (const action of ['reply', 'processing', 'close']) {
      state.feedback = 1; state.status = 'pending';
      await w.openAdminFeedback(1);
      if (action === 'reply') { $('feedbackAdminReply').value = '已处理'; $('feedbackAdminReview').requestSubmit(); }
      else $(action === 'close' ? 'feedbackAdminClose' : 'feedbackAdminProcessing').click();
      await tick(); badge(0);
    }
    const reviews = () => state.calls.filter(c => c.url === '/api/admin/feedback/review');
    // Closing sends the draft in the same request; empty/whitespace drafts only close.
    for (const draft of ['已修好', '', '  \n ']) {
      state.status = 'pending'; await w.openAdminFeedback(1);
      $('feedbackAdminReply').value = draft;
      const before = reviews().length;
      $('feedbackAdminClose').click(); await tick();
      assert.equal(reviews().length, before + 1);
      assert.deepEqual(reviews().at(-1).body, {ticket_id: 1, confirm: true, status: 'closed', ...(draft.trim() ? {reply: draft} : {})});
      assert.equal($('feedbackAdminClose').disabled, true);
      if (draft.trim()) assert.equal($('feedbackAdminReply').value, '');
    }
    // Failure retains the draft, and the busy guard prevents duplicate submissions.
    state.status = 'pending'; await w.openAdminFeedback(1);
    $('feedbackAdminReply').value = '待发送';
    let finishReview;
    state.intercept = url => url.endsWith('/feedback/review') ? new Promise(resolve => { finishReview = resolve; }) : null;
    const before = reviews().length;
    $('feedbackAdminClose').click(); $('feedbackAdminClose').click();
    assert.equal(reviews().length, before + 1);
    assert.equal($('feedbackAdminReply').disabled, true);
    finishReview(reply({error: '模拟失败'}, 500)); await tick();
    assert.equal($('feedbackAdminReply').value, '待发送');
    assert.equal($('feedbackAdminClose').disabled, false);
    state.intercept = null;
    $('feedbackAdminClose').click(); await tick();
    assert.equal(reviews().length, before + 2);
    assert.deepEqual(reviews().at(-1).body, {ticket_id: 1, confirm: true, status: 'closed', reply: '待发送'});
    assert.equal($('feedbackAdminReply').value, '');
    state.status = 'pending'; await w.openAdminFeedback(1);
    $('feedbackAdminReply').value = '保留草稿';
    for (const id of ['feedbackAdminSaveNote', 'feedbackAdminProcessing']) {
      $(id).click(); await tick();
      assert.equal($('feedbackAdminReply').value, '保留草稿');
      assert.equal('reply' in reviews().at(-1).body, false);
    }
    const beforeReply = reviews().length;
    $('feedbackAdminReview').requestSubmit(); await tick();
    assert.equal(reviews().length, beforeReply + 1);
    assert.deepEqual(reviews().at(-1).body, {ticket_id: 1, confirm: true, reply: '保留草稿'});
    assert.equal(state.status, 'processing');
    assert.equal($('feedbackAdminReply').value, '');
    // Player follow-up reopens pending; refresh sees it even with the closed filter selected.
    await state.fetch('/api/feedback/message');
    $('feedbackAdminFilter').value = 'closed'; $('feedbackAdminRefresh').click(); await tick(); badge(1);
    assert.ok(state.calls.some(c => c.url === '/api/admin/feedback/list' && c.body.status === 'pending' && c.body.page === 1));
    assert.deepEqual(errors, []);
    console.log('Admin stub PASS: count combinations, 99+, partial failures, queue failure, concurrent refresh, identity changes, stale 401, review and follow-up refresh.');
  } finally { w.close(); }
}
async function checkFeedback() {
  const state = fixture(), {w, $, errors} = page('index.html', state);
  const mode = name => w.document.querySelector(`[data-feedback-mode="${name}"]`);
  const close = () => $('feedbackModal').querySelector('[data-close-modal]');
  try {
    await tick(); w.openFeedback();
    assert.equal(mode('list').hidden, true); assert.equal(mode('query').hidden, false);
    assert.equal($('feedbackSubmit').form, $('feedbackForm'));
    assert.equal($('feedbackSubmit').parentElement, close().parentElement);
    assert.equal($('feedbackModal').querySelectorAll('[data-close-modal]').length, 1);
    assert.equal($('feedbackNotifyEmail').checked, false);
    assert.equal($('feedbackContactEmailField').hidden, true);
    $('feedbackSubmit').click(); await tick();
    assert.equal(state.calls.filter(c => c.url === '/api/feedback/submit').length, 0, 'native required validation retained');
    $('feedbackNotifyEmail').click();
    assert.equal($('feedbackContactEmailField').hidden, false);
    assert.equal($('feedbackContactEmail').required, true);
    $('feedbackContent').value = '反馈'; $('feedbackSubmit').click(); await tick();
    assert.equal(state.calls.filter(c => c.url === '/api/feedback/submit').length, 0, 'missing selected email blocks submit');
    $('feedbackContactEmail').value = 'nanshan2368@gmail.com';
    $('feedbackSubmit').click(); await tick();
    const submitted = state.calls.find(c => c.url === '/api/feedback/submit');
    assert.equal(submitted.body.notify_email, true);
    assert.equal(submitted.body.contact_email, 'nanshan2368@gmail.com');
    assert.equal($('feedbackNotifyEmail').checked, false);
    assert.equal($('feedbackContactEmailField').hidden, true);
    assert.equal($('feedbackReceipt').hidden, false); assert.equal($('feedbackSubmit').hidden, true);
    close().click(); w.openFeedback(); assert.equal($('feedbackSubmit').hidden, true);
    $('feedbackReceiptQuery').click(); await tick();
    assert.equal($('feedbackDetail').hidden, false); assert.equal($('feedbackSubmit').hidden, true);
    $('feedbackFollowupContent').value = '继续提问'; $('feedbackFollowupForm').requestSubmit(); await tick();
    assert.match($('feedbackMessage').textContent, /消息已发送/);
    mode('submit').click(); $('feedbackNew').click(); assert.equal($('feedbackSubmit').hidden, false);
    // DOM focus wrapping only; native sequential Tab is checked in the browser companion.
    const originalRects = w.HTMLElement.prototype.getClientRects;
    w.HTMLElement.prototype.getClientRects = function () { return this.closest('[hidden]') ? [] : [{}]; };
    close().focus(); close().dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Tab', bubbles: true}));
    assert.equal(w.document.activeElement, mode('submit'));
    mode('submit').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true}));
    assert.equal(w.document.activeElement, close()); w.HTMLElement.prototype.getClientRects = originalRects;
    w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    assert.equal($('feedbackModal').classList.contains('show'), false);
    w.localStorage.setItem('cedartoy_token', 'human-stub'); w.openFeedback();
    assert.equal(mode('list').hidden, false); assert.equal(mode('query').hidden, true);
    mode('list').click(); await tick(); assert.equal($('feedbackSubmit').hidden, true); assert.equal(close().hidden, false);
    mode('submit').click(); $('feedbackContent').value = '登录反馈'; $('feedbackSubmit').click(); await tick();
    assert.match($('feedbackMessage').textContent, /我的反馈/); assert.equal($('feedbackSubmit').hidden, false);
    assert.deepEqual(errors, []);
    console.log('Feedback stub PASS: external form association, required validation, anonymous receipt, login modes, query/follow-up, close/Esc and focus wrap. Layout NOT measured by jsdom.');
  } finally { w.close(); }
}
module.exports = {fixture};
if (require.main === module) (async () => { await checkFeedback(); await checkAdmin(); })().catch(e => {console.error(e); process.exitCode = 1;});
