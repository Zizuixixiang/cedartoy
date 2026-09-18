(() => {
  'use strict';

  function installSecureRandomUUID() {
    const unavailable = () => {
      throw new Error('当前浏览器缺少安全随机源，本次记录尚未保存。');
    };
    let cryptoApi = globalThis.crypto;
    if (!cryptoApi) {
      try {
        cryptoApi = { randomUUID: unavailable };
        Object.defineProperty(globalThis, 'crypto', {
          configurable: true,
          value: cryptoApi,
        });
      } catch { /* The upstream call will fail closed instead of using weak randomness. */ }
      return false;
    }
    if (typeof cryptoApi.randomUUID === 'function') return true;
    let implementation = unavailable;
    if (typeof cryptoApi.getRandomValues === 'function') {
      implementation = () => {
        const bytes = new Uint8Array(16);
        try {
          cryptoApi.getRandomValues(bytes);
        } catch {
          throw new Error('当前浏览器的安全随机源不可用，本次记录尚未保存。');
        }
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = [...bytes].map(value => value.toString(16).padStart(2, '0'));
        return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
      };
    }
    try {
      Object.defineProperty(cryptoApi, 'randomUUID', {
        configurable: true,
        value: implementation,
      });
    } catch {
      try { cryptoApi.randomUUID = implementation; } catch { /* Fail closed. */ }
    }
    return typeof cryptoApi.randomUUID === 'function'
      && implementation !== unavailable;
  }

  const secureUuidAvailable = installSecureRandomUUID();

  const labels = {
    'gemini-3.5-flash': ['Flash', 'Gemini 3.5 Flash'],
    'gemini-3.1-pro-preview': ['Pro', 'Gemini 3.1 Pro'],
  };
  const endedText = '本次已结束，记录已保留。';
  const terminalPhases = new Set(['returned', 'stopped', 'deleted']);
  const usableStatuses = new Set(['available', 'retry_ready']);
  const publicStatuses = new Set([
    'available', 'cooling', 'retry_ready', 'probing', 'disabled', 'unconfigured',
  ]);
  let ending = false;
  let ended = false;
  let scheduled = false;
  let settingsOpen = false;
  let statusPhase = 'unknown';
  let statusRequest = null;
  let expiryRefreshPending = false;
  let expiryTimer = null;
  let modelStatuses = new Map();
  let historyCsrf = '';
  let historyLoading = false;
  let historyNextOffset = null;
  let saveStateCheck = null;

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function setClass(node, name, enabled) {
    if (node && node.classList.contains(name) !== enabled) {
      node.classList.toggle(name, enabled);
    }
  }

  function selectedModel() {
    const select = document.querySelector('#providerList select');
    return labels[select?.value] ? select.value : 'gemini-3.5-flash';
  }

  function companionElements() {
    const status = document.getElementById('companionStatus');
    const box = status?.closest('.panel') || null;
    if (!box) {
      return { status, box, legacyFinish: null, legacyStop: null, end: null };
    }
    const buttons = [...box.querySelectorAll('button')];
    const legacyFinish = buttons.find(button =>
      button.dataset.managedLegacyFinish === '1'
      || button.textContent.trim() === '返回聊天');
    const legacyStop = buttons.find(button =>
      button.dataset.managedLegacyStop === '1'
      || button.textContent.trim() === '停止本次');
    const end = buttons.find(button => button.dataset.managedEnd === '1') || null;
    return { status, box, legacyFinish, legacyStop, end };
  }

  function sessionConfig() {
    const node = document.getElementById('companion-config');
    if (!node) throw new Error('缺少会话配置');
    const config = JSON.parse(node.textContent);
    if (
      config?.apiBase !== '/companion/v1'
      || typeof config.sessionId !== 'string'
      || !/^[A-Za-z0-9_-]{1,128}$/.test(config.sessionId)
    ) {
      throw new Error('会话配置无效');
    }
    return config;
  }

  function sessionEndpoint() {
    const config = sessionConfig();
    return `${config.apiBase}/sessions/${config.sessionId}`;
  }

  async function loadSession() {
    const response = await fetch(sessionEndpoint(), {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.session) {
      throw new Error(payload?.error || '无法确认会话状态');
    }
    if (typeof payload.csrf_token === 'string' && payload.csrf_token) {
      historyCsrf = payload.csrf_token;
    }
    return payload.session;
  }

  function isTerminal(session) {
    return terminalPhases.has(session?.phase);
  }

  function shouldStop(session) {
    const draws = Array.isArray(session?.draws) ? session.draws : [];
    const fullyRevealed = draws.length > 0
      && draws.every(draw => draw?.revealed === true);
    return !fullyRevealed || session?.reading?.state === 'running';
  }

  function markEnded() {
    ended = true;
    ending = false;
    const { status, end } = companionElements();
    setText(status, endedText);
    if (end) {
      setText(end, '已结束');
      end.disabled = true;
      end.setAttribute('aria-disabled', 'true');
    }
  }

  function markEnding() {
    const { status, end } = companionElements();
    setText(status, '正在结束本次……');
    if (end) {
      setText(end, '结束中…');
      end.disabled = true;
      end.setAttribute('aria-disabled', 'true');
    }
  }

  function markEndFailed() {
    ending = false;
    const { status, end } = companionElements();
    setText(status, '暂时无法结束本次，请重试。');
    if (end) {
      setText(end, '结束本次');
      end.disabled = false;
      end.removeAttribute('aria-disabled');
    }
  }

  async function waitForLegacyAction(button) {
    await Promise.resolve();
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (!button.disabled) return loadSession();
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('结束操作超时');
  }

  async function endManagedSession() {
    if (ending || ended) return;
    const { legacyFinish, legacyStop } = companionElements();
    if (!legacyFinish || !legacyStop) return;
    ending = true;
    markEnding();
    try {
      let current = await loadSession();
      if (isTerminal(current)) {
        markEnded();
        return;
      }

      const firstAction = shouldStop(current) ? legacyStop : legacyFinish;
      firstAction.click();
      current = await waitForLegacyAction(firstAction);

      if (!isTerminal(current) && firstAction === legacyFinish && shouldStop(current)) {
        legacyStop.click();
        current = await waitForLegacyAction(legacyStop);
      }

      if (!isTerminal(current)) throw new Error('会话尚未进入结束状态');
      markEnded();
    } catch {
      markEndFailed();
    }
  }

  function hideLegacyButton(button, marker) {
    if (!button) return;
    button.dataset[marker] = '1';
    button.hidden = true;
    button.tabIndex = -1;
    button.setAttribute('aria-hidden', 'true');
  }

  function decorateCompanion() {
    const elements = companionElements();
    const { status, box, legacyFinish, legacyStop } = elements;
    let { end } = elements;
    if (!box || !legacyFinish || !legacyStop) return;
    box.dataset.managedCompanion = '1';
    hideLegacyButton(legacyFinish, 'managedLegacyFinish');
    hideLegacyButton(legacyStop, 'managedLegacyStop');

    if (!end) {
      end = document.createElement('button');
      end.type = 'button';
      end.className = 'btn ghost small';
      end.dataset.managedEnd = '1';
      end.title = '结束本次操作，已有记录保留';
      end.setAttribute('aria-label', '结束本次操作，已有记录保留');
      end.addEventListener('click', endManagedSession);
      box.appendChild(end);
      void loadSession().then(session => {
        if (isTerminal(session)) markEnded();
      }).catch(() => {});
    }

    if (ended) {
      setText(status, endedText);
      setText(end, '已结束');
      end.disabled = true;
      end.setAttribute('aria-disabled', 'true');
    } else if (ending) {
      markEnding();
    } else {
      setText(end, '结束本次');
      end.disabled = false;
      end.removeAttribute('aria-disabled');
    }
  }

  function modelView(model) {
    if (statusPhase === 'loading') {
      return { status: 'checking', text: '正在确认…', disabled: true };
    }
    if (statusPhase !== 'ready') {
      return { status: 'unknown', text: '暂无法确认，请重查', disabled: true };
    }
    const item = modelStatuses.get(model);
    if (!item) return { status: 'unknown', text: '暂无法确认，请重查', disabled: true };
    if (item.status === 'cooling') {
      return { status: 'cooling', text: '暂不可用', disabled: true };
    }
    const text = {
      available: '可用',
      retry_ready: '可重试（尚未确认恢复）',
      probing: '探测中 · 请稍后重查',
      disabled: '暂不可用',
      unconfigured: '未配置',
    }[item.status] || '暂无法确认，请重查';
    return { status: item.status, text, disabled: !usableStatuses.has(item.status) };
  }

  function validateStatusPayload(payload) {
    if (!payload || !Array.isArray(payload.models) || payload.models.length !== 2) {
      throw new Error('invalid model status');
    }
    const expected = Object.keys(labels);
    const records = new Map();
    payload.models.forEach((item, index) => {
      if (
        !item
        || item.model !== expected[index]
        || !publicStatuses.has(item.status)
        || !Number.isInteger(item.remaining_seconds)
        || item.remaining_seconds < 0
        || (item.status !== 'cooling' && item.remaining_seconds !== 0)
      ) {
        throw new Error('invalid model status');
      }
      records.set(item.model, { ...item });
    });
    return records;
  }

  function clearExpiryRefresh() {
    if (expiryTimer !== null) {
      clearTimeout(expiryTimer);
      expiryTimer = null;
    }
  }

  function syncExpiryRefresh() {
    clearExpiryRefresh();
    if (document.hidden || statusPhase !== 'ready') return;
    const waits = [...modelStatuses.values()]
      .filter(item => item.status === 'cooling' && item.remaining_seconds > 0)
      .map(item => item.remaining_seconds);
    if (!waits.length) return;
    expiryTimer = setTimeout(() => {
      expiryTimer = null;
      if (expiryRefreshPending || document.hidden) return;
      expiryRefreshPending = true;
      void refreshStatuses().finally(() => { expiryRefreshPending = false; });
    }, Math.max(1000, Math.min(...waits) * 1000));
  }

  async function refreshStatuses() {
    if (statusRequest) return statusRequest;
    statusPhase = 'loading';
    clearExpiryRefresh();
    queueRender();
    statusRequest = (async () => {
      try {
        const response = await fetch('/api/tarot/models/status', {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok) throw new Error('model status unavailable');
        modelStatuses = validateStatusPayload(payload);
        statusPhase = 'ready';
      } catch {
        modelStatuses = new Map();
        statusPhase = 'failed';
      } finally {
        statusRequest = null;
        queueRender();
        syncExpiryRefresh();
      }
    })();
    return statusRequest;
  }

  function ensureStatusControls(list) {
    let controls = document.getElementById('managedModelHealth');
    if (controls || !list) return controls;
    controls = document.createElement('div');
    controls.id = 'managedModelHealth';
    controls.className = 'managed-model-health';
    const summary = document.createElement('p');
    summary.id = 'managedModelSummary';
    summary.setAttribute('role', 'status');
    summary.setAttribute('aria-live', 'polite');
    const refresh = document.createElement('button');
    refresh.type = 'button';
    refresh.className = 'btn ghost small managed-model-refresh';
    refresh.textContent = '重新检查';
    refresh.addEventListener('click', event => {
      event.stopPropagation();
      void refreshStatuses();
    });
    controls.append(summary, refresh);
    list.before(controls);
    return controls;
  }

  function renderStatusControls(list) {
    const controls = ensureStatusControls(list);
    if (!controls) return;
    const summary = controls.querySelector('#managedModelSummary');
    const refresh = controls.querySelector('.managed-model-refresh');
    const current = modelView(selectedModel());
    let message = '打开设置时会读取当前运行状态。';
    if (statusPhase === 'loading') message = '正在确认模型状态…';
    else if (statusPhase === 'failed') message = '暂时无法确认模型状态，请重查。';
    else if (statusPhase === 'ready' && current.disabled) {
      message = '当前选择暂不可用，请改选可用模型或稍后重查。';
    } else if (statusPhase === 'ready' && current.status === 'retry_ready') {
      message = '当前模型可重试，但尚未确认额度或服务已恢复。';
    } else if (statusPhase === 'ready') {
      message = '状态来自当前运行服务；“可重试”不代表额度已恢复。';
    }
    setText(summary, message);
    if (refresh) {
      refresh.disabled = statusPhase === 'loading';
      refresh.setAttribute('aria-disabled', String(refresh.disabled));
    }
  }

  function makeNode(tag, className, textValue) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textValue !== undefined) node.textContent = textValue;
    return node;
  }

  function historyPanelOpen() {
    return document.getElementById('managedHistoryPanel')?.classList.contains('open')
      || false;
  }

  function ensureHistoryUi() {
    let panel = document.getElementById('managedHistoryPanel');
    if (panel) return panel;
    const topRight = document.querySelector('#topbar .top-right');
    const ui = document.getElementById('ui');
    if (!topRight || !ui) return null;

    const trigger = document.createElement('button');
    trigger.id = 'managedHistoryTrigger';
    trigger.type = 'button';
    trigger.className = 'orb-btn managed-history-trigger';
    trigger.title = '查看本人已保存的塔罗记录';
    trigger.setAttribute('aria-controls', 'managedHistoryPanel');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.append(
      makeNode('span', 'managed-history-trigger-full', '历史记录'),
      makeNode('span', 'managed-history-trigger-short', '记录'),
    );
    topRight.insertBefore(trigger, document.getElementById('providerOrb'));

    panel = document.createElement('aside');
    panel.id = 'managedHistoryPanel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', 'managedHistoryTitle');
    panel.setAttribute('aria-hidden', 'true');
    const head = makeNode('div', 'managed-history-head');
    head.append(
      makeNode('div', 'eyebrow', 'SAVED READINGS'),
      makeNode('h2', '', '历史记录'),
    );
    head.querySelector('h2').id = 'managedHistoryTitle';
    const close = makeNode('button', 'detail-close', '收 起');
    close.type = 'button';
    close.id = 'managedHistoryClose';
    close.setAttribute('aria-label', '关闭历史记录');
    head.appendChild(close);
    const status = makeNode('p', 'managed-history-status', '');
    status.id = 'managedHistoryStatus';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const list = makeNode('div', 'managed-history-list');
    list.id = 'managedHistoryList';
    const detail = makeNode('div', 'managed-history-detail');
    detail.id = 'managedHistoryDetail';
    detail.hidden = true;
    const more = makeNode('button', 'btn ghost small managed-history-more', '加载更多');
    more.type = 'button';
    more.hidden = true;
    panel.append(head, status, list, detail, more);
    ui.appendChild(panel);

    trigger.addEventListener('click', () => { void openHistory(); });
    close.addEventListener('click', closeHistory);
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeHistory();
    });
    more.addEventListener('click', () => {
      if (historyNextOffset !== null) void loadHistory(historyNextOffset, true);
    });
    return panel;
  }

  function setHistoryStatus(message, error = false) {
    const node = document.getElementById('managedHistoryStatus');
    setText(node, message);
    setClass(node, 'error', error);
  }

  function closeHistory() {
    const panel = document.getElementById('managedHistoryPanel');
    const trigger = document.getElementById('managedHistoryTrigger');
    panel?.classList.remove('open');
    panel?.setAttribute('aria-hidden', 'true');
    trigger?.setAttribute('aria-expanded', 'false');
    syncSettingsVisibility();
    trigger?.focus();
    queueRender();
  }

  async function openHistory() {
    const panel = ensureHistoryUi();
    if (!panel) return;
    document.getElementById('settingsPanel')?.classList.remove('open');
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    document.getElementById('managedHistoryTrigger')?.setAttribute('aria-expanded', 'true');
    syncSettingsVisibility();
    queueRender();
    panel.querySelector('#managedHistoryClose')?.focus();
    await loadHistory(0, false);
  }

  function historyTime(value) {
    const date = new Date(Number(value) * 1000);
    if (!Number.isFinite(date.getTime())) return '时间未知';
    return date.toLocaleString('zh-CN', { hour12: false });
  }

  function clearHistoryDetail() {
    const detail = document.getElementById('managedHistoryDetail');
    const list = document.getElementById('managedHistoryList');
    if (detail) {
      detail.hidden = true;
      detail.replaceChildren();
    }
    if (list) list.hidden = false;
  }

  function historyDeleteButton(item) {
    const button = makeNode('button', 'btn ghost small managed-history-delete', '删 除');
    button.type = 'button';
    button.title = '永久删除这条塔罗记录';
    button.setAttribute('aria-label', `删除记录：${item.question_summary || '未填写问题'}`);
    button.addEventListener('click', event => {
      event.stopPropagation();
      void deleteHistory(item.session_id, item.question_summary, button);
    });
    return button;
  }

  function renderHistoryItem(item) {
    const row = makeNode('article', 'managed-history-item');
    const view = makeNode('button', 'managed-history-view');
    view.type = 'button';
    view.append(
      makeNode('time', 'managed-history-time', historyTime(item.updated_at)),
      makeNode('strong', 'managed-history-question', item.question_summary || '未填写问题'),
      makeNode('span', 'managed-history-spread', item.spread_name || '未知牌阵'),
    );
    view.addEventListener('click', () => { void loadHistoryDetail(item.session_id); });
    row.append(view, historyDeleteButton(item));
    return row;
  }

  async function loadHistory(offset = 0, append = false) {
    if (historyLoading) return;
    historyLoading = true;
    const list = document.getElementById('managedHistoryList');
    const more = document.querySelector('.managed-history-more');
    clearHistoryDetail();
    setHistoryStatus('正在读取已保存记录……');
    if (more) more.disabled = true;
    try {
      const response = await fetch(`/api/tarot/history?offset=${offset}&limit=10`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !Array.isArray(payload?.items)) {
        throw new Error(payload?.error || '无法读取历史记录');
      }
      if (!append) list?.replaceChildren();
      for (const item of payload.items) {
        if (
          typeof item?.session_id !== 'string'
          || typeof item.question_summary !== 'string'
          || typeof item.spread_name !== 'string'
        ) continue;
        list?.appendChild(renderHistoryItem(item));
      }
      if (!append && !list?.children.length) {
        list?.appendChild(makeNode('p', 'managed-history-empty', '暂无已保存牌面的记录。'));
      }
      historyNextOffset = Number.isInteger(payload.next_offset)
        ? payload.next_offset : null;
      if (more) more.hidden = historyNextOffset === null;
      setHistoryStatus('');
    } catch (error) {
      setHistoryStatus(error.message || '暂时无法读取历史记录。', true);
    } finally {
      historyLoading = false;
      if (more) more.disabled = false;
    }
  }

  function appendHistoryField(parent, label, value) {
    const field = makeNode('section', 'managed-history-field');
    field.append(
      makeNode('div', 'managed-history-field-label', label),
      makeNode('p', '', value || '—'),
    );
    parent.appendChild(field);
  }

  async function loadHistoryDetail(sessionId) {
    const list = document.getElementById('managedHistoryList');
    const detail = document.getElementById('managedHistoryDetail');
    const more = document.querySelector('.managed-history-more');
    if (!list || !detail) return;
    setHistoryStatus('正在读取记录……');
    try {
      const response = await fetch(`/api/tarot/history/${encodeURIComponent(sessionId)}`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      const item = await response.json().catch(() => null);
      if (!response.ok || item?.session_id !== sessionId) {
        throw new Error(item?.error || '无法读取这条记录');
      }
      detail.replaceChildren();
      const back = makeNode('button', 'btn ghost small managed-history-back', '返 回 列 表');
      back.type = 'button';
      back.addEventListener('click', () => {
        detail.hidden = true;
        list.hidden = false;
        if (more) more.hidden = historyNextOffset === null;
        setHistoryStatus('');
      });
      detail.appendChild(back);
      appendHistoryField(detail, '时间', historyTime(item.updated_at));
      appendHistoryField(detail, '问题', typeof item.question === 'string' ? item.question : '');
      appendHistoryField(detail, '牌阵', item.spread?.zh || '未知牌阵');
      const cards = makeNode('section', 'managed-history-field');
      cards.appendChild(makeNode('div', 'managed-history-field-label', '牌面'));
      const cardList = makeNode('div', 'managed-history-cards');
      for (const card of Array.isArray(item.cards) ? item.cards : []) {
        const direction = card.reversed ? '逆位' : '正位';
        const position = card.slot ? `${card.slot} · ` : '';
        cardList.appendChild(
          makeNode('div', 'managed-history-card', `${position}${card.zh || card.card_id} · ${direction}`),
        );
      }
      if (!cardList.children.length) {
        cardList.appendChild(makeNode('p', '', '没有可显示的牌面。'));
      }
      cards.appendChild(cardList);
      detail.appendChild(cards);
      const reading = makeNode('section', 'managed-history-field');
      reading.appendChild(makeNode('div', 'managed-history-field-label', '已有解读'));
      if (typeof item.reading?.text === 'string' && item.reading.text) {
        reading.appendChild(makeNode('pre', 'managed-history-reading', item.reading.text));
      } else {
        const stateText = item.reading?.state === 'running'
          ? '解读仍在进行；查看历史不会重新请求。'
          : '尚无已保存解读。';
        reading.appendChild(makeNode('p', '', stateText));
      }
      detail.appendChild(reading);
      detail.appendChild(historyDeleteButton({
        session_id: sessionId,
        question_summary: typeof item.question === 'string' ? item.question.slice(0, 80) : '未填写问题',
      }));
      list.hidden = true;
      detail.hidden = false;
      if (more) more.hidden = true;
      setHistoryStatus('');
    } catch (error) {
      setHistoryStatus(error.message || '暂时无法读取这条记录。', true);
    }
  }

  async function deleteHistory(sessionId, question, button) {
    if (!window.confirm(`确定永久删除“${question || '未填写问题'}”这条记录吗？`)) return;
    button.disabled = true;
    setHistoryStatus('正在删除记录……');
    try {
      if (!historyCsrf) await loadSession();
      const currentSessionId = sessionConfig().sessionId;
      const response = await fetch(`/api/tarot/history/${encodeURIComponent(sessionId)}/delete`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Companion-CSRF': historyCsrf,
        },
        body: JSON.stringify({ confirm: true, csrf_session_id: currentSessionId }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || payload?.deleted !== true || payload.session_id !== sessionId) {
        throw new Error(payload?.error || '删除未完成');
      }
      if (sessionId === currentSessionId) {
        setHistoryStatus('记录已删除，正在退出本页。');
        window.location.replace('/');
        return;
      }
      setHistoryStatus('记录已删除。');
      await loadHistory(0, false);
    } catch (error) {
      button.disabled = false;
      setHistoryStatus(error.message || '暂时无法删除记录。', true);
    }
  }

  function localOutboxPending() {
    try {
      const key = `cove-tarot-companion-v1.outbox.${sessionConfig().sessionId}`;
      const value = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(value) && value.length > 0;
    } catch {
      return false;
    }
  }

  function normalizeSaveFailure() {
    const status = document.getElementById('companionStatus');
    if (
      !status
      || !status.textContent.includes('同步尚未确认；已保留本次记录，请刷新重试。')
      || saveStateCheck
    ) return;
    const randomUnavailable = !secureUuidAvailable
      || status.textContent.includes('安全随机源');
    if (localOutboxPending()) {
      setText(status, '服务器保存状态尚未确认；本机确有待同步记录。重新打开本页会用同一编号重试，不会重新抽牌。');
      return;
    }
    setText(status, '正在核对本次记录是否保存……');
    saveStateCheck = loadSession().then(session => {
      const draws = Array.isArray(session?.draws) ? session.draws : [];
      if (draws.length && randomUnavailable) {
        setText(status, '服务器已确认保存本次牌面；当前浏览器缺少安全随机源，后续同步或解读操作未发起。');
      } else if (draws.length) {
        setText(status, '服务器已确认保存本次牌面；当前同步或解读步骤未完整确认。');
      } else if (randomUnavailable) {
        setText(status, '当前浏览器缺少安全随机源，本次牌面尚未保存。请勿刷新或离开当前页。');
      } else {
        setText(status, '服务器未确认收到本次牌面，本机也没有待同步记录。本次尚未保存，请勿刷新或离开当前页。');
      }
    }).catch(() => {
      setText(
        status,
        randomUnavailable
          ? '当前浏览器缺少安全随机源，本次记录尚未保存。请勿刷新或离开当前页。'
          : '暂时无法确认服务器是否保存；本机没有待同步记录。请勿刷新或离开当前页。',
      );
    }).finally(() => { saveStateCheck = null; });
  }

  function syncSettingsVisibility() {
    const settings = document.getElementById('settingsPanel');
    const { box } = companionElements();
    if (!settings) return false;
    const open = settings.classList.contains('open');
    const history = document.getElementById('managedHistoryPanel');
    if (open && history?.classList.contains('open')) {
      history.classList.remove('open');
      history.setAttribute('aria-hidden', 'true');
      document.getElementById('managedHistoryTrigger')?.setAttribute('aria-expanded', 'false');
    }
    const overlayOpen = open || history?.classList.contains('open');
    if (box) {
      setClass(box, 'managed-companion-settings-hidden', Boolean(overlayOpen));
      if (overlayOpen) box.setAttribute('aria-hidden', 'true');
      else box.removeAttribute('aria-hidden');
    }
    return open;
  }

  function normalizeToasts() {
    for (const toast of document.querySelectorAll('#toasts .toast')) {
      if (toast.textContent.trim() === '本次记录已锁定；请返回聊天开始新的占问。') {
        setText(toast, '本次记录已锁定；请先结束本次，再开始新的占问。');
      }
    }
  }

  function render() {
    scheduled = false;
    ensureHistoryUi();
    const orb = document.getElementById('providerOrb');
    if (orb) orb.title = '选择本站塔罗模型';
    const current = selectedModel();
    setText(document.getElementById('providerLabel'), `本站 ${labels[current][0]}`);

    const list = document.getElementById('providerList');
    const item = list?.querySelector('.provider-item');
    const select = item?.querySelector('select');
    if (item && select) {
      item.querySelectorAll('input, .p-remove').forEach(node => node.remove());
      setClass(select, 'managed-model-source', true);
      select.setAttribute('aria-hidden', 'true');
      for (const option of [...select.options]) {
        if (labels[option.value]) setText(option, labels[option.value][1]);
        else option.remove();
      }

      let choices = item.querySelector('.managed-model-options');
      if (!choices) {
        choices = document.createElement('div');
        choices.className = 'managed-model-options';
        choices.setAttribute('role', 'radiogroup');
        choices.setAttribute('aria-label', '解读模型');
        for (const [model, names] of Object.entries(labels)) {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'managed-model-option';
          button.dataset.model = model;
          button.setAttribute('role', 'radio');
          const copy = document.createElement('span');
          copy.className = 'managed-model-copy';
          const name = document.createElement('strong');
          name.textContent = names[0];
          const detail = document.createElement('small');
          detail.textContent = names[1];
          const health = document.createElement('small');
          health.className = 'managed-model-status';
          copy.append(name, detail);
          button.append(copy, health);
          button.addEventListener('click', event => {
            event.stopPropagation();
            if (button.disabled) return;
            select.value = model;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            queueRender();
          });
          choices.appendChild(button);
        }
        item.appendChild(choices);
      }
      for (const button of choices.querySelectorAll('.managed-model-option')) {
        const active = button.dataset.model === select.value;
        const view = modelView(button.dataset.model);
        setClass(button, 'selected', active);
        setClass(button, 'managed-model-unavailable', view.disabled);
        button.setAttribute('aria-checked', String(active));
        button.disabled = view.disabled;
        button.setAttribute('aria-disabled', String(view.disabled));
        button.dataset.runtimeStatus = view.status;
        setText(button.querySelector('.managed-model-status'), view.text);
      }
      setText(
        document.getElementById('providerLabel'),
        `本站 ${labels[selectedModel()][0]}`,
      );
    }
    renderStatusControls(list);

    decorateCompanion();
    const open = syncSettingsVisibility();
    if (open !== settingsOpen) {
      settingsOpen = open;
      if (open) void refreshStatuses();
    }
    normalizeToasts();
    normalizeSaveFailure();
  }

  function queueRender() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(render);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearExpiryRefresh();
    else void refreshStatuses();
  });
  window.addEventListener('pagehide', clearExpiryRefresh);
  new MutationObserver(queueRender).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
    childList: true,
    subtree: true,
    characterData: true,
  });
  queueRender();
})();
