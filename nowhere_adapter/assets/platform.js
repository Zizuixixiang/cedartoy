/* Small same-origin bridge around the upstream observer UI. */
(() => {
  const player = new URL(location.href).searchParams.get('player') || '';
  const nativeFetch = window.fetch.bind(window);
  const endpoints = /^\/(state|history|marks|sightings|postcards|messages|message|open_door|walk|listen|look_around|ask|postcard(?:\/\d+(?:\/reply)?)?|where_am_i|continue|mark|walk_to|wait)$/;
  const status = message => {
    const node = document.getElementById('platform-status');
    if (node) { node.textContent = message; node.hidden = !message; }
  };
  document.addEventListener('click', event => {
    // Desktop retains the author's _blank stream link.
    if (!matchMedia('(pointer: coarse)').matches ||
        !/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)) return;
    const link = event.target.closest?.('#trail a.traillink');
    if (!link) return;
    let url;
    try { url = new URL(link.href); } catch (_) { return; }
    if (!/^https?:$/.test(url.protocol)) return;
    event.preventDefault();
    location.assign(`/nowhere/radio?player=${encodeURIComponent(player)}&url=${encodeURIComponent(url.href)}`);
  });
  function privateImages(value) {
    if (!value || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(privateImages);
    const result = {...value};
    if ('front_img' in result) {
      result.front_img = /^\/static\/postcards\/card_\d+\.png$/.test(result.front_img)
        ? `/nowhere${result.front_img}?player=${encodeURIComponent(player)}` : null;
    }
    for (const key of Object.keys(result)) if (typeof result[key] === 'object') result[key] = privateImages(result[key]);
    return result;
  }
  window.fetch = async (input, options = {}) => {
    if (typeof input !== 'string' || !endpoints.test(input)) return nativeFetch(input, options);
    const headers = new Headers(options.headers || {});
    if (options.method === 'DELETE') {
      headers.set('Content-Type', 'application/json');
      options = {...options, body: JSON.stringify({confirm: true})};
    }
    const response = await nativeFetch(`/nowhere${input}?player=${encodeURIComponent(player)}`, {...options, headers, credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        location.replace(`/nowhere/?player=${encodeURIComponent(player)}`);
      }
      const error = await response.json().catch(() => ({}));
      status(error.error || '旅程读取失败，请稍后重试。');
      throw new Error(error.error || '乌有乡请求失败');
    }
    status('');
    return new Response(JSON.stringify(privateImages(await response.json())), {status: response.status, headers: {'Content-Type': 'application/json'}});
  };
})();
