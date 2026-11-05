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
function page(file, state, token = '', hash = '') {
  const errors = [], vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(fs.readFileSync(path.join(root, file), 'utf8'), {
    url: 'https://feedback.test/' + (file === 'admin.html' ? 'admin' : '') + hash, runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      if (token) w.localStorage.setItem('cedartoy_token', token);
      w.fetch = state.fetch;
      w.matchMedia = query => ({media: query, matches: false, addEventListener() {}, removeEventListener() {}});
    },
  });
  return {w: dom.window, $: id => dom.window.document.getElementById(id), errors};
}
function checkActorNames(w, admin) {
  const names = ['First_machine_甲', 'Second_machine_乙', '<img src=x onerror="window.feedbackXss=1">&"', 'M'.repeat(120)];
  const messages = names.map((actor_name, i) => ({id: i + 1, role: 'player', actor_kind: 'ai', actor_name,
    content: '机器留言正文', created_at_epoch: 1700000000}));
  messages.push(
    {role: 'player', actor_kind: 'ai', content: '账号不存在'},
    {role: 'player', actor_kind: 'human', actor_name: 'ignored', content: '人类正文'},
    {role: 'admin', actor_kind: 'ai', actor_name: 'ignored', admin_label: '管理员Clio', content: '管理员正文'},
    {role: 'player', actor_kind: 'anonymous', content: '匿名正文'});
  messages.push({...messages[0], id: 9, content: '最新小机回复'});
  const data = {ticket: {id: 1, category: 'bug', status: 'pending', internal_note: '', created_at_epoch: 1700000000},
    messages, message_count: messages.length, next_before_id: null};
  if (admin) w.renderAdminFeedbackDetail(data); else w.renderFeedbackDetail(data);
  const host = w.document.getElementById(admin ? 'feedbackAdminMessages' : 'feedbackMessages');
  host.querySelector('details').open = true;
  const labels = [...host.querySelectorAll('.feedback-message-meta')].map(el => el.textContent.split(' · ')[0]);
  assert.deepEqual(labels, [...names, '小机留言', admin ? '玩家提问' : '人类留言', '管理员Clio', admin ? '玩家提问' : '匿名留言', names[0]]);
  assert.equal(host.querySelectorAll('img,script').length, 0);
  assert.equal(w.feedbackXss, undefined);
  assert.equal(host.querySelectorAll('.feedback-history .feedback-message').length, messages.length - 3);
  console.log(`${admin ? 'Admin' : 'Frontend'} DOM PASS: distinct AI names in history/recent messages, missing-account fallback, text-only XSS, unchanged human/admin labels.`);
}
async function checkAdmin() {
  const state = fixture(), {w, $, errors} = page('admin.html', state, 'admin-stub');
  const badge = n => {
    const values = w.eval('[pendingTicketCounts.recovery, pendingTicketCounts.feedback]');
    for (const [id, value] of [['recoveryCount', n], ['recoveryKindCount', values[0]], ['feedbackKindCount', values[1]]]) {
      assert.equal($(id).textContent, value > 99 ? '99+' : String(value));
      assert.equal($(id).hidden, value === 0);
      assert.equal($(id).getAttribute('aria-hidden'), 'true');
      assert.equal($(id).parentElement.getAttribute('aria-label').includes(`待处理 ${value} 条`), value > 0);
    }
    assert.equal(values[0] + values[1], n);
  };
  const counts = async (r, f) => { state.recovery = r; state.feedback = f; await w.loadRecoveryCount(); badge(r + f); };
  try {
    checkActorNames(w, true);
    await tick(); badge(1); // feedback-only must work on initial load, before entering the panel.
    assert.equal($('recoveryTab').firstChild.textContent, '工单');
    // The current admin tab uses an inline flex badge, not the old overlay.
    assert.equal(w.getComputedStyle($('recoveryCount')).position, 'static');
    assert.equal(w.getComputedStyle($('recoveryCount')).flex, '0 0 auto');
    for (const pair of [[3, 0], [0, 1], [3, 2], [0, 0], [98, 2], [100, 101]]) await counts(...pair);
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
    // Recovery review must refresh after the write, superseding any pre-review counts.
    let recoveryOpen = true;
    state.intercept = url => url.includes('/admin/recovery?') ? reply({pending_count: recoveryOpen ? 1 : 0,
      total: recoveryOpen ? 1 : 0, tickets: recoveryOpen ? [{id: 7, status: 'pending'}] : []}) : null;
    await w.loadRecoveryTickets();
    const preReview = [];
    state.intercept = (url, options) => options.cache === 'no-store'
      ? new Promise(resolve => preReview.push(resolve)) : null;
    const oldReviewCounts = w.loadRecoveryCount();
    state.intercept = url => {
      if (url.endsWith('/recovery/review')) { recoveryOpen = false; return reply({ok: true}); }
      if (url.includes('/admin/recovery?')) return reply({pending_count: 0, total: 0, tickets: []});
      return null;
    };
    $('recoveryTickets').querySelector('[data-recovery-action="approved"]').click(); await tick(); badge(1);
    assert.equal(recoveryOpen, false); assert.match($('recoveryTickets').textContent, /暂无工单/);
    preReview.forEach(resolve => resolve(reply({pending_count: 99, total: 99})));
    await oldReviewCounts; badge(1); state.intercept = null;
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
async function checkTicketNavigation() {
  const state = fixture(), {w, $, errors} = page('admin.html', state, 'admin-navigation');
  const kindButton = kind => w.document.querySelector(`[data-ticket-kind="${kind}"]`);
  const selected = kind => {
    assert.equal(w.eval('ticketKind'), kind);
    for (const name of ['recovery', 'feedback']) {
      assert.equal($(name === 'recovery' ? 'recoveryQueue' : 'feedbackQueue').hidden, name !== kind);
      assert.equal(kindButton(name).getAttribute('aria-pressed'), String(name === kind));
    }
    assert.equal($('ticketEntryStatus').textContent, '');
  };
  const holdCounts = () => {
    const held = [];
    state.intercept = (url, options) => options.cache === 'no-store'
      ? new Promise(resolve => held.push({url, resolve})) : null;
    return (r, f) => {
      assert.equal(held.length, 2);
      state.intercept = null;
      held.forEach(({url, resolve}) => resolve(reply(url.includes('recovery')
        ? {pending_count: r, tickets: [], total: r} : {total: f, tickets: []})));
    };
  };
  try {
    await tick();
    // Entry uses fresh counts, including a change since the top badge last loaded.
    for (const [r, f, expected] of [[0, 0, 'recovery'], [0, 4, 'feedback'], [2, 0, 'recovery'], [2, 4, 'recovery']]) {
      $('usersTab').click(); state.recovery = r; state.feedback = f;
      const before = state.calls.length;
      $('recoveryTab').click(); await tick(); selected(expected);
      const calls = state.calls.slice(before);
      assert.equal(calls.filter(c => c.url.includes('/admin/recovery?')).length, 1);
      assert.equal(calls.filter(c => c.url.endsWith('/feedback/list') && c.body.status === 'pending').length, 1);
      assert.equal(calls.length, expected === 'feedback' ? 3 : 2, 'one count batch plus only a distinct list query');
    }
    kindButton('feedback').click(); await tick(); selected('feedback');
    // Both-zero restores the last manual choice even after an automatic recovery entry.
    $('usersTab').click(); $('recoveryTab').click(); await tick(); selected('recovery');
    $('usersTab').click(); state.recovery = state.feedback = 0;
    $('recoveryTab').click(); await tick(); selected('feedback');
    const before = state.calls.length;
    $('recoveryTab').click(); await tick(); selected('feedback');
    assert.equal(state.calls.length, before, 'clicking the active top tab is not another entry');
    state.recovery = 8; await w.loadRecoveryCount(); selected('feedback');

    // A manual choice during delayed entry cancels auto-selection and shares counts.
    $('usersTab').click(); let release = holdCounts();
    $('recoveryTab').click();
    assert.ok($('recoveryQueue').hidden && $('feedbackQueue').hidden);
    assert.match($('ticketEntryStatus').textContent, /加载/);
    kindButton('feedback').click(); await tick(); selected('feedback');
    release(9, 0); await tick(); selected('feedback');
    assert.equal($('recoveryKindCount').textContent, '9');
    $('usersTab').click(); release = holdCounts();
    const manualStart = state.calls.length;
    $('recoveryTab').click(); kindButton('recovery').click();
    release(0, 9); await tick(); selected('recovery');
    assert.equal(state.calls.length - manualStart, 2, 'manual pending queue reuses the in-flight count response');

    // A pending feedback filter can also reuse its count response; other filters stay intact.
    $('feedbackAdminFilter').value = 'pending';
    $('usersTab').click(); state.recovery = 0; state.feedback = 1;
    const pendingStart = state.calls.length;
    $('recoveryTab').click(); await tick(); selected('feedback');
    assert.equal(state.calls.length - pendingStart, 2);
    $('feedbackAdminFilter').value = 'all';

    // Leaving/re-entering shares an in-flight count batch; only the new entry loads.
    $('usersTab').click(); release = holdCounts();
    const callStart = state.calls.length;
    $('recoveryTab').click(); $('usersTab').click(); $('recoveryTab').click();
    release(0, 3); await tick(); selected('feedback');
    assert.equal(state.calls.length - callStart, 3);
    $('usersTab').click(); release = holdCounts(); $('recoveryTab').click(); $('usersTab').click();
    const leavingCalls = state.calls.length; release(0, 2); await tick();
    assert.equal($('recoveryPanel').hidden, true);
    assert.equal(state.calls.length, leavingCalls, 'leaving prevents a late queue load');

    // Explicit refresh supersedes an entry request, even when the old one finishes first.
    release = holdCounts(); $('recoveryTab').click();
    const releaseNew = holdCounts(); const newer = w.loadRecoveryCount();
    release(8, 0); await tick();
    assert.ok($('recoveryQueue').hidden && $('feedbackQueue').hidden);
    releaseNew(0, 6); await newer; await tick(); selected('feedback');

    // Login changes cancel entry, clear every badge and discard the prior manual choice.
    for (const notify of [true, false]) {
      $('usersTab').click(); release = holdCounts(); $('recoveryTab').click();
      w.localStorage.setItem('cedartoy_token', 'changed-' + notify);
      if (notify) w.dispatchEvent(new w.StorageEvent('storage', {key: 'cedartoy_token'}));
      release(77, 88); await tick();
      for (const id of ['recoveryCount', 'recoveryKindCount', 'feedbackKindCount']) assert.equal($(id).hidden, true);
      assert.ok($('recoveryQueue').hidden && $('feedbackQueue').hidden);
      state.recovery = state.feedback = 0;
      $('usersTab').click(); $('recoveryTab').click(); await tick(); selected('recovery');
    }
    const releaseOldLogin = holdCounts(); const oldLogin = w.loadRecoveryCount();
    w.localStorage.setItem('cedartoy_token', 'latest-login');
    w.dispatchEvent(new w.StorageEvent('storage', {key: 'cedartoy_token'}));
    state.intercept = null; state.feedback = 3; await w.loadRecoveryCount();
    releaseOldLogin(99, 99); await oldLogin;
    assert.equal($('recoveryCount').textContent, '3', 'old login cannot overwrite a new login count');
    // Initial failures have no fabricated counts; later failures retain successful values.
    state.recovery = 0; state.feedback = 2; await w.loadRecoveryCount();
    state.intercept = (url, options) => options.cache === 'no-store' ? reply({}, 503) : null;
    $('usersTab').click(); $('recoveryTab').click(); await tick(); selected('feedback');
    assert.equal($('feedbackKindCount').textContent, '2');
    state.intercept = null;
    w.localStorage.removeItem('cedartoy_token');
    w.dispatchEvent(new w.StorageEvent('storage', {key: null}));
    assert.equal($('recoveryCount').hidden, true);
    assert.deepEqual(errors, []);
  } finally { w.close(); }
  // Direct #recovery page entry waits for startup counts and reuses the pending list.
  for (const fail of [false, true]) {
    const state = fixture();
    if (fail) state.intercept = (url, options) => options.cache === 'no-store' ? reply({}, 500) : null;
    const {w, $, errors} = page('admin.html', state, 'admin-direct', '#recovery');
    try {
      await tick(); assert.equal(w.eval('ticketKind'), fail ? 'recovery' : 'feedback');
      assert.equal($('recoveryPanel').hidden, false);
      assert.equal($('recoveryCount').hidden, fail);
      assert.equal(state.calls.length, 3);
      assert.deepEqual(errors, []);
    } finally { w.close(); }
  }
  console.log('Admin navigation PASS: four combinations, stable priority, manual memory, entry-only selection, deduplicated requests, delayed/stale refresh, login changes, failures and direct page entry.');
}
async function checkFeedback() {
  const state = fixture(), {w, $, errors} = page('index.html', state);
  const mode = name => w.document.querySelector(`[data-feedback-mode="${name}"]`);
  const close = () => $('feedbackModal').querySelector('[data-close-modal]');
  try {
    checkActorNames(w, false);
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
if (require.main === module) (async () => {
  if (!process.env.FEEDBACK_ADMIN_ONLY) await checkFeedback();
  await checkAdmin(); await checkTicketNavigation();
})().catch(e => {console.error(e); process.exitCode = 1;});
