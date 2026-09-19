const app = document.querySelector('#app');

async function api(path, options = {}) {
  return {path, options};
}

async function showHome() {
  const {sessions} = await api('/api/sessions');
  app.innerHTML = `<section>
    <button id="mcp-setup">建立 AI 連接網址</button>
    <div class="cloud-save-note" role="note"><strong>雲端存檔，頁面可以關閉</strong><p>權威存檔保存在站點的雲端資料庫。MCP 連接完成後，人不必守在頁面前；小機仍可使用有效的專屬網址繼續遊玩。</p></div>
    <a href="/downloads/detroit_blind_host_windows_v9.zip">下載</a>
    <details class="usage-note"><summary>三種玩法與費用差異</summary><div class="tiny"><p><strong>自接 API＋MCP：</strong>第三方中轉是否命中快取無法保證。</p></div></details>
    <p class="tiny">按上方按鈕取得這個瀏覽器專屬的完整 MCP 網址。網址等同存檔鑰匙，請勿公開分享；換手機、換瀏覽器或清除網站資料前，請先匯出完整存檔。<strong>完整存檔是備份，不是交接卡，請勿交給盲玩的 AI 閱讀。</strong>每人最多 12 個存檔。</p><p class="tiny">非商用實驗 · 劇情與原始程式來自 <a href="https://github.com/Baba88611/detroit-ai-player" target="_blank" rel="noopener noreferrer">Baba88611</a>；本主持台為改編版本。劇情資料依 <a href="https://github.com/Baba88611/detroit-ai-player/blob/main/docs/legal/CC-BY-NC-4.0.txt" target="_blank" rel="noopener noreferrer">CC BY-NC 4.0</a> 授權。</p>
  </section>`;
  return sessions;
}

async function showMcpSetup() {
  const result = await api('/api/mcp-connection', {method: 'POST', body: JSON.stringify({action: 'create'})});
  const dialog = document.createElement('div');
  dialog.innerHTML = `<p>${result.mcp_url}</p>`;
  document.body.append(dialog);
}

function showDeleteDialog(id, name) {
  return api(`/api/delete?id=${encodeURIComponent(id)}`, {method: 'POST', body: name});
}

async function loadSession(id) {
  try { render(await api(`/api/session?id=${encodeURIComponent(id)}`)); }
  catch (error) { notify(error.message); }
}

function render(data) {
  app.innerHTML = `<button id="home">回存檔首頁</button>`;
  document.querySelector('#home').addEventListener('click', showHome);
  return data;
}

showHome().catch(error => { app.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`; });
