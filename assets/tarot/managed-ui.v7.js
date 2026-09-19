(() => {
  'use strict';

  const managedIdPattern = /^[A-Za-z0-9_-]{1,128}$/;
  const invitationActionTimeoutMs = 12000;
  const invitationToastLifetimeMs = 1400;
  let scheduled = false;
  let newSessionPending = false;
  let newSessionFeedback = false;
  let nextActionId = '';
  const readingRequestUi = {
    requestId: 0,
    active: false,
    sawText: false,
  };
  const invitationState = {
    invitations: [],
    cursor: '',
    activeId: '',
    processingId: '',
    autoSeen: new Set(),
    controller: null,
    retryTimer: null,
    generation: 0,
    stopped: false,
    notice: '',
    noticeError: false,
  };

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function setClass(node, name, enabled) {
    if (node && node.classList.contains(name) !== enabled) {
      node.classList.toggle(name, enabled);
    }
  }

  function isMobile() {
    const width = Number(globalThis.innerWidth);
    return Number.isFinite(width) && width <= 600;
  }

  function sessionConfig() {
    const node = document.getElementById('companion-config');
    const config = JSON.parse(node?.textContent || 'null');
    if (
      config?.protocol !== 'cove-tarot-companion-v1'
      || config.apiBase !== '/companion/v1'
      || typeof config.sessionId !== 'string'
      || !managedIdPattern.test(config.sessionId)
    ) throw new Error('会话配置无效，请刷新后重试。');
    return config;
  }

  function companionBox() {
    const status = document.getElementById('companionStatus');
    return status?.closest('[data-managed-companion="1"]')
      || status?.closest('.panel')
      || null;
  }

  function readingFeedback() {
    let feedback = document.getElementById('managedReadingFeedback');
    if (feedback) return feedback;
    const question = document.getElementById('readingQuestion');
    if (!question) return null;
    feedback = document.createElement('p');
    feedback.id = 'managedReadingFeedback';
    feedback.className = 'managed-reading-feedback';
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    feedback.hidden = true;
    question.insertAdjacentElement('afterend', feedback);
    return feedback;
  }

  function showFeedback(message, { error = false, owner = 'companion' } = {}) {
    const feedback = readingFeedback();
    if (!feedback) return;
    setText(feedback, message);
    if (feedback.hidden !== !message) feedback.hidden = !message;
    setClass(feedback, 'error', Boolean(error));
    if (owner === 'new-session') newSessionFeedback = Boolean(message);
  }

  function isRoutineReadingStatus(message) {
    return [
      /^已恢复原解读。$/,
      /^本次会话已结束，已有结果仅供查看。$/,
      /^牌面已保存；尚无完整原解读。$/,
      /^本次已结束，记录已保留。$/,
      /^本次已停止。已有记录保留/,
      /^会话已就绪；抽牌将保存到本次记录。$/,
    ].some(pattern => pattern.test(message));
  }

  function syncCompanionFeedback() {
    if (newSessionFeedback) return;
    const status = document.getElementById('companionStatus');
    const message = status?.textContent?.trim() || '';
    if (!message || isRoutineReadingStatus(message)) {
      showFeedback('');
      return;
    }
    const error = /失败|无法|未确认|中断|缺少|不可用|错误/.test(message);
    showFeedback(message, { error });
  }

  function syncReadingCompanion() {
    const panel = document.getElementById('readingPanel');
    const box = companionBox();
    const status = document.getElementById('companionStatus');
    if (!panel || !box || !status) return;
    const compact = isMobile() && panel.classList.contains('open');
    setClass(box, 'managed-companion-reading-compact', compact);
    if (compact) {
      if (status.getAttribute('aria-hidden') !== 'true') {
        status.setAttribute('aria-hidden', 'true');
      }
      readingFeedback();
      syncCompanionFeedback();
    } else {
      if (status.hasAttribute('aria-hidden')) status.removeAttribute('aria-hidden');
      if (!newSessionFeedback) {
        const feedback = readingFeedback();
        if (feedback && !feedback.hidden) feedback.hidden = true;
      }
    }
  }

  function newReadButton() {
    return document.getElementById('newReadBtn');
  }

  function readingIsStreaming() {
    return document.getElementById('readingStream')?.classList.contains('streaming') || false;
  }

  function readingStateNode(stream = document.getElementById('readingStream')) {
    return stream?.querySelector?.('[data-managed-reading-state="1"]') || null;
  }

  function readingHasRealContent(stream) {
    if (!stream) return false;
    for (const child of stream.childNodes) {
      if (child.nodeType === 1 && child.matches?.('[data-managed-reading-state="1"]')) {
        continue;
      }
      if ((child.textContent || '').trim()) return true;
      if (child.nodeType === 1 && child.querySelector?.('img,video,audio,canvas,svg')) {
        return true;
      }
    }
    return false;
  }

  function clearReadingState(stream) {
    readingStateNode(stream)?.remove();
  }

  function showReadingState(stream, message, { busy = false } = {}) {
    if (!stream) return;
    clearReadingState(stream);
    const state = document.createElement('div');
    state.className = 'managed-reading-state' + (busy ? ' busy' : ' empty');
    state.dataset.managedReadingState = '1';
    state.setAttribute('role', 'status');
    state.setAttribute('aria-live', 'polite');
    const label = document.createElement('span');
    label.textContent = message;
    state.appendChild(label);
    if (busy) {
      const dots = document.createElement('span');
      dots.className = 'managed-reading-dots';
      dots.setAttribute('aria-hidden', 'true');
      for (let index = 0; index < 3; index += 1) dots.appendChild(document.createElement('i'));
      state.appendChild(dots);
    }
    stream.appendChild(state);
  }

  function handleReadingState(event) {
    const detail = event?.detail;
    const requestId = Number(detail?.requestId);
    const state = detail?.state;
    if (!Number.isSafeInteger(requestId) || requestId <= 0) return;
    if (!['start', 'delta', 'done', 'error', 'cancelled'].includes(state)) return;
    const stream = document.getElementById('readingStream');
    if (!stream) return;

    if (state === 'start') {
      if (requestId < readingRequestUi.requestId) return;
      readingRequestUi.requestId = requestId;
      readingRequestUi.active = true;
      readingRequestUi.sawText = false;
      clearReadingState(stream);
      stream.setAttribute('aria-busy', 'true');
      if (!readingHasRealContent(stream)) {
        showReadingState(stream, '正在解读牌面，请稍候…', { busy: true });
      }
      return;
    }

    if (requestId !== readingRequestUi.requestId) return;
    if (state === 'delta') {
      if (detail.hasText || readingHasRealContent(stream)) {
        readingRequestUi.sawText = true;
        clearReadingState(stream);
      } else if (readingRequestUi.active) {
        showReadingState(stream, '正在解读牌面，请稍候…', { busy: true });
      }
      return;
    }

    readingRequestUi.active = false;
    stream.classList.remove('streaming');
    stream.setAttribute('aria-busy', 'false');
    clearReadingState(stream);
    const hasContent = readingRequestUi.sawText || readingHasRealContent(stream);
    if (state === 'error' || hasContent) return;
    if (!document.getElementById('readingPanel')?.classList.contains('open')) return;
    if (state === 'cancelled') {
      showReadingState(stream, '本次解读已取消。');
    } else if (detail.ok === false) {
      showReadingState(stream, '解读未能完成，可稍后手动再问一次。');
    } else {
      showReadingState(stream, '本次未收到解读内容，可稍后手动再问一次。');
    }
  }

  function syncNewReadControl() {
    const button = newReadButton();
    if (!button) return;
    const busy = newSessionPending || readingIsStreaming();
    if (button.disabled !== busy) button.disabled = busy;
    if (button.getAttribute('aria-disabled') !== String(busy)) {
      button.setAttribute('aria-disabled', String(busy));
    }
    if (newSessionPending) {
      setText(button, '开 始 中…');
      if (button.title !== '正在创建新的独立占问') {
        button.title = '正在创建新的独立占问';
      }
    } else {
      if (readingIsStreaming()) {
        setText(button, '解 读 中…');
        if (button.title !== '解读正在进行，请等待完成或先明确结束本次') {
          button.title = '解读正在进行，请等待完成或先明确结束本次';
        }
      } else {
        setText(button, '新 的 占 问');
        button.removeAttribute('title');
      }
    }
  }

  function stableActionId(sessionId) {
    const key = `cove-tarot-companion-v1.new-session.${sessionId}`;
    if (managedIdPattern.test(nextActionId)) return nextActionId;
    let actionId = nextActionId;
    try { actionId = localStorage.getItem(key) || ''; } catch { /* Keep an in-memory id. */ }
    if (managedIdPattern.test(actionId)) {
      nextActionId = actionId;
      return actionId;
    }
    actionId = crypto.randomUUID();
    if (!managedIdPattern.test(actionId)) throw new Error('无法生成安全的新占问编号。');
    nextActionId = actionId;
    try { localStorage.setItem(key, actionId); } catch { /* The current click remains safe. */ }
    return actionId;
  }

  async function responseJson(response, fallback) {
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.error || fallback);
    return payload;
  }

  async function startNewSession(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (newSessionPending) return;
    if (readingIsStreaming()) {
      newSessionFeedback = true;
      showFeedback('解读正在进行，请等待完成或先明确结束本次。', {
        error: true,
        owner: 'new-session',
      });
      syncNewReadControl();
      return;
    }

    newSessionPending = true;
    newSessionFeedback = true;
    showFeedback('正在创建新的占问；当前牌面与解读会原样保留。', {
      owner: 'new-session',
    });
    syncNewReadControl();

    try {
      const config = sessionConfig();
      const endpoint = `${config.apiBase}/sessions/${config.sessionId}`;
      const bootstrap = await responseJson(await fetch(endpoint, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      }), '暂时无法确认当前占问，请稍后重试。');
      if (
        bootstrap?.session?.id !== config.sessionId
        || typeof bootstrap.csrf_token !== 'string'
        || !bootstrap.csrf_token
      ) throw new Error('当前占问状态无效，请刷新后重试。');
      if (bootstrap.session.reading?.state === 'running') {
        throw new Error('解读仍在进行，请等待完成或先明确结束本次。');
      }

      const payload = await responseJson(await fetch(`${endpoint}/new`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Companion-CSRF': bootstrap.csrf_token,
        },
        body: JSON.stringify({ action_id: stableActionId(config.sessionId) }),
      }), '新的占问暂时无法开始，请稍后重试。');
      const expectedLocation = `/tarot/session/${payload?.session_id}/`;
      if (
        !managedIdPattern.test(payload?.session_id || '')
        || payload.location !== expectedLocation
      ) throw new Error('新占问返回无效，请留在本页并稍后重试。');
      globalThis.location.assign(expectedLocation);
    } catch (error) {
      newSessionPending = false;
      showFeedback(error?.message || '新的占问暂时无法开始，请稍后重试。', {
        error: true,
        owner: 'new-session',
      });
      syncNewReadControl();
    }
  }

  function installNewReadHandler() {
    const button = newReadButton();
    if (!button || button.dataset.managedNewSession === '1') return;
    button.dataset.managedNewSession = '1';
    button.addEventListener('click', startNewSession, { capture: true });
  }

  function pageOwnerId() {
    try {
      const ownerId = sessionConfig().humanUserId;
      return Number.isSafeInteger(ownerId) && ownerId > 0 ? ownerId : 0;
    } catch {
      return 0;
    }
  }

  function elementVisible(id) {
    const node = document.getElementById(id);
    return Boolean(node && !node.classList.contains('hidden'));
  }

  function localOutboxPending() {
    try {
      const config = sessionConfig();
      const key = `cove-tarot-companion-v1.outbox.${config.sessionId}`;
      const value = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(value) && value.length > 0;
    } catch {
      return false;
    }
  }

  function automaticInviteIsSafe() {
    if (document.hidden || invitationState.processingId || newSessionPending) return false;
    if (!document.getElementById('companionStatus')) return false;
    if (!elementVisible('phase-question')) return false;
    if (document.getElementById('settingsPanel')?.classList.contains('open')) return false;
    if (document.getElementById('managedHistoryPanel')?.classList.contains('open')) return false;
    if (document.getElementById('cardDetail')?.classList.contains('open')) return false;
    if (document.getElementById('readingPanel')?.classList.contains('open')) return false;
    if (elementVisible('phase-spread') || elementVisible('photoPanel') || elementVisible('ritualBar')) return false;
    if (readingIsStreaming() || localOutboxPending()) return false;
    const question = document.getElementById('questionInput')?.value?.trim() || '';
    const status = document.getElementById('companionStatus')?.textContent || '';
    return !question && !/正在恢复|未确认|请勿刷新|尚未保存|正在核对/.test(status);
  }

  function invitationById(sessionId) {
    return invitationState.invitations.find(item => item.session_id === sessionId) || null;
  }

  function setInvitationNotice(message, error = false) {
    invitationState.notice = String(message || '');
    invitationState.noticeError = Boolean(error);
    renderInvitationUi();
  }

  function showInvitationToast(message) {
    const container = document.getElementById('toasts');
    if (!container) return;
    container.querySelectorAll('[data-managed-invite-toast="1"]').forEach(node => node.remove());
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.dataset.managedInviteToast = '1';
    toast.setAttribute('role', 'status');
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity .3s ease';
    }, invitationToastLifetimeMs - 400);
    setTimeout(() => toast.remove(), invitationToastLifetimeMs);
  }

  function invitationButton(label, action, item, className = '') {
    const button = document.createElement('button');
    button.type = 'button';
    const tone = className === 'primary' ? 'primary' : 'ghost';
    button.className = `btn ${tone} small managed-invite-${action}`;
    button.textContent = label;
    button.disabled = Boolean(invitationState.processingId);
    button.setAttribute('aria-disabled', String(button.disabled));
    button.addEventListener('click', () => { void respondInvitation(item.session_id, action); });
    return button;
  }

  function ensureInvitationModal() {
    let modal = document.getElementById('managedInviteModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'managedInviteModal';
    modal.className = 'managed-invite-modal';
    modal.hidden = true;
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-hidden', 'true');
    modal.setAttribute('aria-labelledby', 'managedInviteTitle');
    const box = document.createElement('section');
    box.className = 'managed-invite-dialog panel';
    const eyebrow = document.createElement('div');
    eyebrow.className = 'eyebrow';
    eyebrow.textContent = 'A MESSAGE AWAITS';
    const title = document.createElement('h2');
    title.id = 'managedInviteTitle';
    title.textContent = '小机发来占问';
    const machine = document.createElement('p');
    machine.id = 'managedInviteMachine';
    machine.className = 'managed-invite-machine';
    const question = document.createElement('p');
    question.id = 'managedInviteQuestion';
    question.className = 'managed-invite-question';
    const note = document.createElement('p');
    note.className = 'managed-invite-note';
    note.textContent = '同意后进入该会话并预填原问题；牌阵与抽牌仍由你亲自决定。';
    const status = document.createElement('p');
    status.id = 'managedInviteModalStatus';
    status.className = 'managed-invite-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const actions = document.createElement('div');
    actions.id = 'managedInviteModalActions';
    actions.className = 'managed-invite-actions';
    const later = document.createElement('button');
    later.type = 'button';
    later.className = 'btn ghost small managed-invite-later';
    later.textContent = '稍 后';
    later.addEventListener('click', () => closeInvitationModal(true));
    actions.appendChild(later);
    box.append(eyebrow, title, machine, question, note, status, actions);
    modal.appendChild(box);
    document.body.appendChild(modal);
    modal.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeInvitationModal(true);
    });
    return modal;
  }

  function ensureInvitationHistoryUi() {
    const panel = document.getElementById('managedHistoryPanel');
    const list = document.getElementById('managedHistoryList');
    if (!panel || !list) return null;
    let section = document.getElementById('managedInvitePending');
    if (section) return section;
    section = document.createElement('section');
    section.id = 'managedInvitePending';
    section.className = 'managed-invite-pending';
    section.hidden = true;
    const head = document.createElement('div');
    head.className = 'managed-invite-pending-head';
    const title = document.createElement('h3');
    title.textContent = '待确认邀请';
    const count = document.createElement('span');
    count.id = 'managedInvitePendingCount';
    head.append(title, count);
    const status = document.createElement('p');
    status.id = 'managedInvitePendingStatus';
    status.className = 'managed-invite-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const pendingList = document.createElement('div');
    pendingList.id = 'managedInvitePendingList';
    pendingList.className = 'managed-invite-pending-list';
    section.append(head, status, pendingList);
    panel.insertBefore(section, list);
    return section;
  }

  function closeInvitationModal(deferCurrentBatch = false) {
    const modal = document.getElementById('managedInviteModal');
    if (deferCurrentBatch) {
      for (const item of invitationState.invitations) {
        invitationState.autoSeen.add(item.session_id);
      }
    }
    invitationState.activeId = '';
    if (modal) {
      modal.hidden = true;
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
    }
    document.getElementById('managedHistoryTrigger')?.focus();
  }

  function showInvitationModal(item) {
    const modal = ensureInvitationModal();
    invitationState.activeId = item.session_id;
    invitationState.autoSeen.add(item.session_id);
    setText(document.getElementById('managedInviteMachine'), `${item.machine_name || '你的小机'} 想问`);
    setText(
      document.getElementById('managedInviteQuestion'),
      item.question || '这是一条旧版邀请，没有附带问题；同意后仍可由你填写。',
    );
    const remaining = Math.max(0, invitationState.invitations.length - 1);
    setText(
      document.getElementById('managedInviteModalStatus'),
      remaining ? `还有 ${remaining} 条待确认邀请` : '',
    );
    const actions = document.getElementById('managedInviteModalActions');
    const later = actions?.querySelector('.managed-invite-later');
    if (actions && later) {
      actions.querySelectorAll('[data-managed-invite-action]').forEach(node => node.remove());
      const accept = invitationButton('同意并进入', 'accept', item, 'primary');
      const reject = invitationButton('拒 绝', 'reject', item);
      accept.dataset.managedInviteAction = '1';
      reject.dataset.managedInviteAction = '1';
      actions.insertBefore(accept, later);
      actions.insertBefore(reject, later);
    }
    modal.hidden = false;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    actions?.querySelector('.managed-invite-accept')?.focus();
  }

  function maybeShowInvitation() {
    if (!automaticInviteIsSafe()) return;
    const modal = ensureInvitationModal();
    if (modal.classList.contains('open')) return;
    const item = invitationState.invitations.find(
      candidate => !invitationState.autoSeen.has(candidate.session_id),
    );
    if (item) showInvitationModal(item);
  }

  function renderInvitationUi() {
    const modal = ensureInvitationModal();
    const section = ensureInvitationHistoryUi();
    if (section) {
      const count = document.getElementById('managedInvitePendingCount');
      const status = document.getElementById('managedInvitePendingStatus');
      const list = document.getElementById('managedInvitePendingList');
      section.hidden = !invitationState.invitations.length && !invitationState.notice;
      setText(count, invitationState.invitations.length ? String(invitationState.invitations.length) : '');
      setText(status, invitationState.notice);
      setClass(status, 'error', invitationState.noticeError);
      if (list) {
        list.replaceChildren();
        for (const item of invitationState.invitations) {
          const row = document.createElement('article');
          row.className = 'managed-invite-pending-item';
          row.dataset.sessionId = item.session_id;
          const machine = document.createElement('strong');
          machine.textContent = item.machine_name || '你的小机';
          const question = document.createElement('p');
          question.textContent = item.question || '未附带问题';
          const actions = document.createElement('div');
          actions.className = 'managed-invite-row-actions';
          actions.append(
            invitationButton('同意', 'accept', item, 'primary'),
            invitationButton('拒绝', 'reject', item),
          );
          row.append(machine, question, actions);
          list.appendChild(row);
        }
      }
    }
    const active = invitationById(invitationState.activeId);
    if (!active && invitationState.activeId) closeInvitationModal(false);
    if (active && modal.classList.contains('open')) {
      modal.querySelectorAll('.managed-invite-actions button').forEach(button => {
        button.disabled = Boolean(invitationState.processingId);
        button.setAttribute('aria-disabled', String(button.disabled));
      });
      if (invitationState.notice && (invitationState.processingId || invitationState.noticeError)) {
        const modalStatus = document.getElementById('managedInviteModalStatus');
        setText(modalStatus, invitationState.notice);
        setClass(modalStatus, 'error', invitationState.noticeError);
      }
    }
  }

  function normalizeInvitationSnapshot(payload) {
    const ownerId = pageOwnerId();
    if (!ownerId || payload?.human_user_id !== ownerId) {
      const error = new Error('登录身份已变化，请重新进入塔罗。');
      error.identityInvalid = true;
      throw error;
    }
    if (!Array.isArray(payload.invitations) || typeof payload.cursor !== 'string') {
      throw new Error('邀请列表返回无效，请稍后重试。');
    }
    const invitations = payload.invitations.filter(item => (
      item && managedIdPattern.test(item.session_id || '')
      && typeof item.csrf_token === 'string' && item.csrf_token
      && typeof item.machine_name === 'string'
      && typeof item.question === 'string'
    ));
    return { invitations, cursor: payload.cursor };
  }

  function applyInvitationSnapshot(payload) {
    const snapshot = normalizeInvitationSnapshot(payload);
    const ids = new Set(snapshot.invitations.map(item => item.session_id));
    invitationState.invitations = snapshot.invitations;
    invitationState.cursor = snapshot.cursor;
    invitationState.autoSeen = new Set(
      [...invitationState.autoSeen].filter(sessionId => ids.has(sessionId)),
    );
    if (invitationState.activeId && !ids.has(invitationState.activeId)) {
      closeInvitationModal(false);
    }
    renderInvitationUi();
    maybeShowInvitation();
  }

  async function invitationJson(response, fallback) {
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(payload?.error || fallback);
      error.status = response.status;
      if ([401, 403].includes(response.status)) error.identityInvalid = true;
      throw error;
    }
    return payload;
  }

  async function invitationActionFetch(url, options, timeoutMessage) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), invitationActionTimeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted || error?.name === 'AbortError') {
        throw new Error(timeoutMessage);
      }
      if (error instanceof TypeError) {
        throw new Error('网络连接未完成；邀请仍保留在待确认列表，请稍后重试。');
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  async function fetchInvitationSnapshot({ cursor = '', waitSeconds = 0, signal } = {}) {
    const query = new URLSearchParams();
    if (cursor) {
      query.set('cursor', cursor);
      query.set('wait_seconds', String(waitSeconds));
    }
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return invitationJson(await fetch(`/api/tarot/invitations/pending${suffix}`, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal,
    }), '暂时无法读取待确认邀请。');
  }

  function stopInvitationMonitor({ clear = false } = {}) {
    invitationState.generation += 1;
    invitationState.controller?.abort();
    invitationState.controller = null;
    if (invitationState.retryTimer !== null) {
      clearTimeout(invitationState.retryTimer);
      invitationState.retryTimer = null;
    }
    if (clear) {
      invitationState.invitations = [];
      invitationState.cursor = '';
      invitationState.autoSeen.clear();
      closeInvitationModal(false);
      renderInvitationUi();
    }
  }

  function handleInvitationIdentityLoss(message) {
    invitationState.stopped = true;
    stopInvitationMonitor({ clear: true });
    setInvitationNotice(message || '登录身份已变化，请重新进入塔罗。', true);
  }

  function startInvitationMonitor({ fresh = false } = {}) {
    if (!pageOwnerId() || document.hidden || invitationState.stopped || invitationState.controller) return;
    if (fresh) invitationState.cursor = '';
    const controller = new AbortController();
    const generation = ++invitationState.generation;
    invitationState.controller = controller;
    let retry = false;
    void (async () => {
      try {
        while (!controller.signal.aborted && !document.hidden) {
          const cursor = invitationState.cursor;
          const payload = await fetchInvitationSnapshot({
            cursor,
            waitSeconds: cursor ? 25 : 0,
            signal: controller.signal,
          });
          if (generation !== invitationState.generation) return;
          applyInvitationSnapshot(payload);
        }
      } catch (error) {
        if (error?.name === 'AbortError' || controller.signal.aborted) return;
        if (error?.identityInvalid) {
          handleInvitationIdentityLoss(error.message);
          return;
        }
        retry = true;
        setInvitationNotice(error?.message || '待确认邀请接收暂时中断，将自动重试。', true);
      } finally {
        if (invitationState.controller === controller) invitationState.controller = null;
        if (
          retry && generation === invitationState.generation
          && !document.hidden && !invitationState.stopped
        ) {
          invitationState.retryTimer = setTimeout(() => {
            invitationState.retryTimer = null;
            startInvitationMonitor({ fresh: true });
          }, 15000);
        }
      }
    })();
  }

  async function currentSessionSafeToLeave() {
    if (newSessionPending || readingIsStreaming()) {
      throw new Error('当前解读仍在进行，请等待完成或先明确结束本次。');
    }
    if (localOutboxPending()) {
      throw new Error('当前记录仍有待同步内容，请等待保存确认后再接受邀请。');
    }
    const readingPanelOpen = document.getElementById('readingPanel')?.classList.contains('open');
    if (
      elementVisible('phase-spread')
      || elementVisible('photoPanel')
      || (elementVisible('ritualBar') && !readingPanelOpen)
    ) {
      throw new Error('当前选阵或抽牌尚未完成；请先完成或返回问询，再接受邀请。');
    }
    const status = document.getElementById('companionStatus')?.textContent || '';
    if (/正在恢复|未确认|请勿刷新|尚未保存|正在核对/.test(status)) {
      throw new Error('当前记录尚未确认保存，请稍后再接受邀请。');
    }
    const config = sessionConfig();
    const bootstrap = await responseJson(await invitationActionFetch(
      `${config.apiBase}/sessions/${config.sessionId}`,
      {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      },
      '确认当前占问超时；没有中断或清空当前记录，请稍后重试。',
    ), '暂时无法确认当前占问，请稍后重试。');
    if (bootstrap?.session?.id !== config.sessionId) {
      throw new Error('当前占问状态无效，请刷新后重试。');
    }
    if (bootstrap.session.reading?.state === 'running') {
      throw new Error('当前解读仍在进行，请等待完成或先明确结束本次。');
    }
    const typedQuestion = document.getElementById('questionInput')?.value?.trim() || '';
    const draws = Array.isArray(bootstrap.session.draws) ? bootstrap.session.draws : [];
    if (typedQuestion && !draws.length) {
      throw new Error('当前问题尚未完成抽牌；请先处理本次占问，避免离开后遗漏。');
    }
  }

  async function refreshInvitationsAfterConflict() {
    if (invitationState.stopped) return;
    stopInvitationMonitor();
    try {
      applyInvitationSnapshot(await fetchInvitationSnapshot());
    } catch (error) {
      if (error?.identityInvalid) handleInvitationIdentityLoss(error.message);
      else setInvitationNotice(error?.message || '待确认邀请刷新失败，请稍后重试。', true);
    } finally {
      if (!invitationState.stopped) startInvitationMonitor();
    }
  }

  async function respondInvitation(sessionId, answer) {
    if (invitationState.processingId || !['accept', 'reject'].includes(answer)) return;
    const item = invitationById(sessionId);
    if (!item) {
      setInvitationNotice('该邀请已在其他页面处理或已过期，正在刷新。', true);
      await refreshInvitationsAfterConflict();
      return;
    }
    invitationState.processingId = sessionId;
    setInvitationNotice(answer === 'accept' ? '正在确认并进入该占问……' : '正在拒绝该邀请……');
    renderInvitationUi();
    try {
      if (answer === 'accept') await currentSessionSafeToLeave();
      const payload = await invitationJson(await invitationActionFetch(
        `/api/tarot/invitations/${encodeURIComponent(sessionId)}/${answer}`,
        {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'X-Tarot-CSRF': item.csrf_token,
          },
          body: '{}',
        },
        '邀请处理超时；当前记录未被清空，请刷新待确认状态后再试。',
      ), '邀请处理失败，请稍后重试。');
      const expected = answer === 'accept' ? 'accepted' : 'rejected';
      if (payload?.invitation_state !== expected) {
        throw new Error('邀请状态返回无效，请刷新后重试。');
      }
      invitationState.invitations = invitationState.invitations.filter(
        candidate => candidate.session_id !== sessionId,
      );
      invitationState.autoSeen.delete(sessionId);
      invitationState.processingId = '';
      if (answer === 'accept') {
        closeInvitationModal(false);
        globalThis.location.assign(`/tarot/session/${sessionId}/`);
        return;
      }
      closeInvitationModal(false);
      setInvitationNotice('');
      showInvitationToast('已拒绝');
      maybeShowInvitation();
    } catch (error) {
      invitationState.processingId = '';
      if (error?.identityInvalid) {
        handleInvitationIdentityLoss(error.message);
        return;
      }
      if ([404, 409, 410].includes(error?.status)) {
        setInvitationNotice('该邀请已在其他页面处理或已过期，正在刷新。', true);
        await refreshInvitationsAfterConflict();
        return;
      }
      setInvitationNotice(error?.message || '邀请处理失败，请稍后重试。', true);
      renderInvitationUi();
    }
  }

  function sync() {
    scheduled = false;
    installNewReadHandler();
    syncReadingCompanion();
    syncNewReadControl();
    ensureInvitationModal();
    const hadPendingUi = Boolean(document.getElementById('managedInvitePending'));
    const pendingUi = ensureInvitationHistoryUi();
    if (!hadPendingUi && pendingUi) renderInvitationUi();
    maybeShowInvitation();
  }

  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(sync);
  }

  new MutationObserver(scheduleSync).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class', 'disabled', 'data-managed-companion'],
    childList: true,
    subtree: true,
    characterData: true,
  });
  document.addEventListener('cedartoy:tarot-reading-state', handleReadingState);
  window.addEventListener('resize', scheduleSync, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopInvitationMonitor();
    } else if (!invitationState.stopped) {
      startInvitationMonitor({ fresh: true });
    }
  });
  window.addEventListener('focus', () => {
    if (!document.hidden && !invitationState.stopped) {
      stopInvitationMonitor();
      startInvitationMonitor({ fresh: true });
    }
  });
  document.addEventListener('input', event => {
    if (event.target?.id === 'questionInput') scheduleSync();
  });
  window.addEventListener('pagehide', () => {
    invitationState.stopped = true;
    stopInvitationMonitor({ clear: true });
  });
  scheduleSync();
  queueMicrotask(() => startInvitationMonitor({ fresh: true }));
})();
