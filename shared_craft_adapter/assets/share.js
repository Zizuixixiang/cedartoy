let selectedShare = null, shareFocus = null;
async function openShare(id) {
  try {
    selectedShare = {item: id};
    shareFocus = document.activeElement;
    $('#share-image').src = `${base}/gallery/${id}.png`;
    $('#share-note').value = '';
    $('#share-status').textContent = '';
    $('#share-dialog').showModal();
  } catch (error) { $('#status').textContent = error.message; }
}
function closeShare() { $('#share-dialog').close(); shareFocus?.focus(); }
$('#share-cancel').onclick = closeShare;
$('#share-dialog').addEventListener('close', () => shareFocus?.focus());
async function submitShare() {
  const buttons = [...$('#share-form').querySelectorAll('button')];
  buttons.forEach(b => b.disabled = true);
  try {
    const result = await req(`${base}/shares`, {
      method: 'POST',
      body: JSON.stringify({
        item_id: selectedShare.item, note: $('#share-note').value
      })
    });
    closeShare();
    openDrawer();
    $('#status').textContent = result.status === 'shared' ? '已分享给作者' : '未分享';
  } catch (error) { $('#share-status').textContent = error.message; }
  finally { buttons.forEach(b => b.disabled = false); }
}
$('#share-form').onsubmit = event => { event.preventDefault(); submitShare(); };
