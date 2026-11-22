// Exercise the real room shell and renderer on viewer-safe temporary room projections.
// --browser uses an installed Playwright (or PLAYWRIGHT_MODULE=/path/to/playwright).
// Set DUEL_SCREENSHOTS to retain viewport screenshots at all three widths.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const duel = path.resolve(__dirname, "../vendor/duel");
const asset = file => fs.readFileSync(path.join(duel, "app/static", file), "utf8");
const makeFixture = `
import json, random
from app import database, framework, invites, main
from starlette.requests import Request
from app.games import GAMES, game_catalog
from tests.full_state_support import isolated_scenario_database
with isolated_scenario_database():
    game = GAMES['liars_bar']
    game._rng = random.Random(15)
    room = framework.create_room('liars_bar', 'human_first', 'human', 'human', opponent_id='ai',
                                 participant_names={'human':'南杉','ai':'小机'})
    # Make one reproducible actionable Web projection for both renderers.
    state = room['board_state']
    state.update(turn_player_id='human', target='K', pending={'player_id':'ai','cards':['A']})
    state['hands'] = {'human':['A','A','K','Q','Joker'],'ai':['K','K','Q','A']}
    room.update(current_player_id='human', turn='human')
    view = framework.project_room_for_viewer(framework._decorate(room), 'human')
    state['chambers']['ai'] = 5
    with database.write_transaction() as conn:
        conn.execute("UPDATE rooms SET board_state=?,current_player_id='human',turn='human' WHERE room_id=?",
                     (json.dumps(state),room['room_id']))
    played = game.apply_action(state, {'action':'play','cards':['A','A']}, {'player_id':'human'})
    after_play = dict(room, board_state=played.state, revision=room['revision'] + 1,
                      current_player_id=played.next_player_id, turn='ai')
    play_view = framework.project_room_for_viewer(framework._decorate(after_play), 'human')
    decided = framework.play_move(room['room_id'], 'human', 'human', {'action':'challenge'},
                                  expected_revision=room['revision'])
    reveal = framework.project_room_for_viewer(decided, 'human')
    req = Request(dict(type='http',headers=[(b'x-duel-human-player', b'outsider'),(b'x-duel-ai-player',b'ai')]))
    spectator = main.spectator_response(decided, req, None)['room']
    waiting = invites.create_invite('liars_bar','human','human', display_name='南杉',target_player_count=4)
    invitation = framework.project_room_for_viewer(waiting, 'human')
    for pid,name in [('ai','小机'),('friend','林间来客'),('guest','同桌')]:
        invites.join_invite(waiting['invite_code'],'ai',pid,display_name=name)
    started = invites.start_invite(waiting['room_id'],'human','human')
    invite_play = framework.project_room_for_viewer(started,'human')
    state['hands']['ai'] = []
    room['revision'] += 1
    last = framework.project_room_for_viewer(framework._decorate(room), 'human')
    print(json.dumps(dict(catalog=game_catalog(), view=view, last=last, play_view=play_view, reveal=reveal, spectator=spectator, invitation=invitation, invite_play=invite_play)))
`;
function loadFixture() {
  const temp = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "duel-bar-ui-"));
  const filename = path.join(temp, "fixture.json");
  const fd = fs.openSync(filename, "w");
  try {
    execFileSync(path.join(duel, ".venv/bin/python"), ["-c", makeFixture], {
      cwd: duel, stdio: ["ignore", fd, "inherit"], env: {...process.env, PYTHONDONTWRITEBYTECODE: "1"},
    });
    return JSON.parse(fs.readFileSync(filename, "utf8"));
  } finally {
    fs.closeSync(fd);
    fs.rmSync(temp, {recursive: true});
  }
}
const fixture = loadFixture();

const html = asset("index.html");
const host = asset("app.js").split("void (async () => {\n  const invite =")[0];
const identityFixture = {
  bound:true, human_player_id:"human", human_name:"南杉", identity_label:"南杉 · 小机",
  wallet:{balance:0}, machines:[{id:"ai",name:"小机"}], games:fixture.catalog,
};
const controls = `startRoomPolling = () => {};
   window.posts = [];
   window.fetch = async (url, options) => {
     if (url.endsWith("/move")) posts.push(JSON.parse(options.body));
     return {ok:true, json: async () => ({ok:true, room:window.replyRoom || fixture.view, timeline:[]})};
   };
   window.showRoom = data => renderGame(data, "", []);
   window.selectTabletop = () => {
     $("gameCategory").value = $("inviteCategory").value = "tabletop";
     syncGameTypeOptions(identity.games);
     syncInviteGameOptions();
     $("gameType").value = "liars_bar";
     $("gameType").dispatchEvent(new Event("change"));
   };`;
