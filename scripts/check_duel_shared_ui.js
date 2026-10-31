/* Shared lobby/navigation/desktop CSS regressions. JSDOM does not measure layout. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('vendor/duel/app/static/index.html', 'utf8');
const source = fs.readFileSync('vendor/duel/app/static/app.js', 'utf8');
const css = fs.readFileSync('vendor/duel/app/static/styles.css', 'utf8');
const dom = new JSDOM(html, {url: 'http://duel.test/', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, doc = w.document;
w.scrollTo = () => {};
w.HTMLElement.prototype.scrollIntoView = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
w.eval(source.replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '') + '\nwindow.run = source => eval(source);');
const summary = {room_id: 'MINE', room_kind: 'invite', status: 'waiting', game_type: 'tictactoe',
  game_name: '井字棋', participant_names: ['测试'], participant_count: 1, target_player_count: 2};
w.fixtureIdentity = {bound: true, human_player_id: 'human:1', human_name: '测试', identity_label: '测试',
  wallet: {balance: 0}, machines: [], rooms: [summary], pending_invitations: [],
  games: [{game_type: 'tictactoe', display_name: '井字棋', allowed_player_counts: [2]}]};
w.run('identity=fixtureIdentity; startRoomPolling=()=>{};');
const spectatorSummary = {...summary, room_id: 'MACHINE', spectating: true};
const calls = [];
const roomCalls = () => calls.filter(call => ["/api/whoami", "/api/spectating/rooms"].includes(call.url));
let pendingSpectator;
w.fetch = async (url, options = {}) => {
  calls.push({url, method: options.method || 'GET'});
  if (url === '/api/spectating/rooms' && pendingSpectator) return pendingSpectator;
  const data = url === '/api/whoami' ? w.fixtureIdentity : {rooms: [spectatorSummary]};
  return {ok: true, json: async () => data};
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const scope = doc.getElementById('roomListScope');
const mine = scope.querySelector('[data-room-scope="mine"]');
const machines = scope.querySelector('[data-room-scope="machines"]');
const hint = doc.getElementById('roomScopeHint');
const refresh = doc.getElementById('refreshRoomsButton');
const visibleHint = () => hint.getAttribute('aria-hidden') === 'false';
const assertSelection = value => {
  assert.equal(w.run('roomListScope'), value);
  assert.equal(scope.querySelectorAll('[aria-selected="true"]').length, 1);
  assert.equal(scope.querySelector('[aria-selected="true"]').dataset.roomScope, value);
  assert.equal(visibleHint(), value === 'machines');
  const selected = scope.querySelector('[aria-selected="true"]');
  assert.equal(selected.tabIndex, 0);
  assert.equal(scope.querySelector('[aria-selected="false"]').tabIndex, -1);
  assert.equal(doc.getElementById('roomScopePanel').getAttribute('aria-labelledby'), selected.id);
};

(async () => {
  try {
    assert.equal(scope.tagName, 'DIV');
    assert.equal(scope.getAttribute('role'), 'tablist');
    assert.equal(scope.getAttribute('aria-label'), '房间列表范围');
    assert.deepEqual([...scope.children].map(b => b.textContent), ['我的', '自家小机']);
    assert.equal(doc.querySelector('.room-panel select, .room-panel h2, .room-panel .tag'), null);
    assert.equal(hint.textContent, '仅可观战，不能操作对局。');
    assert.equal(scope.contains(hint), false, 'helper text is outside the segmented control');
    assert.equal(scope.parentElement.firstElementChild, scope, 'tabs occupy the card top');
    assert.equal(scope.parentElement.nextElementSibling.id, 'roomScopePanel');
    assert.equal(refresh.parentElement, scope.parentElement, 'refresh is in the topbar');
    assert.equal(doc.querySelector('.room-list-tools'), null, 'no empty tools row');
    assert.equal(hint.parentElement.id, 'roomScopePanel');
    assert.equal(refresh.getAttribute('aria-label'), '刷新房间列表');
    assert.equal(scope.querySelectorAll('[role="tab"]').length, 2, 'refresh is not a third tab');
    assert.equal(scope.contains(refresh), false, 'refresh is a separate action outside tablist');
    assertSelection('mine');
    for (const button of [mine, machines]) {
      assert.equal(button.type, 'button');
      button.focus();
      assert.equal(doc.activeElement, button, 'native buttons are keyboard focusable');
    }
    machines.click(); await flush();
    assertSelection('machines');
    assert.equal(roomCalls().at(-1).url, '/api/spectating/rooms');
    assert.match(doc.querySelector('.room-open').getAttribute('aria-label'), /MACHINE/);
    assert.equal(doc.querySelector('.room-enter').textContent, '围观 →');
    const count = roomCalls().length;
    machines.click(); await flush();
    assert.equal(roomCalls().length, count, 'selected segment does not refetch');
    refresh.click(); await flush();
    assert.equal(roomCalls().at(-1).url, '/api/spectating/rooms');
    mine.click(); await flush();
    assertSelection('mine');
    assert.equal(roomCalls().at(-1).url, '/api/whoami');
    assert.match(doc.querySelector('.room-open').getAttribute('aria-label'), /MINE/);
    refresh.click(); await flush();
    assert.equal(roomCalls().at(-1).url, '/api/whoami');

    // Arrow/Home/End navigation updates focus, selected state and panel naming.
    for (const [key, expected] of [['ArrowRight', 'machines'], ['ArrowRight', 'mine'],
      ['ArrowLeft', 'machines'], ['Home', 'mine'], ['End', 'machines']]) {
      scope.querySelector('[aria-selected="true"]').dispatchEvent(new w.KeyboardEvent('keydown', {key, bubbles:true, cancelable:true}));
      await flush(); assertSelection(expected);
      assert.equal(doc.activeElement.dataset.roomScope, expected);
    }
    mine.click(); await flush();

    // A slow spectator response must not replace 我的 after switching back.
    let release;
    pendingSpectator = new Promise(resolve => { release = resolve; });
    machines.click();
    mine.click(); await flush();
    release({ok: true, json: async () => ({rooms: [spectatorSummary]})});
    await flush(); pendingSpectator = null;
    assertSelection('mine');
    assert.match(doc.querySelector('.room-open').getAttribute('aria-label'), /MINE/);
    assert.ok(calls.every(call => call.method === 'GET'), 'switch and refresh never mutate a room');

    w.waiting = {...summary, initiator_player_id: 'human:1', spectating: true, board_state: {},
      viewer: {player_id: 'human:1', seat: 0}, participants: [{player_id: 'human:1', display_name: '测试', role: 'human'}]};
    w.run('renderGame(waiting,"",[]);');
    assertSelection('machines');
    assert.deepEqual([...doc.querySelectorAll('#inviteRoomPanel button')].map(b => b.textContent), ['返回大厅']);
    await w.run('backToLobby({fromHistory:true})');
    assertSelection('machines');
    assert.equal(roomCalls().at(-1).url, '/api/spectating/rooms');

    // Preserve the invitation lifecycle fixes with an isolated transport.
    const inviteButton = text => doc.querySelector(`#inviteRoomPanel [data-action="${text}"]`);
    w.waiting.spectating = false;
    w.run('setRoomListScope("mine"); renderGame(waiting,"",[]);');
    assert.equal(inviteButton('开始游戏').nextElementSibling, inviteButton('解散房间'));
    let confirmations = 0;
    w.confirm = () => { confirmations++; return false; };
    const beforeCancel = w.run('JSON.stringify(room)');
    const beforeRequests = calls.length;
    inviteButton('解散房间').click(); await flush();
    assert.equal(w.run('JSON.stringify(room)'), beforeCancel);
    assert.equal(calls.length, beforeRequests, 'cancel has no transport side effects');
    inviteButton('返回大厅').click(); await flush();
    assert.equal(w.run('room'), null);
    assert.equal(confirmations, 1, 'return does not confirm destruction');
    assert.equal(calls.filter(c => c.url.endsWith('/leave')).length, 0);
    assert.match(doc.querySelector('.room-open').getAttribute('aria-label'), /MINE/);
    w.run('renderGame(waiting,"",[]);');
    w.confirm = () => { confirmations++; return true; };
    const dissolve = inviteButton('解散房间');
    dissolve.click(); dissolve.click(); await flush();
    assert.equal(confirmations, 2);
    assert.deepEqual(calls.filter(c => c.url.endsWith('/leave')), [{url:'/api/rooms/MINE/leave', method:'POST'}]);
    assert.equal(w.run('room'), null);

    // JSDOM does not apply viewport media queries. Select their CSS rules explicitly
    // to verify breakpoints and declared sizing, without claiming rendered geometry.
    doc.getElementById('gameView').classList.remove('hidden', 'invite-waiting-view', 'spectating-view');
    const originalStyle = doc.createElement('style');
    originalStyle.textContent = css; doc.head.appendChild(originalStyle);
    const cssRules = [...originalStyle.sheet.cssRules]; originalStyle.remove();
    // JSDOM cannot resolve var() in these shorthands; inspect their parsed declarations.
    // These checks deliberately make no claim about rendered color/contrast or border pixels.
    const ruleFor = selector => cssRules.find(rule => rule.selectorText === selector).style;
    assert.equal(ruleFor('.room-scope-select').getPropertyValue('border'), '2px solid var(--purple)');
    assert.equal(ruleFor('.room-scope-tabs button').getPropertyValue('background'), 'var(--purple-light)');
    assert.equal(ruleFor('.room-scope-tabs button').getPropertyValue('border-bottom'), '4px solid var(--purple)');
    const atWidth = (rules, width) => rules.map(rule => {
      if (rule.type !== w.CSSRule.MEDIA_RULE) return rule.cssText;
      const condition = rule.conditionText;
      if (!/^\((min|max)-width: \d+px\)$/.test(condition)) return '';
      const [, kind, limit] = condition.match(/^\((min|max)-width: (\d+)px\)$/);
      return (kind === 'min' ? width >= +limit : width <= +limit) ? atWidth([...rule.cssRules], width) : '';
    }).join('\n');
    for (const width of [360, 430, 1024, 1280]) {
      const style = doc.createElement('style'); style.textContent = atWidth(cssRules, width); doc.head.appendChild(style);
      const computed = node => w.getComputedStyle(node);
      assert.equal(computed(scope).display, 'grid');
      assert.equal(computed(scope).gridTemplateColumns, 'repeat(2, minmax(0, 1fr))');
      assert.equal(parseFloat(computed(scope.parentElement).padding), 0, 'tabs stay flush on mobile too');
      assert.equal(computed(mine).appearance, 'none');
      assert.equal(computed(mine).borderRadius, '0px');
      const spectator = computed(doc.getElementById('spectatorSelect'));
      assert.equal(spectator.fontSize, '12px');
      assert.equal(spectator.minHeight, '28px');
      for (const value of ['mine', 'machines']) {
        w.run(`setRoomListScope('${value}')`);
        const selected = scope.querySelector('[aria-selected="true"]');
        assert.equal(computed(selected).backgroundColor, 'rgb(255, 255, 255)');
        assert.equal(computed(selected).borderBottomColor, 'rgba(0, 0, 0, 0)');
        assert.equal(computed(hint).display, value === 'mine' ? 'none' : 'block', 'mine reserves no helper height');
      }
      assert.equal(computed(mine).fontSize, '13px');
      assert.equal(computed(mine).minHeight, '44px');
      assert.equal(computed(hint).fontSize, '12px');
      assert.equal(computed(refresh).minHeight, '44px');
      const view = doc.getElementById('gameView');
      view.classList.add('spectating-view');
      assert.equal(computed(doc.getElementById('spectatorSelect')).height, '28px');
      assert.equal(computed(doc.getElementById('spectatorSelect')).borderTopWidth, '1px');
      assert.equal(computed(doc.getElementById('turn')).fontSize, '12px');
      assert.equal(computed(doc.getElementById('turn')).overflow, 'hidden');
      assert.equal(computed(doc.getElementById('turn')).boxShadow, 'none');
      view.classList.remove('spectating-view');
      assert.equal(computed(doc.getElementById('turn')).fontSize, width < 600 ? '14px' : '15px', 'ordinary player hierarchy unchanged');
      for (const count of [2, 4]) {
        w.layoutFixture = {game_type: count === 2 ? 'tictactoe' : 'uno',
          participants: Array.from({length: count}, (_, i) => ({player_id: String(i)}))};
        w.run('applyParticipantLayout(layoutFixture);');
        const stage = doc.getElementById('battleStage');
        assert.equal(stage.classList.contains('multiplayer-presentation'), count > 2, 'seat presentation retains real count');
        assert.equal(doc.getElementById('tableLayout').classList.contains('layout-duel'), count === 2);
        const chat = computed(doc.getElementById('gameChatArea'));
        if (width >= 1024) {
          assert.match(computed(stage).gridTemplateColumns, /^minmax\(0, 1fr\) clamp\(/);
          assert.equal(chat.gridColumn, '2', `${width}px / ${count} players has a right chat column`);
          assert.equal(chat.contain, 'size', 'chat history cannot expand the board row');
          assert.equal(computed(doc.getElementById('recentChatFeed')).height, 'auto');
        } else {
          assert.equal(computed(stage).gridTemplateColumns, 'none', `${width}px keeps stacked game/chat`);
          assert.equal(chat.gridColumn || 'auto', 'auto');
          assert.equal(computed(doc.getElementById('recentChatFeed')).height, width < 600 ? '126px' : '134px');
        }
      }
      style.remove();
    }
    console.log('PASS shared UI: full-width tabs, keyboard, topbar refresh with no blank tool row, spectator select, refresh/races, invite return/cancel/confirm; CSS declarations at 360/430/1024/1280 for 2/4 seats. No rendered geometry or screenshots.');
  } finally { w.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
