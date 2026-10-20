"""Small, checked transformations of the upstream assets, served in place."""
import mimetypes
from pathlib import Path
import re

from starlette.responses import Response

STATIC = Path(__file__).resolve().parents[1] / "vendor/shared-craft-room/src/shared_craft_room/static"
ASSETS = Path(__file__).with_name("assets")


def replace(text, before, after):
    if before not in text:
        raise ValueError("上游页面已变化，需重新核验适配")
    return text.replace(before, after)


def function(text, name, replacement):
    # Upstream functions start at column zero; don't match nested statements.
    pattern = r"^function " + name + r"\([\s\S]*?(?=\n(?:async function|function|const |let |\$\(|document\.))"
    result, count = re.subn(pattern, lambda _: replacement.rstrip(), text, count=1, flags=re.M | re.S)
    if count != 1:
        raise ValueError("上游多人界面函数已变化: " + name)
    return result


def page(name):
    text = (STATIC / (name + ".html")).read_text()
    if name == "sketch":
        text = text.replace("对方", "伙伴")
    if name == "beads":
        text = text.replace("对方", "伙伴")
        text = replace(text, '<span id="box-count">',
                       '<select id="stock-actor" aria-label="查看伙伴豆盒" hidden></select><span id="box-count">')
    if name == "crafts":
        text = replace(text, "</body>", (ASSETS / "share.html").read_text() + "</body>")
    text = replace(text, "</head>", '<link rel="stylesheet" href="../assets/platform.css"></head>')
    return Response(text, media_type="text/html")


def asset(name):
    if Path(name).name != name or not name:
        return Response(status_code=404)
    path = ASSETS / name if name == "platform.css" else STATIC / name
    if not path.is_file():
        return Response(status_code=404)
    raw = path.read_bytes()
    if name in {"sketch.js", "beads.js", "crafts.js"}:
        text = raw.decode()
        text = replace(text, "`/api/${encodeURIComponent(key)}`", "'/shared-craft/api'")
        if name == "sketch.js":
            text = replace(text, "action:'clear',width:", "action:'clear',confirm:true,width:")
            text = function(text, "showPresence", """
function showPresence(entries=[]){
  document.querySelector('#presence').textContent=entries.length
    ? entries.map(p=>`${p.name}：${p.action==='drawing'&&p.age<8?'正在动笔':p.action==='selecting'&&p.age<20?'正在选色':'在画本里'}`).join(' · ')
    : '等待伙伴进入画本……';
}""")
            text = text.replace("你和对方画在同一页上", "大家画在同一页上")
        if name == "beads.js":
            text = function(text, "showBeadPresence", """
function showBeadPresence(entries=[]){
  const box=$('#bead-presence');box.hidden=false;
  box.textContent=current?.mode!=='together'
    ? (current?.mode==='separate'?'各自拼模式 · 这里是你自己的豆盒和选色':'惊喜豆板 · 揭晓前保密')
    : entries.length ? entries.map(p=>`${p.name}：${p.action==='selecting'?'选色':p.action==='placing'?'放豆':p.action==='taking'?'取豆':'在豆板旁'}${COLOR_NAMES[p.color]?' '+COLOR_NAMES[p.color]:''}`).join(' · ') : '等待伙伴进入豆板……';
}""")
            text = function(text, "inventoryView", """
function inventoryView(){
  const inventories=current?.inventories||{},own=inventories[me?.id]||{},select=$('#stock-actor');
  const others=current?.mode==='together'?participants.filter(p=>p.id!==me?.id):[];
  const previous=select.value;
  select.replaceChildren(...others.map(p=>new Option(p.display_name,p.id)));
  if(others.some(p=>p.id===previous))select.value=previous;
  select.hidden=!others.length;
  select.onchange=()=>{renderProgress();syncSelectionClasses()};
  return {own,other:select.value?(inventories[select.value]||{}):null};
}""")
            text = text.replace("对方${other[color]??80}", "TA${other[color]??80}")
            text = text.replace(" · 对方 ${otherTotal}", " · TA ${otherTotal}")
            text = replace(text, "actor!==me?.id).map", "actor===$('#stock-actor').value).map")
            text = text.replace("对方拿了一块新豆板", "伙伴拿了一块新豆板")
        if name == "crafts.js":
            text = replace(text, "let choosing = null;", "let choosing = null;\nlet pendingShares = [];")
            text = replace(text, "${actionButtons(item, selectId)}</div>",
                           '${actionButtons(item, selectId)}<button data-share="${item.id}">${pendingShares.some(p=>p.item===item.id) ? "确认分享" : "分享给作者"}</button></div>')
            text = replace(text, "function bind() {", """function bind() {
  document.querySelectorAll('[data-share]').forEach(button => button.onclick = () => openShare(button.dataset.share));""")
            text = replace(text, "items = data.items;", "items = data.items;\n  pendingShares = data.pending_shares || [];")
            text += "\n" + (ASSETS / "share.js").read_text()
        raw = text.encode()
    elif name == "beads-v2.css":
        raw = raw.replace('content:"对方"'.encode(), 'content:"TA"'.encode())
    return Response(raw, media_type=mimetypes.guess_type(name)[0] or "application/octet-stream")
