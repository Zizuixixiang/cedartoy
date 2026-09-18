(() => {
  'use strict';

  const labels = {
    'gemini-3.5-flash': ['Flash', 'Gemini 3.5 Flash'],
    'gemini-3.1-pro-preview': ['Pro', 'Gemini 3.1 Pro'],
  };
  const endedText = '本次已结束，记录已保留。';
  const terminalPhases = new Set(['returned', 'stopped', 'deleted']);
  let ending = false;
  let ended = false;
  let scheduled = false;

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

  function sessionEndpoint() {
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

      // A reading may start between the status read and the return request.
      // The original return handler refuses atomically; refresh and then use
      // the original stop handler to end the now-running operation.
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

  function syncSettingsVisibility() {
    const settings = document.getElementById('settingsPanel');
    const { box } = companionElements();
    if (!settings || !box) return;
    const open = settings.classList.contains('open');
    setClass(box, 'managed-companion-settings-hidden', open);
    if (open) box.setAttribute('aria-hidden', 'true');
    else box.removeAttribute('aria-hidden');
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
          const name = document.createElement('span');
          name.textContent = names[0];
          const detail = document.createElement('small');
          detail.textContent = names[1];
          button.append(name, detail);
          button.addEventListener('click', event => {
            event.stopPropagation();
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
        setClass(button, 'selected', active);
        button.setAttribute('aria-checked', String(active));
      }
      setText(
        document.getElementById('providerLabel'),
        `本站 ${labels[selectedModel()][0]}`,
      );
    }

    decorateCompanion();
    syncSettingsVisibility();
    normalizeToasts();
  }

  function queueRender() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(render);
  }

  new MutationObserver(queueRender).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
    childList: true,
    subtree: true,
    characterData: true,
  });
  queueRender();
})();
