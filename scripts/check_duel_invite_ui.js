/* Run the real app in a DOM with stubbed transport; no server/production data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('vendor/duel/app/static/index.html','utf8');
const js = fs.readFileSync('vendor/duel/app/static/app.js','utf8');
const css = fs.readFileSync('vendor/duel/app/static/styles.css','utf8');
const dom = new JSDOM(html, {url:'https://toy.cedarstar.org/duel/',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;
const style=w.document.createElement('style');
style.textContent=css;
w.document.head.appendChild(style);
assert.ok(style.sheet, 'stylesheet parses');
const visible=(element)=> {
  for (let node=element; node; node=node.parentElement) {
    if (w.getComputedStyle(node).display==='none') return false;
  }
  return true;
};
const button=(action)=>w.document.querySelector(`#inviteRoomPanel [data-action="${action}"]`);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
w.fetch=async()=>({ok:true,json:async()=>({bound:false})});
w.HTMLElement.prototype.scrollIntoView=()=>{};
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;};
w.eval(js.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '') + '\nwindow.run = (source) => eval(source);');
w.run(`identity={human_player_id:'human:1',human_name:'乙',machines:[],games:[{game_type:'xiangqi',display_name:'象棋',allowed_player_counts:[2],supports_npcs:false}]};`);
// Active invitations have one visible tag; ordinary/ended cards keep their hierarchy.
w.roomSummaries=['waiting','playing','finished'].map((status,i)=>({
  room_id:`INVITE${i}`,room_kind:'invite',status,game_type:'uno',game_name:'UNO',
  participant_names:['甲','乙','小机','朋友'],participant_count:4,target_player_count:4,
}));
w.roomSummaries.push({...w.roomSummaries[1],room_id:'BOUND',room_kind:'bound',ai_name:'小机'});
w.run('renderRooms(roomSummaries)');
const roomCards=[...w.document.querySelectorAll('#roomList .room-card')];
for (const card of roomCards.slice(0,2)) {
  assert.ok(card.classList.contains('invite-active'));
  assert.equal(card.querySelector('.room-status-badge.room-invite-badge').textContent,'邀请房');
  assert.equal((card.textContent.match(/邀请房/g)||[]).length,1,'no duplicate invitation label');
  assert.ok(card.querySelector('.room-meta').textContent.includes('4/4'));
}
assert.ok(roomCards[2].classList.contains('ended'));
for (const card of roomCards.slice(2)) {
  assert.equal(card.querySelector('.room-invite-badge'),null);
  assert.equal(card.classList.contains('invite-active'),false);
}
const participants=[{player_id:'human:2',display_name:'甲',role:'human',participant_kind:'human',token:'X',seat_index:0,handle:'2',active:true,activity_state:'active'}, {player_id:'human:1',display_name:'乙',role:'human',participant_kind:'human',token:'O',seat_index:1,handle:'1',active:true,activity_state:'active'}, {player_id:'123',display_name:'机',role:'ai',participant_kind:'bound_machine',token:'P3',seat_index:2,handle:'123',active:true,activity_state:'active'}];
w.fixture={room_id:'ABCDEFGH',room_kind:'invite',status:'playing',game_type:'xiangqi',viewer:{player_id:'human:1',seat:1,token:'O'},participants:participants.slice(0,2),current_player_id:'human:1',board_state:{}};
w.run('room=fixture;');
assert.equal(w.run('canHumanMove()'),true,'second human controls own turn');
assert.equal(w.run('participantFor("human").player_id'),'human:1');
w.fixture.participants=participants;
assert.deepEqual(Array.from(w.run('relativeParticipantsFor(room).map(p=>p.player_id)')),['human:1','123','human:2']);
const input=w.document.getElementById('chatInput');
input.value='@'; input.setSelectionRange(1,1); input.dispatchEvent(new w.Event('input'));
const options=w.document.querySelectorAll('#mentionOptions button');
assert.equal(options.length,2); assert.ok(!Array.from(options).some(b=>b.textContent.includes('乙 · ID 1')));
options[1].click(); assert.equal(input.value,'@123 ');
const menu=w.document.getElementById('mentionOptions');
assert.equal(menu.parentElement,input.parentElement,'menu anchors to the composer');
assert.equal(w.getComputedStyle(menu).position,'absolute');
assert.equal(w.getComputedStyle(input.parentElement).position,'relative');
assert.equal(w.getComputedStyle(options[0]).fontFamily,'system-ui, sans-serif');
assert.equal(w.getComputedStyle(options[0]).fontSize,'12px');
assert.equal(menu.querySelector('img'),null);
assert.equal(options[1].querySelector('.mention-id').textContent,' · ID 123');
const typeMention=()=>{input.value='@'; input.focus(); input.setSelectionRange(1,1); input.dispatchEvent(new w.Event('input'));};
const key=name=>input.dispatchEvent(new w.KeyboardEvent('keydown',{key:name,bubbles:true,cancelable:true}));
typeMention();
key('ArrowDown'); key('ArrowDown');
assert.equal(input.getAttribute('aria-activedescendant'),'mention-option-1');
assert.equal(w.document.activeElement,input,'keyboard remains focused on input');
key('Enter'); assert.equal(input.value,'@123 ');
assert.equal(input.getAttribute('aria-expanded'),'false');
typeMention(); key('ArrowUp'); key('Tab'); assert.equal(input.value,'@123 ');
typeMention(); key('ArrowDown'); key('ArrowUp');
assert.equal(input.getAttribute('aria-activedescendant'),'mention-option-1');
key('Escape'); assert.equal(input.value,'@'); assert.equal(input.getAttribute('aria-expanded'),'false');
typeMention(); input.blur(); assert.ok(menu.classList.contains('hidden'));
// Ordinary rooms preserve plain chat and never expose invitation completion.
w.fixture.room_kind='bound';w.run('syncChatRoomMode()');typeMention();
assert.ok(menu.classList.contains('hidden'));
assert.equal(menu.children.length,0);
assert.equal(input.getAttribute('role'),null);
assert.ok(w.document.querySelector('.chat-hint').classList.contains('hidden'));
w.fixture.room_kind='invite';w.run('syncChatRoomMode()');typeMention();
assert.equal(menu.children.length,2);
assert.equal(input.getAttribute('role'),'combobox');
assert.ok(!w.document.querySelector('.chat-hint').classList.contains('hidden'));
key('Escape');
// Real xiangqi renderer reverses displayed coordinates, submits original ones.
w.fixture.participants=participants.slice(0,2);
w.fixture.board_state={rows:10,cols:9,board:Array.from({length:10},()=>Array(9).fill(null)),legal_moves:[{from_row:0,from_col:0,to_row:1,to_col:0}],turn_color:'b'};
w.fixture.board_state.board[0][0]='b:r';
w.run('selectedXiangqiCell=null; renderXiangqiBoard(document.getElementById("board"),room.board_state);');
const board=w.document.getElementById('board');
assert.equal(board.dataset.viewColor,'b');
const blackRook=board.querySelector('[data-move-row="0"][data-move-col="0"]');
assert.ok(blackRook,'authoritative black rook coordinate remains in DOM');
blackRook.click();
board.querySelector('[data-move-row="1"][data-move-col="0"]').click();
assert.deepEqual(JSON.parse(w.run('JSON.stringify(pendingMove)')),{from_row:0,from_col:0,to_row:1,to_col:0});
// Pregame invite renders with empty state, never invokes a game board renderer.
w.fixture={room_id:'ABCDEFGH',status:'waiting',game_type:'xiangqi',game_name:'象棋',room_kind:'invite',target_player_count:2,room_ready:true,initiator_player_id:'human:1',invite_code:'ABCDEF123456',invite_link:'/duel/?invite=ABCDEF123456',viewer:{player_id:'human:1',seat:1},participants:participants.slice(0,2),board_state:{},private_state:{},rules_text:'规则',revision:2};
w.run('renderGame(fixture,"",[]);');
const waitingFixture=structuredClone(w.fixture);
const panel=w.document.getElementById('inviteRoomPanel');
assert.equal(panel.parentElement.id,'gameView','waiting module is outside battle stage');
assert.ok(visible(panel));
assert.ok(w.document.getElementById('gameView').classList.contains('invite-waiting-view'));
const hiddenDuringWait=['.game-header','#gameMessage','#resultBanner','#battleStage','.battle-main-column','#board','#aiAvatar','#humanAvatar','#viewerParticipantSlot','#privateStatePanel','#gameChatArea','#chatInput','#sendMessageButton','#recentChatFeed','#historyDrawerTab'];
for (const selector of hiddenDuringWait) assert.equal(visible(w.document.querySelector(selector)),false,selector);
assert.equal(Array.from(w.document.getElementById('gameView').children).filter(visible).length,1);
assert.equal(w.document.getElementById('board').children.length,0);
assert.equal(w.getComputedStyle(panel).maxWidth,'560px');
assert.equal(w.getComputedStyle(panel).marginLeft,'auto');
assert.equal(w.getComputedStyle(w.document.getElementById('gameView')).paddingLeft,'14px');
assert.ok(panel.textContent.includes('乙 · ID 1'));
assert.ok(!/human:|~|@/.test(panel.textContent));
assert.equal(button('关闭邀请房').textContent,'×');
assert.equal(button('关闭邀请房').getAttribute('aria-label'),'关闭邀请房');
assert.equal(w.getComputedStyle(button('关闭邀请房')).position,'absolute');
assert.equal(button('开始游戏').disabled,false);
assert.equal(panel.querySelector('#inviteStartHint'),null,'owner has no guest hint');
assert.equal(button('NPC 补满并开始'),null);
const copied=[];
Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async text=>copied.push(text)}});
button('复制邀请码').click(); await flush();
button('复制邀请链接').click(); await flush();
assert.deepEqual(copied,['ABCDEF123456','https://toy.cedarstar.org/duel/?invite=ABCDEF123456']);
assert.equal(panel.querySelector('.invite-code-line').textContent,'ABCDEF123456');
w.fixture={...waitingFixture,viewer:{player_id:'human:2',seat:0}};
w.run('renderGame(fixture,"",[]);');
assert.equal(button('关闭邀请房'),null);
assert.equal(button('开始游戏').disabled,true);
assert.equal(panel.querySelector('#inviteStartHint').textContent,'等待房主开始游戏');
assert.ok(visible(panel.querySelector('#inviteStartHint')));
assert.equal(button('开始游戏').getAttribute('aria-describedby'),'inviteStartHint');
assert.equal(button('NPC 补满并开始'),null,'guest cannot fill even when full');
w.fixture={...w.fixture,target_player_count:4,room_ready:false};
w.run('renderGame(fixture,"",[]);');
assert.equal(button('开始游戏').disabled,true);
assert.ok(visible(panel.querySelector('#inviteStartHint')),'guest hint also visible before full');
assert.equal(button('NPC 补满并开始'),null);
w.fixture={...waitingFixture,target_player_count:4,household_count:1,participants:[participants[1]],room_ready:false};
w.run('renderGame(fixture,"",[]);');
assert.equal(button('开始游戏').disabled,true);
assert.equal(panel.querySelector('#inviteStartHint'),null,'unfilled owner has no guest hint');
assert.equal(button('NPC 补满并开始'),null);
w.fixture={...waitingFixture,target_player_count:4,room_ready:false};
w.run('renderGame(fixture,"",[]);');
assert.equal(button('NPC 补满并开始'),null,'unsupported game cannot fill NPC seats');
w.run('identity.games[0].supports_npcs=true; identity.games[0].uses_local_npc_strategy=false;');
w.run('renderGame(fixture,"",[]);');
assert.equal(button('NPC 补满并开始'),null,'unavailable provider cannot fill NPC seats');
w.run('identity.games[0].uses_local_npc_strategy=true; renderGame(fixture,"",[]);');
assert.ok(button('NPC 补满并开始'));
assert.equal(button('开始游戏').disabled,true,'unfilled start stays visible beside NPC alternative');
assert.equal(button('开始游戏').classList.contains('secondary'),false,'disabled start remains the primary action');
assert.ok(button('NPC 补满并开始').classList.contains('secondary'));
w.fixture={...waitingFixture,participants:[participants[1],{player_id:'npc:test',display_name:'NPC',participant_kind:'system_npc',handle:'forged'}],room_ready:false};
w.run('renderGame(fixture,"",[]);');
assert.equal(button('NPC 补满并开始'),null,'NPC is not a real invitee');
input.value='@'; input.setSelectionRange(1,1); input.dispatchEvent(new w.Event('input'));
assert.equal(w.document.querySelectorAll('#mentionOptions button').length,0,'NPC cannot be mentioned');
// Stale drawers/modals from a previous game must not overlay the waiting card.
w.run('openHistory(); openRules(); openResultModal("旧结果"); renderGame(fixture,"",[]);');
assert.equal(w.document.getElementById('historyDrawer').getAttribute('aria-hidden'),'true');
assert.equal(w.document.getElementById('rulesScrim').getAttribute('aria-hidden'),'true');
assert.equal(visible(w.document.getElementById('resultModal')),false);
// The start action restores the full game and chat, even without a revision change.
w.fixture=waitingFixture;
w.run('renderGame(fixture,"",[]);');
const requests=[];
w.fetch=async(url,options)=>{
  requests.push({url,body:JSON.parse(options.body)});
  return {ok:true,json:async()=>({room:{...waitingFixture,status:'playing',game_type:'tictactoe',board_state:{board:[['','',''],['','',''],['','','']]}},timeline:[]})};
};
w.run('startRoomPolling=()=>{};');
button('开始游戏').click(); await flush();
assert.equal(requests[0].url,'/duel/api/rooms/ABCDEFGH/start');
assert.equal(requests[0].body.fill_with_npcs,false);
for (const selector of ['.game-header','#battleStage','#gameChatArea','#chatInput','#sendMessageButton','#historyDrawerTab']) assert.ok(visible(w.document.querySelector(selector)),selector);
assert.equal(panel.parentElement.id,'gameView');
assert.equal(visible(panel),false,'playing invitation has no waiting card');
assert.equal(panel.children.length,0);
assert.equal(w.document.getElementById('sendMessageButton').disabled,false);
const playingFixture=structuredClone(w.run('room'));
const reclaimControl=w.document.getElementById('reclaimControl');
w.fixture={...playingFixture,timeout_takeover:true,current_player_id:'human:1'};
w.run('renderGame(fixture,"",[]);');
assert.equal(visible(reclaimControl),false,'enabled timeout alone is not active takeover');
w.fixture={...w.fixture,takeover_revision:1};
w.run('renderGame(fixture,"",[]);');
assert.equal(visible(reclaimControl),false,'completed takeover marker is not active work');
w.fixture={...w.fixture,viewer:{...w.fixture.viewer,temporary_takeover_active:true}};
w.run('renderGame(fixture,"",[]);');
assert.ok(visible(reclaimControl));
assert.ok(reclaimControl.closest('.game-toolbar-actions'));
assert.ok(reclaimControl.classList.contains('compact'));
assert.equal(visible(panel),false);
reclaimControl.click(); await flush();
assert.equal(requests.at(-1).url,'/duel/api/rooms/ABCDEFGH/reclaim');
assert.equal(visible(reclaimControl),false);
w.fixture={...w.fixture,current_player_id:'human:2'};
w.run('renderGame(fixture,"",[]);');
assert.equal(visible(reclaimControl),false,'another seat is not reclaimable');
w.fixture={...w.fixture,room_kind:'bound',current_player_id:'human:1'};
w.run('renderGame(fixture,"",[]);');
assert.equal(visible(reclaimControl),false,'ordinary game cannot show invitation takeover control');
// Filling and closing use the existing lifecycle endpoints.
w.fixture={...waitingFixture,target_player_count:4,room_ready:false};
w.run('renderGame(fixture,"",[]);');
button('NPC 补满并开始').click(); await flush();
assert.equal(requests.at(-1).body.fill_with_npcs,true);
w.run('backToLobby=async()=>{window.returnedToLobby=true;}; renderGame(fixture,"",[]);');
button('关闭邀请房').click(); await flush();
assert.equal(requests.at(-1).url,'/duel/api/rooms/ABCDEFGH/leave');
assert.equal(w.returnedToLobby,true);
w.run('renderGame({...room,status:"playing",game_type:"tictactoe",board_state:{board:[["","",""],["","",""],["","",""]]}},"",[]);');
// Legacy games retain the same stage and do not show invitation controls.
w.run('renderGame({...room,room_kind:"bound"},"",[]);');
assert.equal(visible(panel),false);
assert.ok(visible(w.document.getElementById('battleStage')));
assert.ok(visible(input));
assert.ok(html.includes('输入 @ 可选择房间成员；@小机会提醒它。'));
assert.equal((html.match(/id="aiPlayer"/g)||[]).length,1,'legacy create UI retained');
assert.ok(html.includes('/static/app.js?v=0.9.20'));
assert.ok(html.includes('/static/styles.css?v=0.9.17'));
assert.ok(html.includes('<option value="0" selected>关闭</option>'));
assert.ok(js.includes('if (mode === "create") $("inviteTimeout").value = "0";'));
assert.ok(!css.includes('invite-waiting-stage'),'obsolete waiting-stage patches removed');
// Render actual backend projections for every human viewer at a four-seat UNO table.
const {spawnSync} = require('node:child_process');
const generated = spawnSync('.venv/bin/python', ['-c', `
import tempfile,json
from pathlib import Path
from app import database,invites,framework
with tempfile.TemporaryDirectory(prefix="duel-ui-") as d:
 database.DB_PATH=Path(d)/"test.db"
 database.init_db()
 room=invites.create_invite("uno","human","u0",target_player_count=4)
 for n in range(1,4): invites.join_invite(room["invite_code"],"human",f"u{n}")
 room=invites.start_invite(room["room_id"],"human","u0")
 print(json.dumps([framework.project_room_for_viewer(room,p["player_id"]) for p in room["participants"]]))
`], {cwd:'vendor/duel', encoding:'utf8'});
assert.equal(generated.status,0,generated.stderr);
w.eval(fs.readFileSync('vendor/duel/app/static/game_ui_registry.js','utf8'));
w.eval(fs.readFileSync('vendor/duel/app/static/games/uno.js','utf8'));
for (const snapshot of JSON.parse(generated.stdout)) {
  w.fixture = snapshot;
  w.run('room=null; renderGame(fixture,"",[]);');
  const ownCards=Array.from(w.document.querySelectorAll('.uno-hand-scroll .uno-card'));
  assert.equal(ownCards.length,snapshot.private_state.hand.length);
  const actualIds=ownCards.map(c=>c.dataset.cardId);
  assert.deepEqual(actualIds,snapshot.private_state.hand.map(c=>c.id));
  const ownId=snapshot.viewer.player_id;
  assert.ok(!w.document.querySelector('.uno-opponents').textContent.includes('你的手牌'));
  const relative=Array.from(w.run('relativeParticipantsFor(room).map(p=>p.player_id)'));
  assert.equal(relative[0],ownId);
  assert.deepEqual(relative.slice(1), snapshot.participants.slice(snapshot.viewer.seat+1).concat(snapshot.participants.slice(0,snapshot.viewer.seat)).map(p=>p.player_id));
}
// Polling preserves a draft; switching tables must not send it elsewhere.
input.value='只发给上一桌的消息';
w.run('renderGame(fixture,"",[]);');
assert.equal(input.value,'只发给上一桌的消息');
assert.ok(!js.includes('chatReplyTo'));
assert.ok(!js.includes('reply_to'));
assert.equal(w.document.querySelector('#recentChatMessages button'), null);
w.fixture={...w.fixture,room_id:'NEWTABLE'};
w.run('renderGame(fixture,"",[]);');
assert.equal(input.value,'');
assert.equal(input.placeholder,'说点什么…');
assert.ok(w.document.getElementById('mentionOptions').classList.contains('hidden'));
// Shared chat renders the whole visible timeline in two- and multi-seat games.
w.chatEvents=Array.from({length:12},(_,i)=>({sequence:i+1,event_type:'message',is_public:true,
  sender:{player_id:'human:2',name:'朋友',role:'human'},text:`消息 ${i+1}`}));
for (const count of [2,3]) {
  w.run(`renderGame({...fixture,participants:fixture.participants.slice(0,${count})},"",chatEvents);`);
  assert.equal(elChat().children.length,12);
  assert.equal(elChat().firstElementChild.querySelector('p').textContent,'消息 1');
  assert.equal(elChat().querySelector('button'),null);
  assert.equal(w.getComputedStyle(elChat()).overflowY,'auto');
  assert.equal(w.getComputedStyle(elChat().parentElement).height,'134px');
}
function elChat(){return w.document.getElementById('recentChatMessages');}
assert.ok(!js.includes('RECENT_CHAT_LIMIT'));
assert.equal(w.getComputedStyle(menu).gridColumn,'1 / 2');
// JSDOM has no layout engine. Model a scrolling viewport explicitly, then run
// real renderGame/refreshRoom; Chromium checks actual geometry separately.
const chatList=elChat();
let scrollPosition=0;
Object.defineProperties(chatList, {
  clientHeight:{configurable:true,get:()=>100},
  scrollHeight:{configurable:true,get:()=>Math.max(100,chatList.children.length*24)},
  scrollTop:{configurable:true,get:()=>scrollPosition,set:value=>{scrollPosition=Math.max(0,Math.min(value,chatList.scrollHeight-100));}},
});
const originalRect=w.HTMLElement.prototype.getBoundingClientRect;
w.HTMLElement.prototype.getBoundingClientRect=function(){
  if (this.parentElement===chatList) {
    const top=[...chatList.children].indexOf(this)*24-chatList.scrollTop;
    return {top,bottom:top+24};
  }
  return originalRect.call(this);
};
const originalReplace=chatList.replaceChildren.bind(chatList);
chatList.replaceChildren=(...children)=>{originalReplace(...children);scrollPosition=0;};
w.run('renderGame({...fixture,room_id:"CHAT-SCROLL"},"",chatEvents)');
assert.equal(chatList.scrollTop,188,'first entry follows bottom');
chatList.scrollTop=55;
w.run('renderGame(room,"",chatEvents)');
assert.equal(chatList.scrollTop,55,'unchanged render retains reading position');
w.chatEvents.push({...w.chatEvents[0],sequence:13,text:'新消息'});
w.fetch=async()=>({ok:true,json:async()=>({room:w.run('room'),timeline:w.chatEvents})});
await w.run('refreshRoom({quiet:true,wait:true,generation:roomSyncGeneration})');
assert.equal(chatList.children.length,13);
assert.equal(chatList.scrollTop,55,'poll appends below the reader without shifting');
w.chatEvents=w.chatEvents.slice(1);
w.run('renderGame(room,"",chatEvents)');
assert.equal(chatList.scrollTop,31,'trimmed old message compensates by its height');
chatList.scrollTop=chatList.scrollHeight-chatList.clientHeight-10;
w.chatEvents.push({...w.chatEvents[0],sequence:14,text:'接着跟随'});
w.run('renderGame(room,"",chatEvents)');
assert.equal(chatList.scrollTop,212,'near-bottom reading follows incoming messages');
chatList.scrollTop=30;
w.run('renderGame({...room,room_id:"CHAT-SWITCH"},"",chatEvents)');
assert.equal(chatList.scrollTop,212,'switch defaults to bottom');
w.HTMLElement.prototype.getBoundingClientRect=originalRect;
chatList.replaceChildren=originalReplace;
for (const property of ['scrollHeight','scrollTop','clientHeight']) delete chatList[property];
// Web sends only message; targeted reminders use the handle in its text.
const sentMessages=[];
w.fetch=async(url,options)=>{
  sentMessages.push(JSON.parse(options.body));
  return {ok:true,json:async()=>({room:w.run('room'),timeline:w.chatEvents})};
};
input.value='@123 看这里';
await w.run('sendMessage()');
assert.deepEqual(sentMessages,[{message:'@123 看这里'}]);
// Reply is mention insertion only: canonical identity, existing text/caret,
// no transport until Send, no quotation state, and no NPC/self affordance.
w.replyFixture={...playingFixture,participants:[...participants,
  {player_id:'npc:test',display_name:'NPC',role:'ai',participant_kind:'system_npc',handle:'forged'}]};
w.replyFixture.participants[2]={...participants[2],player_id:'123:2'};
w.replyEvents=w.replyFixture.participants.map((p,i)=>({sequence:i+1,event_type:'message',is_public:true,
  sender:{player_id:p.player_id,name:p.display_name,role:p.role},text:'留言'}));
w.replyEvents.push({sequence:5,event_type:'message',is_public:true,sender:{player_id:'system',role:'system'},text:'系统通知'});
w.run('renderGame(replyFixture,"",replyEvents)');
const replies=elChat().querySelectorAll('.recent-chat-reply');
const replyStyle=w.getComputedStyle(replies[0]);
assert.equal(replyStyle.width,'28px');
assert.equal(replyStyle.height,'19px');
assert.equal(replyStyle.fontSize,'10px');
// JSDOM does not resolve var() in shorthands; Chromium checks rendered colors/borders.
const replyRule=[...style.sheet.cssRules].find(rule=>rule.selectorText==='.recent-chat-reply');
assert.equal(replyRule.style.getPropertyValue('border'),'1px solid var(--purple-dark)');
assert.equal(replyRule.style.getPropertyValue('background'),'var(--purple-light)');
assert.equal(replies.length,2,'only other real participants get reply');
assert.equal(elChat().querySelector('[data-sequence="2"] button'),null,'own message');
assert.equal(elChat().querySelector('[data-sequence="4"] button'),null,'NPC message');
assert.equal(elChat().querySelector('[data-sequence="5"] button'),null,'system message');
sentMessages.length=0;
input.value='前文 后文'; input.focus(); input.setSelectionRange(3,3);
const pointer=new w.Event('pointerdown',{bubbles:true,cancelable:true});
replies[0].dispatchEvent(pointer);
assert.ok(pointer.defaultPrevented,'pointer must not blur the composer');
replies[0].click();
assert.equal(input.value,'前文 @2 后文');
assert.equal(input.selectionStart,6);
assert.equal(w.document.activeElement,input);
input.value='保留选中文字'; input.setSelectionRange(2,4); replies[0].click();
assert.equal(input.value,'保留 @2 选中文字','insertion preserves selected draft text too');
input.value=''; replies[1].click();
assert.equal(input.value,'@123 ');
input.value='草稿'; input.setSelectionRange(2,2); replies[1].click();
assert.equal(input.value,'草稿 @123 ','separator keeps mention canonical');
await flush();
assert.deepEqual(sentMessages,[],'reply never issues a request');
w.run('renderGame({...replyFixture,room_kind:"bound"},"",replyEvents)');
assert.equal(elChat().querySelectorAll('.recent-chat-reply').length,0,'ordinary chat has no invitation reply shortcuts');
assert.equal(elChat().querySelectorAll('.recent-chat-message').length,w.replyEvents.filter(e=>e.sender.role!=='system').length,'ordinary chat still displays public speech');
w.run('renderGame(replyFixture,"",replyEvents)');
assert.equal(elChat().querySelectorAll('.recent-chat-reply').length,2,'invite replies recover after switching room types');

await w.run('sendMessage()');
assert.deepEqual(sentMessages,[{message:'草稿 @123'}],'send body contains only message');
// Household members occupy seats, but cannot unlock start or NPC fill.
w.run('identity.games[0].supports_npcs=true; identity.games[0].uses_local_npc_strategy=true;');
w.fixture={...waitingFixture,target_player_count:4,household_count:3,room_ready:false,
  participants:[participants[1],participants[2],{...participants[2],player_id:'124',display_name:'小机二'}]};
w.run('renderGame(fixture,"",[]);');
assert.ok(panel.textContent.includes('机 · ID 123'));
assert.ok(panel.textContent.includes('小机二 · ID 124'));
assert.equal(button('NPC 补满并开始'),null,'household AIs are not external invitees');
assert.equal(button('开始游戏').disabled,true);
w.fixture={...w.fixture,household_count:2};
w.run('renderGame(fixture,"",[]);');
assert.ok(button('NPC 补满并开始'),'an external AI unlocks fill');
w.fixture={...w.fixture,household_count:3,room_ready:true,participants:[...w.fixture.participants,participants[0]]};
w.run('renderGame(fixture,"",[]);');
assert.ok(button('开始游戏'),'external admission reaching target unlocks start');
// Invite selection reuses the ordinary picker visuals and identity data, but
// keeps independent state and always leaves one external seat.
w.run(`identity.machines=Array.from({length:5},(_,i)=>({id:String(101+i),name:'小机'+(i+1)}));
identity.games=[{game_type:'uno',category:'card',display_name:'UNO',allowed_player_counts:[2,3,4,5,6],supports_stakes:true,supports_multiplayer_stakes:true}];
$('aiPlayer').closest('.participant-picker').dataset.selectionMode='multiple';
selectedMachineIds.clear(); ['101','102','103'].forEach(id=>selectedMachineIds.add(id)); renderMachineMultiPicker();
$('inviteCategory').value='card';`);
await w.run('showInviteDialog("create")');
const el=id=>w.document.getElementById(id);
const changeCount=n=>{el('inviteCount').value=String(n); el('inviteCount').dispatchEvent(new w.Event('change'));};
const inviteOption=id=>w.document.querySelector(`#inviteAiMenu [data-player-id="${id}"]`);
assert.equal(el('inviteAiTrigger').disabled,true);
assert.equal(el('inviteAiHint').textContent,'至少留 1 个邀请席位。');
assert.equal(el('inviteStakeHint').textContent,'0=娱乐局；非 0 按游戏规则结算。');
assert.equal(el('inviteTimeoutHint').textContent,'超时由 NPC 临时代操作，可随时接回。');
for (const count of [3,4,5,6]) {
  w.run('inviteMachineIds.clear()');
  changeCount(count);
  el('inviteAiTrigger').click();
  for (let i=0;i<count-2;i++) inviteOption(String(101+i)).click();
  assert.equal(w.run('inviteMachineIds.size'),count-2);
  assert.equal(inviteOption(String(101+count-2)).disabled,true);
  inviteOption(String(101+count-2)).click();
  assert.equal(w.run('inviteMachineIds.size'),count-2,'limit is enforced by actual controls');
  assert.ok(el('inviteAiSummary').textContent.includes(`已选 ${count-2} 位`));
}
changeCount(4);
assert.deepEqual(Array.from(w.run('[...inviteMachineIds]')),['101','102'],'shrinking retains first choices');
assert.equal(inviteOption('103').getAttribute('aria-selected'),'false');
changeCount(2);
assert.equal(w.run('inviteMachineIds.size'),0);
assert.equal(el('inviteAiTrigger').disabled,true);
assert.equal(el('inviteAiTrigger').getAttribute('aria-expanded'),'false');
assert.ok(el('inviteAiSummary').textContent.includes('不带小机'));
changeCount(4);
el('inviteAiTrigger').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
assert.equal(w.document.activeElement,inviteOption('101'));
inviteOption('101').click(); inviteOption('102').click();
assert.equal(el('inviteAiTrigger').getAttribute('aria-expanded'),'true','selection keeps the menu open');
inviteOption('102').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
assert.equal(w.document.activeElement,el('inviteAiTrigger'));
assert.equal(el('inviteAiTrigger').getAttribute('aria-expanded'),'false');
const createRequests=[];
w.fetch=async(url,options)=>{
  createRequests.push({url,body:JSON.parse(options.body)});
  return {ok:true,json:async()=>({room:{...waitingFixture,household_count:3,target_player_count:4,room_ready:false,
    participants:[participants[1],{...participants[2],player_id:'101',display_name:'小机1'},{...participants[2],player_id:'102',display_name:'小机2'}]},timeline:[]})};
};
el('inviteStake').value='10';
el('inviteSubmit').click(); await flush();
assert.equal(createRequests[0].url,'/duel/api/invites');
assert.deepEqual(createRequests[0].body.ai_players,['101','102']);
assert.equal(createRequests[0].body.target_player_count,4);
assert.equal(createRequests[0].body.stake,10,'existing invite stakes are preserved');
assert.deepEqual(Array.from(w.run('selectedParticipantIds()')),['101','102','103'],'ordinary create retains its selection');
assert.equal(w.document.querySelectorAll('#aiMultiMenu [aria-selected="true"]').length,3);
assert.ok(panel.textContent.includes('小机1 · ID 101'));
assert.equal(button('NPC 补满并开始'),null);
w.close();
console.log('PASS full chat, scroll/render/poll/trim/switch behavior, reply mention insertion without requests or reply_to, invite picker, limits, standalone waiting UI, stakes, IDs, mentions, legacy UI, multiplayer perspective and chat isolation');
})().catch(error=>{w.close(); console.error(error); process.exitCode=1;});