const domSource = [asset("game_ui_registry.js"), asset("games/liars_bar.js"), host,
  "identity = window.identityFixture;", controls].join("\n");

// Runs unchanged in Chromium and jsdom; only Chromium supplies pixel geometry.
async function exercise(measure) {
  const expect = (condition, msg) => { if (!condition) throw Error(msg); };
  const q = css => document.querySelector(css);
  const all = css => [...document.querySelectorAll(css)];
  const button = text => all("#board button").find(b => b.textContent === text);
  const submit = async text => {
    button(text).click();
    await new Promise(resolve => setTimeout(resolve, 0));
    return posts.at(-1);
  };
  selectTabletop();
  for (const selector of ["#gameType", "#inviteGame"]) {
    expect(q(selector).querySelector('option[value="liars_bar"]').textContent.includes("骗子酒馆"), selector);
  }
  expect(q("#mode").disabled && q("#mode").value === "random", "random opener");
  expect(q("#stake").disabled && q("#stake").value === "0", "entertainment only");
  showRoom(fixture.view);
  expect(q("#rulesText a").href === "https://github.com/LYiHub/liars-bar-llm", "attribution");
  const cards = all("#board input");
  expect(cards.length === 5 && button("暗出所选牌").disabled, "initial hand");
  cards.slice(0, 3).forEach(c => c.click());
  expect(cards[3].disabled && cards[4].disabled, "limit");
  const output = {};
  if (measure) {
    const bounds = q("#board").getBoundingClientRect();
    expect(bounds.width > 0 && bounds.left >= 0 && bounds.right <= innerWidth, "board overflow");
    expect(document.documentElement.scrollWidth <= innerWidth, "page overflow");
    expect(button("暗出所选牌").getBoundingClientRect().height >= 44, "action target");
    expect(cards.every(c => c.parentElement.getBoundingClientRect().height >= 44), "touch targets");
    output.boardWidth = Math.round(bounds.width);
  }
  const play = await submit("暗出所选牌");
  expect(JSON.stringify(play.move.cards) === '["A","A","K"]' && play.revision === fixture.view.revision, "play payload");
  expect((await submit("质疑上一手")).move.action === "challenge", "challenge payload");
  showRoom(fixture.last);
  expect(!button("暗出所选牌") && !q("#board input"), "last hand");
  expect((await submit("放过并摊验余牌")).move.action === "accept", "system challenge");
  showRoom(fixture.spectator);
  expect(!q("#board fieldset") && !q("#board button"), "public spectator");
  showRoom({...fixture.view, revision:fixture.last.revision + 1, status:"finished", current_player_id:null});
  expect(!q("#board button") && !q("#board input"), "terminal controls");
  return output;
}

