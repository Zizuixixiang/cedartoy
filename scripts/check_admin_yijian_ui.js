'use strict';
// Game-only grants remain separate from site-admin permissions.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const admin=fs.readFileSync('admin.html','utf8'),home=fs.readFileSync('index.html','utf8');
assert(!/autochessDashboard|autochessAdmin|\/autochess\/api\/stats/.test(admin));
assert(admin.includes('duelDashboard')&&admin.includes('turtleDashboard'));
assert.equal((home.match(/data-autochess-stats-entry/g)||[]).length,3); // two mounts + renderer
assert(!/autochessStatsModal|autochessStatsContent|data-autochess-range|loadAutochessStats/.test(home));
assert.equal((home.match(/data-autochess-stats type="button">管理统计/g)||[]).length,2);
assert(home.includes('window.location.href = "/autochess/#management"'));
const game=fs.readFileSync('vendor/autochess/cedartoy/main.tsx','utf8');
assert(game.includes("useState(()=>location.hash==='#management')"));
assert(game.includes('management&&config?.author_stats&&!ai'));
assert(game.includes("if(location.hash==='#management')history.replaceState(null,'',location.pathname+location.search)"));
const controller=home.slice(home.indexOf('    const autochessStats ='),home.indexOf('    let craftAuthorIdentity ='));
assert(controller.includes('JSON.stringify({op: "access"})'));
assert(controller.includes('data.allowed === true'));
assert(controller.includes('autochessStats.identity === candleIdentity()'));
assert(controller.includes('autochessStats.access?.abort();'));
assert(!/is_admin|ai_id|player_id/.test(controller));
for(const [,source] of home.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(source);
console.log('Homepage game-only stats entry, server grant guards and inline syntax passed.');
