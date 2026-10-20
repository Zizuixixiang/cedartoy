let selectedShare = null, shareFocus = null;
async function openShare(id) {
  try {
    const latest = await req(`${base}/gallery`);
    pendingShares = latest.pending_shares || [];
    const pending = pendingShares.find(p => p.item === id);
    selectedShare = {item: id, pending};
    shareFocus = document.activeElement;
    $('#share-image').src = pending ? `${base}/shares/${pending.id}.png` : `${base}/gallery/${id}.png`;
    $('#share-note').value = pending?.note || '';
    $('#share-note').readOnly = Boolean(pending);
    $('#share-decline').hidden = !pending;
    $('#share-status').textContent = '';
    $('#share-dialog').showModal();
  } catch (error) { $('#status').textContent = error.message; }
}
function closeShare() { $('#share-dialog').close(); shareFocus?.focus(); }
$('#share-cancel').onclick = closeShare;
$('#share-dialog').addEventListener('close', () => shareFocus?.focus());
async function submitShare(approve) {
  const pending = selectedShare.pending;
  const buttons = [...$('#share-form').querySelectorAll('button')];
  buttons.forEach(b => b.disabled = true);
  try {
    const result = await req(pending ? `${base}/shares/${pending.id}` : `${base}/shares`, {
      method: 'POST',
      body: JSON.stringify(pending ? {confirm: approve} : {
        item_id: selectedShare.item, note: $('#share-note').value, confirm: true
      })
    });
    pendingShares = (await req(`${base}/gallery`)).pending_shares || [];
    closeShare();
    openDrawer();
    $('#status').textContent = result.status === 'pending' ? '等待人类确认' : result.status === 'shared' ? '已分享给作者' : '未分享';
  } catch (error) { $('#share-status').textContent = error.message; }
  finally { buttons.forEach(b => b.disabled = false); }
}
$('#share-form').onsubmit = event => { event.preventDefault(); submitShare(true); };
$('#share-decline').onclick = () => submitShare(false);
