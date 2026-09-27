"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM, VirtualConsole} = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");

async function checkStats(metric, signedIn) {
  let releaseStats;
  let releaseMe;
  const pendingStats = new Promise(resolve => { releaseStats = resolve; });
  const pendingMe = new Promise(resolve => { releaseMe = resolve; });
  const requests = [];
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", error => errors.push(error));
  const dom = new JSDOM(html, {
    url: "https://toy.cedarstar.org/", runScripts: "dangerously", pretendToBeVisual: true, virtualConsole,
    beforeParse(window) {
      window.matchMedia = () => ({matches: true});
      if (signedIn) window.localStorage.setItem("cedartoy_token", "fixture-only");
      window.fetch = async url => {
        requests.push(url);
        let data = {};
        if (url === "/api/games/stats") {
          await pendingStats;
          data = {puzzle_box: {metric_label: "存档数", metric}};
        } else if (url === "/api/auth/me") {
          await pendingMe;
          data = {user: {id: 1, username: "fixture", is_ai: false}, bindings: []};
        } else if (url === "/soup/api/rooms/") data = [];
        return {ok: true, status: 200, json: async () => data};
      };
    },
  });
  const {window} = dom;
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  const byId = id => window.document.getElementById(id);
  const card = () => window.document.querySelector('[data-game="puzzle_box"]');
  try {
    await tick();
    assert.ok(requests.includes("/api/games/stats"), "real page startup must call loadGameStats");
    card().click();
    assert.equal(byId("detailMetric").textContent, "--");
    assert.equal(byId("drawerMetric").textContent, "--");
    assert.ok(byId("gameDrawer").classList.contains("show"));
    releaseStats();
    await tick();
    const check = () => {
      assert.equal(byId("detailMetricLabel").textContent, "存档数");
      assert.equal(byId("drawerMetricLabel").textContent, "存档数");
      assert.equal(byId("detailMetric").textContent, String(metric));
      assert.equal(byId("drawerMetric").textContent, String(metric));
    };
    check();
    // Late authentication, search/filter rerenders and selecting another card
    // must not restore the catalog's original "--" placeholder.
    releaseMe();
    await tick();
    check();
    byId("searchInput").value = "解谜";
    byId("searchInput").dispatchEvent(new window.Event("input"));
    window.document.querySelector('[data-game="soup"]').dispatchEvent(new window.Event("mouseenter"));
    card().click();
    check();
    assert.deepEqual(errors, [], "page initialization and rerenders must not throw");
  } finally { releaseStats(); releaseMe(); window.close(); }
}

(async () => {
  for (const metric of [8, 0, 137]) {
    for (const signedIn of [false, true]) await checkStats(metric, signedIn);
  }
  console.log("Home live stats: real startup fetch, delayed response, card selection, detail/drawer, zero and late auth rerenders passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
