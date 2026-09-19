(() => {
  'use strict';

  const COMPACT_QUERY = '(hover: none) and (pointer: coarse) and (max-width: 600px), (hover: none) and (pointer: coarse) and (orientation: landscape) and (max-height: 600px) and (max-width: 1000px)';
  const root = document.documentElement;
  const boardSpace = document.querySelector('.board-space');
  const boardCanvas = document.querySelector('.page-shell');
  const compactMedia = window.matchMedia(COMPACT_QUERY);

  if (!boardSpace || !boardCanvas) return;

  const viewportHeight = () => {
    const visualHeight = window.visualViewport && window.visualViewport.height;
    return Math.max(1, Math.round(visualHeight || window.innerHeight));
  };

  let wasCompact = false;
  const syncLayout = () => {
    if (compactMedia.matches) {
      wasCompact = true;
      root.style.setProperty('--cedartoy-viewport-height', `${viewportHeight()}px`);
      boardSpace.style.removeProperty('width');
      boardSpace.style.removeProperty('height');
      boardCanvas.style.removeProperty('--board-scale');
      root.style.removeProperty('--board-scale');
      root.dataset.cedartoyLayout = 'compact';
      return;
    }

    // Desktop retains the original frontend sizing logic.
    if (wasCompact) {
      wasCompact = false;
      root.style.removeProperty('--cedartoy-viewport-height');
      delete root.dataset.cedartoyLayout;
      root.classList.remove('cedartoy-dialog-open');
      if (typeof syncBoardScale === 'function') syncBoardScale();
    }
  };

  let pendingFrame = 0;
  const scheduleLayout = () => {
    if (pendingFrame) window.cancelAnimationFrame(pendingFrame);
    pendingFrame = window.requestAnimationFrame(() => {
      pendingFrame = 0;
      syncLayout();
    });
  };

  const dialogs = Array.from(document.querySelectorAll('dialog'));
  const syncDialogState = () => {
    root.classList.toggle(
      'cedartoy-dialog-open',
      compactMedia.matches && dialogs.some((dialog) => dialog.hasAttribute('open')),
    );
  };

  if ('MutationObserver' in window) {
    const dialogObserver = new MutationObserver(syncDialogState);
    for (const dialog of dialogs) {
      dialogObserver.observe(dialog, { attributes: true, attributeFilter: ['open'] });
      dialog.addEventListener('close', syncDialogState);
      dialog.addEventListener('cancel', syncDialogState);
    }
  }

  window.addEventListener('resize', scheduleLayout, { passive: true });
  window.addEventListener('orientationchange', scheduleLayout, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', scheduleLayout, { passive: true });
    window.visualViewport.addEventListener('scroll', scheduleLayout, { passive: true });
  }
  if (typeof compactMedia.addEventListener === 'function') {
    compactMedia.addEventListener('change', scheduleLayout);
  } else {
    compactMedia.addListener(scheduleLayout);
  }

  syncDialogState();
  syncLayout();
})();
