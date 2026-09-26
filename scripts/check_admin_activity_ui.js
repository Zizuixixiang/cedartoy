"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "../admin.html"), "utf8");
const requests = [];
const timers = [];
const dom = new JSDOM(html, {
  url: "https://toy.cedarstar.org/admin#dashboard",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(window) {
    window.localStorage.setItem("cedartoy_token", "fixture-admin-token");
    window.setInterval = (callback, delay) => { timers.push(delay); return 1; };
    window.clearInterval = () => {};
    window.fetch = async (url) => {
      requests.push(url);
      return {ok: true, status: 200, json: async () => ({
        generated_at: "2026-09-27T00:00:00Z",
        range: {label: new URL(url, window.location.href).searchParams.get("range")},
        overview: {ok: true, games: [
          {game: "eco", name: "瓶中生态<script>bad()</script>", active_users: 3, human_users: 1, ai_users: 2, operations: 6, save_count: 12},
          {game: "duel", name: "双弈", active_users: 0, human_users: 0, ai_users: 0, operations: 0, save_count: null},
        ]},
        duel: {ok: true}, turtle: {ok: true},
      })};
    };
  },
});

(async () => {
  const {window} = dom;
  const document = window.document;
  const settle = () => new Promise(resolve => window.setTimeout(resolve, 0));
  await settle();
  const content = document.getElementById("gameOverviewContent");
  assert.equal(content.querySelectorAll('[role="row"]').length, 3);
  assert.equal(content.querySelectorAll("script").length, 0);
  assert.match(content.textContent, /瓶中生态<script>bad\(\)<\/script>/);
  assert.match(content.textContent, /—/);
  assert.deepEqual(Array.from(content.querySelectorAll('[role="row"]')[1].children).slice(1).map(cell => cell.textContent), ["3", "1", "2", "6", "12"]);
  const select = document.getElementById("dashboardRange");
  assert.deepEqual(Array.from(select.options).map(option => option.value), ["10m", "1h", "6h", "12h", "24h"]);
  for (const range of ["10m", "6h", "12h", "24h"]) {
    select.value = range;
    select.dispatchEvent(new window.Event("change"));
    await settle();
    assert.ok(requests.includes(`/api/admin/activity?range=${range}`));
    for (const id of ["gameOverviewRangeLabel", "duelActivityRangeLabel", "turtleActivityRangeLabel"]) {
      assert.ok(document.getElementById(id).textContent.startsWith(range));
    }
  }
  assert.ok(timers.includes(30000));
  const previousDuel = document.getElementById("duelContent").innerHTML;
  window.renderGameOverview({ok: false, games: [], error: "fixture failure"}, "24h");
  assert.match(content.textContent, /fixture failure/);
  assert.equal(document.getElementById("duelContent").innerHTML, previousDuel);
  assert.ok(document.getElementById("gameOverview").compareDocumentPosition(document.getElementById("duelDashboard")) & window.Node.DOCUMENT_POSITION_FOLLOWING);
  dom.window.close();
  console.log("Admin activity UI: rendering, shared ranges, 30s refresh, escaping and isolated error passed");
})().catch(error => { dom.window.close(); console.error(error); process.exitCode = 1; });
