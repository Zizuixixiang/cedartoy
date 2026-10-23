"""Page-local routing for the two upstream UIs that use shared login cookies."""

import json


def page_slot_script(prefix):
    """Prepend before upstream code; player is routing input, never authority."""
    return ("""const cedarPageSlotUrl = (() => {
  const prefix = PREFIX;
  const player = new URL(location.href).searchParams.get('player') || '';
  function scopedUrl(value) {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin || !(url.pathname === prefix ||
        url.pathname === prefix + '/' || url.pathname.startsWith(prefix + '/api/'))) return null;
    // Never recover a missing page target from another tab's cookie/storage.
    url.searchParams.set('player', player);
    return url.href;
  }
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, options) => {
    const url = scopedUrl(input instanceof Request ? input.url : input);
    return nativeFetch(url ? (input instanceof Request ? new Request(url, input) : url) : input, options);
  };
  // Export links and internal navigation also need the page's target, including
  // middle-click/copy-link. Do not change external links or static downloads.
  function pinLink(link) {
    const url = scopedUrl(link.href);
    if (url && link.href !== url) link.href = url;
  }
  function pinLinks(root) {
    if (root.matches?.('a[href]')) pinLink(root);
    root.querySelectorAll?.('a[href]').forEach(pinLink);
  }
  pinLinks(document);
  new MutationObserver(records => records.forEach(record => {
    if (record.type === 'attributes') pinLinks(record.target);
    else record.addedNodes.forEach(pinLinks);
  })).observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ['href']});
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (link) pinLink(link);
  }, true);
  return scopedUrl;
})();
""".replace("PREFIX", json.dumps(prefix), 1)).encode("utf-8")
