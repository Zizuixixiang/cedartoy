/* Render real Duel assets and temporary-db projections in Chromium.
 * PLAYWRIGHT_MODULE may point at an existing external Playwright installation.
 * No running service, production account, or model provider is used.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = process.env.DUEL_UI_SCREENSHOTS || '/tmp/duel-ui-finish';
fs.mkdirSync(output, {recursive: true});
const generated = spawnSync('.venv/bin/python', ['-c', `
import tempfile,json
from pathlib import Path
from app import database,invites,framework
from app.games import GAMES,game_catalog
with tempfile.TemporaryDirectory(prefix='duel-visual-') as d:
 database.DB_PATH=Path(d)/'test.db'
 database.init_db()
 r=invites.create_invite('uno','human','human:1',target_player_count=4,display_name='南杉',ai_players=['101'],trusted_bound_ais=[{'id':'101','name':'小紫'}])
 project=lambda r: framework.project_room_for_viewer(r,'human:1')
 household=project(r)
 r=invites.join_invite(r['invite_code'],'human','human:2',display_name='朋友阿青')
 waiting=project(r)
 r=invites.join_invite(r['invite_code'],'ai','202',display_name='名字很长也不应该撑开补全菜单的小机')
 full=project(r)
 r=invites.start_invite(r['room_id'],'human','human:1')
 playing=project(r)
 games=[]
 for key,plugin in GAMES.items():
  database.DB_PATH=Path(d)/f"{key}.db"
  database.init_db()
  count=max(plugin.resolved_allowed_player_counts())
  g=invites.create_invite(key,'human','human:1',target_player_count=count,display_name='南杉')
  for i in range(1,count):
   g=invites.join_invite(g['invite_code'],'human',f'human:{i+1}',display_name=f'朋友{i}')
  g=invites.start_invite(g['room_id'],'human','human:1')
  games.append(project(g))
 print(json.dumps(dict(household=household,waiting=waiting,full=full,playing=playing,games=games,catalog=game_catalog())))
`], {cwd: path.join(root, 'vendor/duel'), encoding: 'utf8'});
assert.equal(generated.status, 0, generated.stderr);
const fixtures = JSON.parse(generated.stdout);

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const measurements = [];
  try {
    for (const width of [360, 428, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}, hasTouch: width < 600});
      // Polling is exercised explicitly below. Automatic polling against an
      // immediate stub response would create an artificial long-poll busy loop.
      await page.addInitScript(() => { window.setInterval = () => 0; });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.host !== 'duel.test') return route.abort();
        if (url.pathname.startsWith('/api/')) return route.fulfill({json: {ok: true}});
        const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/static\//, '');
        const file = path.join(root, 'vendor/duel/app/static', relative);
        if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
        let body = fs.readFileSync(file);
        if (relative === 'app.js') {
          body = body.toString().replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '') + '\nwindow.run=(source)=>eval(source);';
        }
        return route.fulfill({body, contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
      });
      await page.goto('http://duel.test/');
      if (process.env.DUEL_UI_FONT) {
        // Optional local copies of the page's existing Google fonts keep visual
        // checks deterministic without depending on third-party font requests.
        for (const [weight, file] of [[400,process.env.DUEL_UI_FONT],[700,process.env.DUEL_UI_FONT_BOLD]]) {
          if (file) await page.addStyleTag({content:`@font-face {font-family:Silkscreen;font-weight:${weight};src:url(data:font/ttf;base64,${fs.readFileSync(file).toString('base64')})}`});
          await page.evaluate(weight=>document.fonts.load(`${weight} 16px Silkscreen`), weight);
        }
      }
      await page.evaluate(() => window.run(`identity={human_player_id:'human:1',human_name:'南杉',machines:[{id:'101',name:'小紫'}],games:[{game_type:'uno',display_name:'UNO',category:'card',allowed_player_counts:[2,3,4,5,6],supports_npcs:true}],npc_provider:{available:true}};`));
      await page.evaluate(() => window.run('startRoomPolling=()=>{};'));
      await page.evaluate(async catalog => {window.catalog=catalog; await window.run('loadCatalogGameRenderers(catalog)');}, fixtures.catalog);
      await page.evaluate(() => window.run(`
        const summary={room_kind:'invite',game_type:'uno',game_name:'UNO',
          participant_names:['南杉','朋友阿青','小紫','周末来玩的朋友'],participant_count:4,target_player_count:4};
        renderRooms([
          {...summary,room_id:'INVITE1',status:'waiting'},
          {...summary,room_id:'INVITE2',status:'playing'},
          {...summary,room_id:'BOUND',room_kind:'bound',status:'playing',participant_names:['南杉','小紫'],ai_name:'小紫'},
          {...summary,room_id:'ENDED',status:'finished'},
        ]);
        showView('lobbyView');
      `));
      const cards=page.locator('#roomList .room-card');
      assert.equal(await cards.locator('.room-invite-badge').count(),2);
      assert.equal(await cards.nth(2).locator('.room-invite-badge').count(),0);
      assert.equal(await cards.nth(3).locator('.room-invite-badge').count(),0);
      const roomMetrics=await cards.evaluateAll(cards=>cards.map(card=>{
        const title=card.querySelector('.room-title'), badge=card.querySelector('.room-invite-badge');
        const r=card.getBoundingClientRect();
        const titleBefore=title.getBoundingClientRect();
        if (badge) badge.style.display='none';
        const titleWithoutBadge=title.getBoundingClientRect();
        if (badge) badge.style.removeProperty('display');
        return {left:r.left,right:r.right,titleHeight:title.getBoundingClientRect().height,
          titleWidth:title.getBoundingClientRect().width,overflow:card.scrollWidth>card.clientWidth,
          titleWidthDelta:titleBefore.width-titleWithoutBadge.width,
          titleHeightDelta:titleBefore.height-titleWithoutBadge.height,
          border:getComputedStyle(card).borderColor,innerBorder:getComputedStyle(card,'::after').borderColor,
          badgeHeight:badge?.getBoundingClientRect().height};
      }));
      for (const metric of roomMetrics) {
        assert.ok(metric.left>=0 && metric.right<=width);
        assert.equal(metric.overflow,false);
      }
      assert.equal(roomMetrics[0].badgeHeight,18);
      assert.equal(roomMetrics[0].titleWidthDelta,0,'badge does not take title width');
      assert.equal(roomMetrics[0].titleHeightDelta,0,'badge does not wrap participant names');
      assert.notEqual(roomMetrics[0].innerBorder,roomMetrics[2].innerBorder);
      assert.equal(roomMetrics[3].border,'rgb(170, 163, 173)','ended gray wins');
      measurements.push({width,rooms:roomMetrics});
      await page.locator('#roomList').scrollIntoViewIfNeeded();
      await page.screenshot({path:`${output}/rooms-${width}.png`,fullPage:true});
      // Real form geometry: retain the existing 12px helper scale and one line.
      await page.evaluate(async () => {
        window.run("identity.games[0].supports_stakes=true; identity.games[0].supports_multiplayer_stakes=true; $('inviteCategory').value='card';");
        await window.run('showInviteDialog("create")');
      });
      for (const count of [2, 4]) {
        await page.locator('#inviteCount').selectOption(String(count));
        for (const id of ['inviteAiHint', 'inviteStakeHint', 'inviteTimeoutHint']) {
          const metric = await page.locator(`#${id}`).evaluate(e => {
            const r=e.getBoundingClientRect(), css=getComputedStyle(e);
            return {text:e.textContent, size:parseFloat(css.fontSize), line:parseFloat(css.lineHeight),
              height:r.height, left:r.left, right:r.right, overflow:e.scrollWidth>e.clientWidth};
          });
          assert.equal(metric.size,12);
          assert.ok(metric.height <= metric.line + 1, `${width}px ${id}: ${metric.text}`);
          assert.ok(metric.left>=0 && metric.right<=width);
          assert.equal(metric.overflow,false);
          measurements.push({width,count,id,...metric});
        }
        assert.equal(await page.locator('#inviteAiTrigger').isDisabled(),count===2);
        await page.screenshot({path:`${output}/create-${count}-${width}.png`,fullPage:true});
      }
      await page.evaluate(() => window.run('$("inviteDialog").close()'));
      const render = fixture => page.evaluate(f => {window.fixture=f; window.run('renderGame(fixture,"",[]);');}, fixture);
      const primary = page.locator('#inviteRoomPanel button[data-action="开始游戏"]');
      const npc = page.locator('#inviteRoomPanel button[data-action="NPC 补满并开始"]');
      await render(fixtures.household);
      assert.equal(await primary.isDisabled(), true);
      assert.equal(await npc.count(), 0);
      await render(fixtures.waiting);
      assert.equal(await primary.isDisabled(), true);
      assert.equal(await npc.isEnabled(), true);
      assert.equal(await page.locator('#inviteStartHint').count(),0);
      await page.screenshot({path: `${output}/waiting-3of4-${width}.png`, fullPage: true});
      await render(fixtures.full);
      assert.equal(await primary.isEnabled(), true);
      assert.equal(await npc.count(), 0);
      assert.equal(await page.locator('#inviteStartHint').count(),0);
      await page.screenshot({path: `${output}/waiting-4of4-${width}.png`, fullPage: true});
      for (const fixture of [fixtures.waiting, fixtures.full]) {
        await render({...fixture,viewer:{...fixture.viewer,player_id:'human:2'}});
        const hint=page.locator('#inviteStartHint');
        assert.equal(await primary.isDisabled(),true);
        assert.equal(await npc.count(),0);
        assert.equal(await hint.isVisible(),true);
        assert.equal(await hint.textContent(),'等待房主开始游戏');
        const buttonRect=await primary.boundingBox(), hintRect=await hint.boundingBox();
        assert.ok(hintRect.x>=buttonRect.x+buttonRect.width,'hint beside disabled button');
        assert.ok(Math.abs(hintRect.y+hintRect.height/2-buttonRect.y-buttonRect.height/2)<1);
        assert.ok(hintRect.x+hintRect.width<=width);
        assert.equal(await hint.evaluate(e=>getComputedStyle(e).fontSize),'12px');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.screenshot({path:`${output}/waiting-guest-${fixture.participants.length}-${width}.png`,fullPage:true});
      }
      await render(fixtures.playing);
      assert.equal(await page.locator('#inviteRoomPanel').isVisible(), false);
      assert.equal(await page.locator('#inviteRoomPanel').textContent(), '');
      const input = page.locator('#chatInput');
      await input.scrollIntoViewIfNeeded();
      // Browser geometry, not JSDOM: overlay must contribute zero flow height.
      const geometry = () => page.evaluate(() => Object.fromEntries(['#gameView','#battleStage','#gameChatArea','.chat-compose','#chatInput','#board'].map(s => {
        const r = document.querySelector(s).getBoundingClientRect();
        return [s, {x:r.x,y:r.y,width:r.width,height:r.height}];
      })));
      const before = await geometry();
      await input.fill('@');
      const after = await geometry();
      assert.deepEqual(after, before, `mention layout stays fixed at ${width}px`);
      const stats = await page.evaluate(() => {
        const menu = document.querySelector('#mentionOptions'), row=menu.firstElementChild;
        const rect = menu.getBoundingClientRect(), input=document.querySelector('#chatInput').getBoundingClientRect();
        const css = getComputedStyle(row);
        return {font:css.fontFamily, size:parseFloat(css.fontSize), inputSize:parseFloat(getComputedStyle(document.querySelector('#chatInput')).fontSize), rowHeight:row.getBoundingClientRect().height,
          position:getComputedStyle(menu).position, top:rect.top,bottom:rect.bottom,left:rect.left,right:rect.right,inputTop:input.top,
          unclipped: menu.contains(document.elementFromPoint(rect.left+12,rect.top+12)), horizontalOverflow:document.documentElement.scrollWidth>innerWidth};
      });
      assert.equal(stats.position, 'absolute');
      assert.equal(stats.font, 'system-ui, sans-serif');
      assert.ok(stats.size < stats.inputSize);
      assert.ok(stats.rowHeight >= 24 && stats.rowHeight <= 32);
      assert.ok(Math.abs(stats.bottom + 4 - stats.inputTop) < 1);
      assert.ok(stats.left >= 0 && stats.right <= width);
      assert.ok(stats.unclipped, 'overlay is above board/chat and not clipped');
      assert.equal(stats.horizontalOverflow, false);
      measurements.push({width, ...stats, layoutDelta: 0});
      await page.screenshot({path: `${output}/mention-${width}.png`, fullPage: true});
      await input.press('ArrowDown'); await input.press('ArrowDown');
      const expected = await page.locator('#mentionOptions [aria-selected="true"]').textContent();
      assert.ok(expected.includes('ID'));
      await input.press('Tab');
      assert.ok((await input.inputValue()).endsWith(' '));
      assert.equal(await page.locator('#mentionOptions').isVisible(), false);
      await input.fill('@'); await input.press('ArrowUp'); await input.press('Enter');
      assert.equal(await page.locator('#mentionOptions').isVisible(), false);
      await input.fill('@'); await input.press('Escape');
      assert.equal(await input.inputValue(), '@');
      assert.equal(await page.locator('#mentionOptions').isVisible(), false);
      await input.fill('@');
      await page.locator('#mentionOptions button').first().click();
      assert.equal(await input.evaluate(e=>document.activeElement===e), true);
      // Reply uses canonical handles and stays within the existing chat row.
      await page.evaluate(f => {
        window.fixture=f;
        window.replyEvents=f.participants.map((p,i)=>({sequence:i+1,event_type:'message',is_public:true,
          sender:{player_id:p.player_id,role:p.role,name:p.display_name},text:'一起玩吧'}));
        window.run('renderGame(fixture,"",replyEvents)');
        window.replyRequests=[];
        window.originalFetch=window.fetch;
        window.fetch=async(url,options)=>{
          window.replyRequests.push({url,body:JSON.parse(options.body)});
          return {ok:true,json:async()=>({room:window.fixture,timeline:window.replyEvents})};
        };
      },fixtures.playing);
      const reply=page.locator('.recent-chat-reply').first();
      assert.equal(await page.locator('.recent-chat-reply').count(),fixtures.playing.participants.length-1);
      const target=fixtures.playing.participants.find(p=>p.player_id!=='human:1');
      await input.fill('前文 后文');
      await input.evaluate(e=>e.setSelectionRange(3,3));
      const replyGeometry=await geometry();
      const metrics=await reply.evaluate(e=>{
        const r=e.getBoundingClientRect(), row=e.closest('li'), text=row.querySelector('p');
        return {width:r.width,height:r.height,rowHeight:row.getBoundingClientRect().height,
          font:parseFloat(getComputedStyle(e).fontSize),bodyFont:parseFloat(getComputedStyle(text).fontSize)};
      });
      assert.equal(metrics.width,28);
      assert.equal(metrics.height,19);
      assert.equal(metrics.font,10);
      assert.equal(await reply.evaluate(e=>getComputedStyle(e).borderTopWidth),'1px');
      assert.equal(await reply.evaluate(e=>getComputedStyle(e).borderTopStyle),'solid');
      assert.notEqual(await reply.evaluate(e=>getComputedStyle(e).backgroundColor),'rgba(0, 0, 0, 0)');
      assert.ok(metrics.rowHeight<=26,'small reply does not inflate one-line rows');
      assert.ok(metrics.font<=metrics.bodyFont);
      measurements.push({width,reply:metrics});
      // Track blur as well as final focus; tap must not close/reopen the keyboard.
      await input.evaluate(e=>{window.replyBlurs=0;e.addEventListener('blur',()=>window.replyBlurs++);});
      if (width<600) await reply.tap(); else await reply.click();
      assert.equal(await input.inputValue(),`前文 @${target.handle} 后文`);
      assert.equal(await input.evaluate(e=>document.activeElement===e),true);
      assert.equal(await page.evaluate(()=>window.replyBlurs),0);
      assert.deepEqual(await geometry(),replyGeometry);
      assert.deepEqual(await page.evaluate(()=>window.replyRequests),[]);
      await page.screenshot({path:`${output}/reply-${width}.png`,fullPage:true});
      await page.locator('#sendMessageButton').click();
      assert.deepEqual(await page.evaluate(()=>window.replyRequests.map(r=>r.body)),[{message:`前文 @${target.handle} 后文`}]);
      // Keyboard activation is also insertion, with no implicit Send.
      await input.fill('');
      await page.locator('.recent-chat-reply').first().focus();
      await page.keyboard.press('Enter');
      assert.equal(await input.inputValue(),`@${target.handle} `);
      assert.equal(await page.evaluate(()=>window.replyRequests.length),1);
      await page.evaluate(()=>window.fetch=window.originalFetch);
      // Long Chinese copy retains most of its width even beside a long speaker.
      await page.evaluate(()=>window.run(`
        replyEvents=replyEvents.map(e=>({...e,text:'这是一条较长的中文消息，看看回复按钮会不会把正文挤成过多换行。'}));
        renderGame(fixture,'',replyEvents);
      `));
      const longRows=await page.locator('.recent-chat-message:has(.recent-chat-reply)').evaluateAll(rows=>rows.map(row=>{
        const text=row.querySelector('p'),reply=row.querySelector('button');
        const before=text.getBoundingClientRect(),rowRect=row.getBoundingClientRect();
        const gap=reply.getBoundingClientRect().left-before.right;
        reply.style.display='none';
        const without=text.getBoundingClientRect();
        reply.style.removeProperty('display');
        return {bodyWidth:before.width,rowWidth:rowRect.width,widthCost:without.width-before.width,gap,
          lines:before.height/parseFloat(getComputedStyle(text).lineHeight),
          overflow:row.scrollWidth>row.clientWidth};
      }));
      for (const row of longRows) {
        assert.ok(row.gap<=4);
        assert.ok(row.widthCost<=32);
        assert.ok(row.bodyWidth>=row.rowWidth*.4);
        assert.ok(row.lines<=5,'long copy remains readable');
        assert.equal(row.overflow,false);
      }
      measurements.push({width,longRows});
      await page.screenshot({path:`${output}/reply-long-${width}.png`,fullPage:true});
      // An enabled timer / old completed marker never displays reclaim.
      await render({...fixtures.playing,timeout_takeover:true,takeover_revision:1});
      assert.equal(await page.locator('#reclaimControl').isVisible(), false);
      await render({...fixtures.playing,current_player_id:'human:1',viewer:{...fixtures.playing.viewer,temporary_takeover_active:true}});
      const reclaim=page.locator('#reclaimControl');
      assert.equal(await reclaim.isVisible(), true);
      await reclaim.scrollIntoViewIfNeeded();
      assert.ok((await reclaim.boundingBox()).height <= 40);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path: `${output}/takeover-${width}.png`, fullPage: true});
      await render({...fixtures.playing,room_kind:'bound'});
      assert.equal(await reclaim.isVisible(),false);
      assert.equal(await page.locator('#inviteRoomPanel').isVisible(),false);
      await page.evaluate(()=>window.run('hideWaitModeModal()'));
      assert.equal(fixtures.games.length, 25);
      for (const fixture of process.env.DUEL_UI_INVITE_ONLY ? [] : fixtures.games) {
        await render(fixture);
        const emptyFeedHeight=await page.locator('#recentChatFeed').evaluate(e=>e.getBoundingClientRect().height);
        await page.evaluate(f => {
          window.fixture=f;
          window.events=Array.from({length:200},(_,i)=>({sequence:i+1,event_type:'message',is_public:true,
            sender:{player_id:'human:2',role:'human',name:'朋友'},text:`第 ${i+1} 条普通消息`}));
          window.run('renderGame(fixture,"",events);');
        }, fixture);
        const list=page.locator('#recentChatMessages');
        await input.scrollIntoViewIfNeeded();
        const chatStats=await list.evaluate(e=>({count:e.children.length,scrollHeight:e.scrollHeight,height:e.clientHeight,
          top:e.scrollTop,overflow:getComputedStyle(e).overflowY,feedHeight:e.parentElement.getBoundingClientRect().height,
          replyButtons:e.querySelectorAll('button').length}));
        assert.equal(chatStats.count,200,fixture.game_type);
        assert.equal(chatStats.replyButtons,200);
        assert.equal(chatStats.overflow,'auto');
        assert.ok(Math.abs(chatStats.feedHeight-emptyFeedHeight)<1,'chat data does not set the container height');
        if (width < 600) assert.equal(chatStats.feedHeight,126,fixture.game_type);
        else if (width < 1024 || fixture.participants.length <= 2) assert.equal(chatStats.feedHeight,134,fixture.game_type);
        else {
          const rail=await page.locator('#gameChatArea').boundingBox();
          const main=await page.locator('.battle-main-column').boundingBox();
          assert.ok(Math.abs(rail.height-main.height)<1,'desktop rail adapts to the board column');
          assert.ok(chatStats.feedHeight>0 && chatStats.feedHeight<rail.height);
        }
        assert.ok(chatStats.scrollHeight>chatStats.height);
        assert.ok(Math.abs(chatStats.top+chatStats.height-chatStats.scrollHeight)<=1,'enter room at bottom');
        await list.evaluate(e=>e.scrollTop=100);
        const anchor=()=>list.evaluate(e=>{
          const top=e.getBoundingClientRect().top;
          const row=[...e.children].find(r=>r.getBoundingClientRect().bottom>top);
          return {sequence:row.dataset.sequence,offset:row.getBoundingClientRect().top-top};
        });
        const reading=await anchor();
        const assertReadingAnchor = async label => {
          const current=await anchor();
          assert.equal(current.sequence,reading.sequence,label);
          // scrollTop rounds fractional row heights to device pixels.
          assert.ok(Math.abs(current.offset-reading.offset)<=1,label);
        };
        // Exercise the actual long-poll refresh path with a stubbed response.
        await page.evaluate(async()=>{
          window.events.push({...window.events[0],sequence:201,text:'新到消息'});
          window.fetch=async()=>({ok:true,json:async()=>({room:window.fixture,timeline:window.events})});
          await window.run('refreshRoom({quiet:true,wait:true,generation:roomSyncGeneration})');
        });
        assert.equal(await list.locator('li').count(),201);
        await assertReadingAnchor(`${fixture.game_type}: poll retains reading anchor`);
        // Sliding timeline window: older rows removed, same message stays put.
        await page.evaluate(()=>{window.events=window.events.slice(2);window.run('renderGame(fixture,"",events)');});
        await assertReadingAnchor(`${fixture.game_type}: trimmed history retains reading anchor`);
        await list.evaluate(e=>e.scrollTop=e.scrollHeight-e.clientHeight-10);
        await page.evaluate(()=>{window.events.push({...window.events[0],sequence:202,text:'跟到底部'});window.run('renderGame(fixture,"",events)');});
        assert.ok(await list.evaluate(e=>Math.abs(e.scrollTop+e.clientHeight-e.scrollHeight)<=1));
        await list.evaluate(e=>e.scrollTop=30);
        await page.evaluate(()=>window.run('renderGame({...fixture,room_id:fixture.room_id+"-switch"},"",events)'));
        assert.ok(await list.evaluate(e=>Math.abs(e.scrollTop+e.clientHeight-e.scrollHeight)<=1),'room switch follows bottom');
        const beforeMenu=await geometry();
        await input.fill('@');
        assert.deepEqual(await geometry(),beforeMenu,`${fixture.game_type}: zero overlay layout shift`);
        const overlay=await page.locator('#mentionOptions').evaluate(e=>{
          const r=e.getBoundingClientRect(),i=document.querySelector('#chatInput').getBoundingClientRect();
          return {position:getComputedStyle(e).position,right:r.right,inputRight:i.right,bottom:r.bottom,inputTop:i.top,
            unclipped:e.contains(document.elementFromPoint(r.left+10,r.bottom-10)),font:parseFloat(getComputedStyle(e.firstElementChild).fontSize),inputFont:parseFloat(getComputedStyle(document.querySelector('#chatInput')).fontSize)};
        });
        assert.equal(overlay.position,'absolute');
        assert.ok(Math.abs(overlay.right-overlay.inputRight)<1,JSON.stringify(overlay));
        assert.ok(Math.abs(overlay.bottom+4-overlay.inputTop)<1);
        assert.ok(overlay.unclipped,fixture.game_type+' overlay clipping');
        assert.ok(overlay.font<overlay.inputFont);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,fixture.game_type);
        if (['aeroplane_chess','xiangqi','uno'].includes(fixture.game_type)) {
          await page.screenshot({path:`${output}/global-${fixture.game_type}-${width}.png`,fullPage:true});
        }
        await input.press('Escape');
      }
      await render(fixtures.playing);
      if (width < 600) {
        // Reduced viewport approximates available space above a software keyboard.
        await page.setViewportSize({width,height:430});
        await page.evaluate(()=>window.run(`room.participants.push(...['303','404'].map(id=>({player_id:id,display_name:'额外候选'+id,participant_kind:'bound_machine',handle:id})));`));
        await input.scrollIntoViewIfNeeded(); await input.fill('@');
        const menu=await page.locator('#mentionOptions').boundingBox();
        assert.ok(menu.y>=0 && menu.y+menu.height<=430);
        assert.ok(await page.locator('#mentionOptions').evaluate(e=>{
          const r=e.getBoundingClientRect();
          return [r.left+8,r.right-8].every(x=>[r.top+12,r.bottom-12].every(y=>e.contains(document.elementFromPoint(x,y))));
        }), 'keyboard-space overlay is not occluded');
        assert.ok(await page.locator('#mentionOptions').evaluate(e=>e.scrollHeight>e.clientHeight),'long list scrolls within bounded overlay');
        const stable=await geometry();
        await input.press('ArrowUp');
        assert.deepEqual(await geometry(),stable,'keyboard scrolls menu only');
        assert.ok(await page.locator('#mentionOptions').evaluate(e=>e.scrollTop>0));
        await page.screenshot({path:`${output}/keyboard-space-${width}.png`});
        // 200% enlargement: long-name options remain inside the input width.
        await page.evaluate(()=>document.body.style.zoom='2');
        await input.scrollIntoViewIfNeeded();
        const bounds=await page.locator('#mentionOptions').boundingBox();
        const compose=await page.locator('.chat-compose').boundingBox();
        assert.ok(bounds.width<=compose.width+1);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    fs.writeFileSync(`${output}/measurements.json`, JSON.stringify(measurements,null,2));
    const scope = process.env.DUEL_UI_INVITE_ONLY ? 'invitation states' : '25 games, chat scroll/poll/trim/switch';
    console.log(`PASS Chromium ${scope} x 360/428/1280: zero mention layout shift, typography, clipping, keyboard, waiting/playing, takeover controls. Screenshots: ${output}`);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
