// Inserted inside the SHA-checked upstream framer closure by ui.framer_script.
// Keep downloads in their own user gesture; never start a POST after a download.
const cloudButton = $('#framer-export'), galleryButton = $('#framer-gallery');
const transfer = document.createElement('div');
transfer.className = 'framer-transfer';
transfer.innerHTML = '<button id="framer-download" type="button">下载 PNG</button>' +
  '<div role="status" aria-live="polite" aria-atomic="true">' +
  '<p id="framer-cloud-status"></p><p id="framer-download-status"></p>' +
  '<p id="framer-draft-status"></p></div>';
$('.framer-panel').appendChild(transfer);
const downloadButton = $('#framer-download');
let transferBusy = false, framerView = 0, editVersion = 0;
const cloudStatus = text => { $('#framer-cloud-status').textContent = '云端：' + text; };
const downloadStatus = text => { $('#framer-download-status').textContent = 'PNG：' + text; };
const nativeSaveDraft = saveDraft, nativeScheduleSave = scheduleSave;
saveDraft = function() {
  const saved = nativeSaveDraft();
  $('#framer-draft-status').textContent = saved
    ? '草稿：已保存在本机' : '草稿：仅在当前页面，请勿关闭';
  return saved;
};
function framerEdited() {
  editVersion++;
  galleryButton.hidden = true;
  if (!transferBusy) cloudStatus('构图已修改，需保存');
}
scheduleSave = function() { framerEdited(); nativeScheduleSave(); };
const nativeReset = $('#framer-reset').onclick;
$('#framer-reset').onclick = () => { nativeReset(); framerEdited(); };
const nativeOpenFramer = window.openBeadFramer;
window.openBeadFramer = async value => {
  framerView++;
  await nativeOpenFramer(value);
  cloudStatus(value.craft?.decorated ? '已有成品，可保存当前构图' : '尚未保存');
  downloadStatus('尚未下载');
  cloudButton.textContent = '保存到云端';
  $('#framer-draft-status').textContent = '草稿：保留在当前页面';
};
function setTransferBusy(value) {
  transferBusy = value;
  cloudButton.disabled = downloadButton.disabled = galleryButton.disabled = value;
}
function transferDiagnostic(operation, detail) {
  // No identity, URL, image, draft, cookie, or response text in diagnostics.
  console.info('[CedarToy framer]', {operation, time: new Date().toISOString(), ...detail});
}
cloudButton.textContent = '保存到云端';
cloudButton.onclick = async () => {
  if (transferBusy || !board) return;
  const target = board, view = framerView, version = editVersion;
  const started = performance.now();
  let stage = 'compose', status = null, bytes = 0, timer, timedOut = false;
  let wasHidden = document.visibilityState !== 'visible';
  const hidden = () => { if (document.visibilityState !== 'visible') wasHidden = true; };
  const pageHide = () => { wasHidden = true; };
  document.addEventListener('visibilitychange', hidden);
  window.addEventListener('pagehide', pageHide);
  setTransferBusy(true);
  cloudButton.textContent = '正在保存…';
  clearTimeout(saveTimer);
  saveDraft(); // Flush the debounce before any async work, including failures.
  cloudStatus('正在保存，请保持页面打开');
  try {
    const piece = objects.find(o => o.kind === 'piece');
    const exportCanvas = composedCanvas(), dataUrl = exportCanvas.toDataURL('image/png');
    const shell = piece?.shell, keyring = piece?.keyring;
    let pieceUrl;
    // The original exporter mutates these flags; restore them even on an error.
    try { pieceUrl = pieceDataURL(); }
    finally { if (piece) { piece.shell = shell; piece.keyring = keyring; } draw(true); }
    const hasPouch = Boolean(shell);
    const body = JSON.stringify({action:'decoration', data_url:dataUrl,
      piece_data_url:pieceUrl, has_pouch:hasPouch});
    bytes = new Blob([body]).size;
    stage = 'request';
    const controller = new AbortController();
    timer = setTimeout(() => { timedOut = true; controller.abort(); }, 45000);
    transferDiagnostic('cloud', {stage, bytes, visibility: document.visibilityState});
    const response = await fetch(`/shared-craft/api/bead-boards/${target.id}`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body, signal:controller.signal
    });
    status = response.status;
    stage = 'response';
    if (!response.ok) throw Error('HTTP');
    const data = await response.json();
    if (data?.ok !== true) throw Error('Rejected');
    stage = 'confirmed';
    target.craft.decorated = true;
    target.craft.has_pouch = hasPouch;
    if (view === framerView && board === target) {
      const unchanged = version === editVersion;
      cloudStatus(unchanged ? '已保存，可收入作品间' : '已保存提交时构图；新修改待保存');
      galleryButton.hidden = !unchanged;
      document.querySelector('#exhibit').hidden = false;
    }
  } catch (error) {
    if (view === framerView && board === target) {
      const message = stage === 'compose' ? '图片生成失败，请重试'
        : status === 401 || status === 403 ? '登录或权限失效，请检查登录'
        : status === 413 ? '图片过大，请减少装饰后重试'
        : status && status >= 400 ? `保存失败（HTTP ${status}），可重试`
        : timedOut ? '等待超时，结果未确认，可重试'
        : '未确认保存结果，请重试';
      cloudStatus(message);
      cloudButton.textContent = '重试云端保存';
    }
    transferDiagnostic('cloud-error', {stage, status, bytes, timedOut, wasHidden,
      online:navigator.onLine, visibility:document.visibilityState,
      elapsedMs:Math.round(performance.now() - started), errorName:error.name});
  } finally {
    clearTimeout(timer);
    document.removeEventListener('visibilitychange', hidden);
    window.removeEventListener('pagehide', pageHide);
    transferDiagnostic('cloud-end', {stage, status, bytes, wasHidden,
      elapsedMs:Math.round(performance.now() - started)});
    setTransferBusy(false);
    if (cloudButton.textContent === '正在保存…') cloudButton.textContent = '保存到云端';
  }
};
downloadButton.onclick = async () => {
  if (transferBusy || !board) return;
  const view = framerView, title = board.title;
  setTransferBusy(true);
  clearTimeout(saveTimer);
  saveDraft();
  downloadStatus('正在生成…');
  try {
    await downloadCanvas(composedCanvas(), `${title || '拼豆成品'}-完整成品.png`);
    if (view === framerView) downloadStatus('已发起下载，请查看浏览器');
  } catch (error) {
    if (view === framerView) downloadStatus('生成或下载失败，可重试');
    transferDiagnostic('download-error', {errorName:error.name});
  } finally { setTransferBusy(false); }
};
