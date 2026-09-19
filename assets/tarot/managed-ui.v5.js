(() => {
  'use strict';

  let scheduled = false;
  let readingWasOpen = false;
  let streamWasActive = false;

  function isMobile() {
    const width = Number(globalThis.innerWidth);
    return Number.isFinite(width) && width <= 600;
  }

  function companionBox() {
    const status = document.getElementById('companionStatus');
    return status?.closest('[data-managed-companion="1"]')
      || status?.closest('.panel')
      || null;
  }

  function syncReadingUi() {
    scheduled = false;
    const ui = document.getElementById('ui');
    const panel = document.getElementById('readingPanel');
    const stream = document.getElementById('readingStream');
    if (!ui || !panel || !stream) return;

    const readingOpen = panel.classList.contains('open');
    const streamActive = stream.classList.contains('streaming');
    if ((readingOpen && !readingWasOpen) || (streamActive && !streamWasActive)) {
      stream.scrollTop = 0;
    }
    readingWasOpen = readingOpen;
    streamWasActive = streamActive;

    const box = companionBox();
    if (!box) return;
    const dock = readingOpen && isMobile();
    if (dock) {
      if (box.parentElement !== panel) panel.prepend(box);
      if (!box.classList.contains('managed-companion-reading')) {
        box.classList.add('managed-companion-reading');
      }
    } else {
      if (box.parentElement !== ui) ui.appendChild(box);
      if (box.classList.contains('managed-companion-reading')) {
        box.classList.remove('managed-companion-reading');
      }
    }
  }

  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(syncReadingUi);
  }

  new MutationObserver(scheduleSync).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class', 'data-managed-companion'],
    childList: true,
    subtree: true,
  });
  window.addEventListener('resize', scheduleSync, { passive: true });
  scheduleSync();
})();
