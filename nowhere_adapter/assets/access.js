/* Reuse the platform login; no new tokens, session store or URL credentials. */
(async () => {
  const url = new URL(location.href);
  url.searchParams.delete('token');
  history.replaceState(null, '', url.pathname + url.search);
  const message = document.getElementById('access-message');
  const home = document.getElementById('access-home');
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
  if (location.protocol !== 'https:' && !local) {
    url.protocol = 'https:';
    const link = document.getElementById('access-secure');
    link.href = url.href;
    link.hidden = false;
    home.href = new URL('/#nowhere', url).href;
    return; // Do not read or send an HTTP origin's stored credential.
  }
  if (document.body.dataset.recover !== 'true') return;
  let token;
  try { token = localStorage.getItem('cedartoy_token'); } catch (_) { return; }
  if (!token) return;
  try {
    const result = await fetch(url.pathname + url.search, {
      headers: {Authorization: `Bearer ${token}`}, credentials: 'same-origin', cache: 'no-store'
    });
    if (!result.ok) {
      if (result.status === 403) message.textContent = '当前账号无法查看这个旅程，请返回首页选择已绑定小机的存档槽位。';
      else if (result.status !== 401) message.textContent = '旅程暂时无法打开，请返回首页检查存档或稍后重试。';
      return;
    }
    // Verify the cookie before reloading. A browser that blocks cookies gets
    // a usable prompt, never an endless authentication/redirect loop.
    const check = await fetch(url.pathname + url.search, {credentials: 'same-origin', cache: 'no-store'});
    if (check.ok) location.replace(url.pathname + url.search);
    else message.textContent = '浏览器未能保存登录状态。请允许本站 Cookie，再从首页进入旅程。';
  } catch (_) {
    message.textContent = '暂时无法恢复登录，请返回首页重试。';
  }
})();
