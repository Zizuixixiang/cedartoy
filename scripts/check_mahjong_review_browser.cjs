// Local static assets + temporary demo projection only. No network/database writes.
// PLAYWRIGHT_MODULE=/path/to/playwright node scripts/check_mahjong_review_browser.cjs /tmp/.../human-projection.json
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../vendor/duel');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const output = process.env.DUEL_UI_SCREENSHOTS || '/tmp/mahjong-review-visuals';
(async () => {
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    for (const width of [360, 430, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.host !== 'duel.test') return route.abort();
        if (url.pathname.startsWith('/api/')) return route.fulfill({json: {ok:true}});
        const rel = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
        const file = path.join(root, 'app/static', rel);
        if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
        let body = fs.readFileSync(file);
        if (rel === 'app.js') body = body.toString().replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '') + '\nwindow.run = source => eval(source);';
        return route.fulfill({body, contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html'});
      });
      await page.goto('http://duel.test/');
      await page.evaluate(async fixture => {
        await window.DuelGameUI.load('mahjong');
        window.fixture = fixture;
        window.run(`identity = {human_player_id: '1', human_name: '南杉', machines: [], games: []}; renderGame(window.fixture);`);
      }, fixture);
      await page.locator('#mahjongTerminalReview').waitFor();
      assert.equal(await page.locator('#resultModal').evaluate(el => el.classList.contains('hidden')), true, 'reentry must not pop up');
      assert.equal(await page.locator('.mahjong-terminal-row').count(), 4);
      assert.equal(await page.locator('.winning-tile').count(), 1);
      assert.ok(await page.locator('.winning-tile').evaluate(el => {
        const tile=el.getBoundingClientRect(), hand=el.parentElement.getBoundingClientRect();
        return tile.left >= hand.left && tile.right <= hand.right;
      }), 'winning tile must be initially visible in its scroller');
      assert.equal(await page.locator('.mahjong-terminal-meld-group').count(), 4);
      await page.locator('#mahjongTerminalReview').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(output, `${width}-page.png`), fullPage:true});
      await page.locator('#mahjongTerminalReview').screenshot({path:path.join(output, `${width}-review.png`)});
      const dimensions = await page.evaluate(() => ({
        width: innerWidth, scroll: document.documentElement.scrollWidth,
        rows: [...document.querySelectorAll('.mahjong-terminal-row')].map(e => {const r=e.getBoundingClientRect(); return {x:r.x,right:r.right,width:r.width};}),
        review: getComputedStyle(document.querySelector('.mahjong-terminal-title')).fontSize,
        metadata: getComputedStyle(document.querySelector('.mahjong-terminal-note')).fontSize,
        handScroll: [...document.querySelectorAll('.mahjong-terminal-hand')].map(e => ({client:e.clientWidth,scroll:e.scrollWidth}))
      }));
      assert.ok(dimensions.scroll <= width, JSON.stringify(dimensions));
      assert.ok(dimensions.rows.every(r => r.x >= 0 && r.right <= width));
      assert.equal(dimensions.metadata, '11px');
      // Original generic GAME OVER modal remains unchanged for Mahjong and all games.
      await page.evaluate(() => window.run(`room = {...window.fixture, status: 'playing'}; renderGame(window.fixture);`));
      await page.locator('#resultModal').waitFor({state:'visible'});
      assert.match(await page.locator('#resultModalText').innerText(), /南杉.*获胜/);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'resultPreserveCheckbox');
      await page.screenshot({path:path.join(output, `${width}-modal.png`)});
      for (const id of ['resultPreserveCheckbox','rematchButton','finishGameButton']) assert.ok(await page.locator('#'+id).isVisible());
      await page.locator('#finishGameButton').click();
      assert.equal(await page.locator('#resultModal').evaluate(e => e.classList.contains('hidden')), true);
      await page.locator('#mahjongTerminalReview').scrollIntoViewIfNeeded();
      assert.ok(await page.locator('#mahjongTerminalReview').isVisible());
      // Other game types retain the same generic modal behavior and buttons.
      const games = fs.readdirSync(path.join(root,'app/static/games')).filter(f=>f.endsWith('.js')).map(f=>f.slice(0,-3));
      for (const game of [...new Set([...games, 'gomoku','xiangqi','jungle','liars_dice','tictactoe','othello','connect4','dots_boxes'])].filter(g=>g!=='mahjong')) {
        await page.evaluate(game => window.run(`room = {...window.fixture, game_type: ${JSON.stringify(game)}}; openResultModal('原结算摘要');`), game);
        assert.equal(await page.locator('#resultModalText').innerText(), '原结算摘要');
        assert.equal(await page.evaluate(()=>document.activeElement.id), 'resultPreserveCheckbox');
      }
      // Long player names reflow; 200% browser text size and keyboard scroll remain usable.
      await page.evaluate(() => window.run(`closeResultModal(); room=null; renderGame({...window.fixture, participants: window.fixture.participants.map(p=>({...p,display_name:p.display_name.repeat(10)}))});`));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.addStyleTag({content: '.mahjong-terminal-review {font-size:24px} .mahjong-terminal-review * {font-size:inherit !important}'});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({width,dimensions,genericGames:games.length-1,status:'passed'}));
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e => {console.error(e); process.exitCode=1;});
