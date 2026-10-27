'use strict';
// Guard the rejected integration: the game grant must never become a site-admin UI.
const assert=require('node:assert/strict'),fs=require('node:fs');
const admin=fs.readFileSync('admin.html','utf8'),home=fs.readFileSync('index.html','utf8');
assert(!/autochessDashboard|autochessAdmin|\/autochess\/api\/stats/.test(admin));
assert(!/autochessStatsModal|data-autochess-stats|refreshAutochessStatsAccess/.test(home));
assert(admin.includes('duelDashboard')&&admin.includes('turtleDashboard'));
console.log('Game statistics are isolated from site admin and the removed home popup.');
