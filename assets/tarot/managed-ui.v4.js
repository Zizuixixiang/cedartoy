(() => {
  'use strict';

  function stabilizeProviderLabel() {
    const orb = document.getElementById('providerOrb');
    const upstreamLabel = document.getElementById('providerLabel');
    if (!orb || !upstreamLabel) return;

    upstreamLabel.hidden = true;
    upstreamLabel.setAttribute('aria-hidden', 'true');
    let label = document.getElementById('managedProviderLabel');
    if (!label) {
      label = document.createElement('span');
      label.id = 'managedProviderLabel';
      label.className = 'orb-label managed-provider-label';
      label.textContent = '配置';
      upstreamLabel.after(label);
    }
    if (label.textContent !== '配置') label.textContent = '配置';
    orb.setAttribute('aria-label', '配置');
  }

  function compactHistoryTrigger() {
    const trigger = document.getElementById('managedHistoryTrigger');
    const topRight = document.querySelector('#topbar .top-right');
    const providerOrb = document.getElementById('providerOrb');
    if (!trigger || !topRight || !providerOrb) return false;
    if (trigger.parentElement !== topRight) {
      topRight.insertBefore(trigger, providerOrb);
    }
    if (trigger.textContent !== '记录' || trigger.childNodes.length !== 1) {
      trigger.replaceChildren(document.createTextNode('记录'));
    }
    trigger.setAttribute('aria-label', '历史记录');
    trigger.title = '查看本人已保存的塔罗记录';
    return true;
  }

  stabilizeProviderLabel();
  queueMicrotask(() => {
    stabilizeProviderLabel();
    if (!compactHistoryTrigger()) setTimeout(compactHistoryTrigger, 0);
  });
})();
