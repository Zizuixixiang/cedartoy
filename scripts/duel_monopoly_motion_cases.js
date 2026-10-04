// Runs inside the real Duel page harness in check_duel_monopoly_browser.js.
const assert = require('node:assert/strict');

module.exports = async function checkMonopolyMotion(page, base, width, output) {
  const count = base.participants.length;
  const render = async r => {
    await page.evaluate(f => {window.fixture = f; window.run('room=null;renderGame(fixture,"",[]);hideWaitModeModal();closeResultModal();');}, r);
    await page.locator('.monopoly-ring').waitFor();
  };
  const frames = (label, from = 0, to = 7) => {
    const before = structuredClone(base);
    before.room_id = `motion-${width}-${count}-${label}`;
    before.revision = 500; before.board_state.action_seq = 50;
    before.board_state.phase = 'roll'; before.board_state.extra_roll = false;
    before.board_state.dice = []; before.board_state.last_card_events = [];
    before.board_state.current_player_id = 'human:1';
    before.board_state.players.forEach(p => {p.position = from; p.jailed = false;});
    const after = structuredClone(before);
    after.revision++; after.board_state.action_seq++;
    after.board_state.phase = 'manage'; after.board_state.dice = [3, 4];
    after.board_state.players.find(p => p.player_id === 'human:1').position = to;
    return {before, after};
  };
  const start = async after => page.evaluate(next => {
    window.motionNext = next;
    const ring = document.querySelector('.monopoly-ring');
    const bounds = ring.getBoundingClientRect();
    window.motionProbe = {positions: [], scrollY: window.scrollY, x: bounds.x, y: bounds.y, shifted: false, scrollJump: false, cloned: false, elapsed: 0};
    const probe = window.motionProbe;
    const inspect = () => {
      const ghost = ring.querySelector('.monopoly-moving-token');
      if (!ghost) return;
      probe.cloned = true;
      const id = Number(ghost.dataset.tileId);
      if (Number.isInteger(id) && probe.positions.at(-1) !== id) probe.positions.push(id);
    };
    const observer = new MutationObserver(inspect);
    observer.observe(ring, {subtree: true, childList: true, attributes: true, attributeFilter: ['data-tile-id']});
    window.motionFinished = false;
    const sample = () => {
      const box = ring.getBoundingClientRect();
      if (ring.isConnected && (Math.abs(box.x - probe.x) > 1 || Math.abs(box.y - probe.y) > 1)) probe.shifted = true;
      if (Math.abs(window.scrollY - probe.scrollY) > 1) probe.scrollJump = true;
      if (!window.motionFinished) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    window.run('window.motionPromise=(async()=>{const started=performance.now();await showRoomTransitionFeedback(room,[],motionNext,[]);motionProbe.elapsed=performance.now()-started;renderGame(motionNext,"",[]);motionFinished=true;})()');
    window.motionPromise.finally(() => observer.disconnect());
  }, after);
  const finish = async (target, label, arrival = true) => {
    await page.evaluate(() => window.motionPromise);
    const probe = await page.evaluate(() => window.motionProbe);
    assert.equal(probe.scrollJump, false, `${label}/${width}/${count}: no scroll jump`);
    assert.equal(probe.shifted, false, `${label}/${width}/${count}: stable board bounds`);
    assert.equal(await page.locator('.monopoly-moving-token, .is-moving-source, .is-passing').count(), 0);
    assert.equal(await page.locator('.monopoly-tokens .is-viewer').count(), 1);
    assert.equal(await page.locator('.is-viewer-tile').getAttribute('data-tile-id'), String(target));
    assert.equal(await page.locator('.is-arrival').count(), arrival ? 1 : 0);
    assert.equal(await page.locator('.monopoly-game').evaluate(e => !!e.inert), false);
    assert.equal(await page.locator('.monopoly-controls').evaluate(e => !!e.inert), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    return probe;
  };
  const checkIdentity = async () => {
    const style = await page.locator('.monopoly-tokens .is-viewer').evaluate(e => {
      const own = getComputedStyle(e), other = getComputedStyle(e.parentElement.querySelector(':not(.is-viewer)'));
      const a = e.getBoundingClientRect(), b = e.parentElement.querySelector(':not(.is-viewer)').getBoundingClientRect();
      return {border: own.borderColor, otherBorder: other.borderColor, radius: own.borderRadius, otherRadius: other.borderRadius, width: a.width, otherWidth: b.width};
    });
    assert.notEqual(style.border, style.otherBorder);
    assert.notEqual(style.radius, style.otherRadius);
    assert.equal(style.width, style.otherWidth, 'viewer emphasis does not expand a crowded rack');
  };
  let r = frames('roll');
  await render(r.before); await checkIdentity();
  assert.match(await page.locator('.monopoly-location').textContent(), /^你当前在：/);
  // Position the board as a player would, then forbid any programmatic jump.
  await page.locator('.monopoly-ring').scrollIntoViewIfNeeded();
  await page.locator('.monopoly-game').screenshot({path: `${output}/motion-shared-${count}-${width}.png`});
  await start(r.after);
  await page.locator('.monopoly-moving-token').waitFor();
  assert.equal(await page.locator('.monopoly-game').getAttribute('data-revision'), '500', 'old DOM survives during movement');
  if (width === 360 && count === 4) await page.locator('.monopoly-ring').screenshot({path: `${output}/motion-walking-360.png`});
  let probe = await finish(7, 'roll');
  assert.deepEqual(probe.positions, [1, 2, 3, 4, 5, 6, 7]);
  assert.ok(probe.elapsed >= 7 * 90, 'seven visible steps');
  assert.match(await page.locator('.monopoly-location').textContent(), /^你移动到：/);
  await page.locator('.monopoly-game').screenshot({path: `${output}/motion-arrival-${count}-${width}.png`});
  // Identical polls do not replay the movement or reset the pulse expiry.
  await page.evaluate(() => window.run('showRoomTransitionFeedback(room,[],room,[])'));
  assert.equal(await page.locator('.monopoly-moving-token').count(), 0);
  if (width === 360 && count === 2) {
    await page.locator('.is-arrival').waitFor({state: 'detached', timeout: 3500});
    assert.match(await page.locator('.monopoly-location').textContent(), /^你当前在：/);
    assert.equal(await page.locator('.is-viewer-tile').getAttribute('data-tile-id'), '7');
    assert.equal(await page.locator('.monopoly-tokens .is-viewer').count(), 1);
  }

  r = frames('wrap', 38, 5); await render(r.before); await start(r.after);
  probe = await finish(5, 'wrap'); assert.deepEqual(probe.positions, [39, 0, 1, 2, 3, 4, 5]);

  r = frames('card', 0, 24); await render(r.before); await start(r.after);
  probe = await finish(24, 'card'); assert.deepEqual(probe.positions, [1, 2, 3, 4, 5, 6, 7, 24]);

  r = frames('jail', 28, 10); r.after.board_state.players.find(p => p.player_id === 'human:1').jailed = true;
  await render(r.before); await start(r.after); probe = await finish(10, 'jail');
  assert.deepEqual(probe.positions, [28, 10]); assert.match(await page.locator('.monopoly-location').textContent(), /你被送到监狱/);

  r = frames('missed-turns', 0, 23); r.after.board_state.action_seq += 3;
  await render(r.before); await start(r.after); probe = await finish(23, 'missed-turns'); assert.deepEqual(probe.positions, [0, 23]);

  await page.emulateMedia({reducedMotion: 'reduce'});
  r = frames('reduced'); await render(r.before); await start(r.after); probe = await finish(7, 'reduced');
  assert.equal(probe.cloned, false); assert.deepEqual(probe.positions, []);
  assert.equal(await page.locator('.is-arrival').evaluate(e => getComputedStyle(e).animationName), 'none');
  assert.match(await page.locator('.monopoly-location').textContent(), /^你移动到：/);
  await page.emulateMedia({reducedMotion: 'no-preference'});

  r = frames('npc', 0, 0); r.before.board_state.current_player_id = 'human:2';
  r.after.board_state.players.find(p => p.player_id === 'human:2').position = 7;
  await render(r.before); await start(r.after); probe = await finish(0, 'npc', false); assert.equal(probe.cloned, false);

  r = frames('different-room'); r.after.room_id += '-new';
  await render(r.before); await start(r.after); probe = await finish(7, 'different-room', false); assert.equal(probe.cloned, false);
  assert.match(await page.locator('.monopoly-location').textContent(), /^你当前在：/);

  r = frames('different-viewer'); r.after.viewer.player_id = 'human:2';
  await render(r.before); await start(r.after); probe = await finish(0, 'different-viewer', false);
  assert.equal(probe.cloned, false);
  assert.equal(await page.locator('.monopoly-tokens .is-viewer').getAttribute('data-player-id'), 'human:2');

  r = frames('spectator'); r.after.viewer = {is_participant: false};
  await render(r.before); await start(r.after); await page.evaluate(() => window.motionPromise);
  assert.equal(await page.locator('.monopoly-moving-token, .is-viewer-tile, .is-arrival, .monopoly-location').count(), 0);

  if (width === 360 && count === 4) {
    r = frames('cancel'); await render(r.before); await start(r.after);
    await page.locator('.monopoly-moving-token').waitFor();
    const other = frames('cancel-destination', 20, 20).before;
    // Cancel the host's final render exactly as its roomSyncGeneration guard does.
    await page.evaluate(() => {window.run('window.savedMotionRender=renderGame;renderGame=(r,...args)=>{if(r.room_id===motionNext.room_id)return;window.savedMotionRender(r,...args);};');});
    await render(other); await page.evaluate(() => window.motionPromise);
    assert.equal(await page.locator('.monopoly-moving-token, .is-arrival').count(), 0);
    assert.equal(await page.locator('.is-viewer-tile').getAttribute('data-tile-id'), '20');
    // Restore the real host function after the cancellation-only interception.
    await page.evaluate(() => window.run('renderGame=window.savedMotionRender;delete window.savedMotionRender;'));
  }
  return {width, count, normal: true, wrap: true, card: true, jail: true, reducedMotion: true, npc: true, roomIsolation: true};
};