async function inspectScreens(page, width) {
  const show = async key => {
    await page.evaluate(k => showRoom(fixture[k]), key);
    const dismiss = page.locator("#dismissWaitModeModalButton");
    if (await dismiss.isVisible()) await dismiss.click();
  };
  const take = async (name, selector) => {
    const region = page.locator(selector);
    await region.evaluate(el => el.scrollIntoView({block:"center", behavior:"instant"}));
    const box = await region.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= width + 1, name);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), name);
    if (process.env.DUEL_SCREENSHOTS) {
      fs.mkdirSync(process.env.DUEL_SCREENSHOTS, {recursive:true});
      await page.screenshot({path:path.join(process.env.DUEL_SCREENSHOTS, width + "-" + name + ".png")});
    }
  };
  await page.locator("#gameCategory").selectOption("tabletop");
  const gamePicker = page.locator("#gameType");
  await gamePicker.selectOption("carcassonne");
  await page.locator("#stake").fill("2");
  await gamePicker.selectOption("liars_bar");
  assert.equal(await page.locator("#participantHint").isVisible(), false);
  await gamePicker.selectOption("carcassonne");
  assert.equal(await page.locator("#stake").inputValue(), "2");
  await gamePicker.selectOption("liars_bar");
  assert.ok(await page.locator("#mode").isDisabled());
  assert.ok(await page.locator("#stake").isDisabled());
  await take("ordinary-entry", "#gameType");
  await page.locator("#inviteCreateEntry").click();
  await page.locator("#inviteCategory").selectOption("tabletop");
  await page.locator("#inviteGame").selectOption("liars_bar");
  assert.ok(await page.locator("#inviteStake").isDisabled());
  assert.deepEqual(await page.locator("#inviteCount option").evaluateAll(os => os.map(o => o.value)), ["2","3","4"]);
  await take("invite-entry", "#inviteDialog");
  await page.locator("#inviteDismiss").click();
  await show("invitation");
  await take("invite-waiting", "#inviteRoomPanel");
  await show("invite_play");
  await take("invite-table", "#board");
  await show("view");
  await take("ordinary", "#board");
  const faces = page.locator(".liars-bar-hand label");
  const boxes = await faces.evaluateAll(labels => labels.map(el => {
    const {x,y,width,height} = el.getBoundingClientRect();
    return {x,y,width,height};
  }));
  assert.equal(boxes.length, 5);
  assert.ok(boxes.every(b => b.y === boxes[0].y && b.width >= 44 && b.height >= 44));
  if (width >= 500) {
    const card = faces.first().getByRole("checkbox");
    await card.focus();
    await card.press("Space");
    assert.ok(await card.isChecked());
    await card.press("Space");
    assert.equal(await card.isChecked(), false);
  }
  const touch = async el => width < 500 ? el.tap() : el.click();
  await touch(faces.nth(0));
  await touch(faces.nth(1));
  assert.equal(await page.locator(".liars-bar-hand :checked").count(), 2);
  await take("selected", "#board");
  await page.evaluate(() => { window.replyRoom = fixture.play_view; });
  await touch(page.getByRole("button", {name:"暗出所选牌"}));
  await page.waitForFunction(() => window.posts.at(-1)?.move.action === "play");
  assert.deepEqual(await page.evaluate(() => posts.at(-1).move.cards), ["A","A"]);
  await page.getByRole("button", {name:"暗出所选牌"}).waitFor({state:"hidden"});
  await show("view");
  await page.evaluate(() => { window.replyRoom = fixture.reveal; });
  await touch(page.getByRole("button", {name:"质疑上一手"}));
  await page.getByText(/第 1 轮摊牌/).waitFor();
  await take("challenge", "#board");
  // The forced hand is a different branch from the same source revision.
  await show("view");
  await show("last");
  assert.ok(await page.getByRole("button", {name:"放过并摊验余牌"}).isVisible());
  assert.ok(await page.getByRole("button", {name:"质疑上一手"}).isVisible());
  assert.equal(await page.locator("#board input").count(), 0);
  await take("last-hand", "#board");
  await show("spectator");
  assert.equal(await page.locator("#board .liars-bar-hand,#board button").count(), 0);
  assert.equal(await page.locator("#sendMessageButton").isDisabled(), true);
  await take("spectator", "#board");
  console.log(width, "hand", boxes[0].width, "×", boxes[0].height, "all five in one row");
}

(async () => {
  const browserMode = process.argv.includes("--browser");
  const browser = browserMode
    ? await require(process.env.PLAYWRIGHT_MODULE || "playwright").chromium.launch()
    : null;
  try {
    for (const width of [360, 430, 1280]) {
      if (browser) {
        const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 500});
        await page.route("**/*", route => {
          const url = new URL(route.request().url());
          if (url.origin !== "http://duel.test") return route.abort();
          if (url.pathname === "/") return route.fulfill({body:html, contentType:"text/html"});
          if (url.pathname === "/api/whoami") return route.fulfill({json:{ok:true, ...identityFixture}});
          if (url.pathname === "/api/notifications/unread") return route.fulfill({json:{ok:true, notifications:[]}});
          if (/^\/static\/(?:games\/)?[a-z0-9_]+\.(js|css)$/.test(url.pathname)) {
            const file = url.pathname.slice("/static/".length);
            return route.fulfill({body:asset(file), contentType:file.endsWith(".js") ? "text/javascript" : "text/css"});
          }
          return route.abort();
        });
        await page.goto("http://duel.test/");
        await page.locator("#lobbyView").waitFor();
        assert.ok(await page.evaluate(() => window.DuelGameUI.has("liars_bar")));
        await page.evaluate(f => { window.fixture = f; }, fixture);
        await page.addScriptTag({content:controls});
        await inspectScreens(page, width);
        await page.evaluate(() => { window.replyRoom = null; });
        console.log(width, await page.evaluate(exercise, true));
        await page.close();
      } else {
        const {JSDOM} = require("jsdom");
        const dom = new JSDOM(html, {url:"http://duel.test/", runScripts:"outside-only", pretendToBeVisual:true});
        try {
          const w = dom.window;
          w.innerWidth = width;
          w.fixture = fixture;
          w.HTMLElement.prototype.scrollIntoView = () => {};
          w.identityFixture = identityFixture;
          w.eval(domSource);
          assert.ok(await w.eval(`(${exercise})(false)`));
        } finally { dom.window.close(); }
      }
    }
    console.log(browser ? "Chromium controls and viewport bounds passed" : "DOM controls passed (no pixel measurements)");
  } finally { if (browser) await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
