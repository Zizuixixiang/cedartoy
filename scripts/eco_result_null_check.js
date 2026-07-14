#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..");
const ECO_HTML = path.join(ROOT, "eco.html");
const GAME_IDS = ["rat", "turtle", "snail", "hyacinth", "algae", "ice"];
const SCORE_BY_GAME = { rat: 5, turtle: 48, snail: 4, hyacinth: 3, algae: 28, ice: 65 };
const source = fs.readFileSync(ECO_HTML, "utf8");

function sourceLine(needle) {
  const offset = source.indexOf(needle);
  return offset < 0 ? "?" : String(source.slice(0, offset).split("\n").length);
}

function mockResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return payload; }
  };
}

async function loadPage() {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", error => {
    if (!/Could not load (?:link|img)/.test(error.message)) console.error(`[jsdom] ${error.message}`);
  });
  const dom = await JSDOM.fromFile(ECO_HTML, {
    url: "https://cedartoy.test/eco.html?playtest=1&tune=1&ai_user_id=regression-user",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(window) {
      let rafId = 0;
      const rafCallbacks = new Map();
      window.fetch = async () => mockResponse(401, { message: "test bootstrap" });
      window.requestAnimationFrame = callback => {
        rafId += 1;
        rafCallbacks.set(rafId, callback);
        return rafId;
      };
      window.cancelAnimationFrame = id => rafCallbacks.delete(id);
      window.__flushAnimationFrames = (timestamp = window.performance.now()) => {
        const pending = [...rafCallbacks.values()];
        rafCallbacks.clear();
        pending.forEach(callback => callback(timestamp));
      };
      window.matchMedia = query => ({
        matches: false,
        media: query,
        onchange: null,
        addListener() {},
        removeListener() {},
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() { return false; }
      });
      window.scrollTo = () => {};
      window.HTMLElement.prototype.focus = () => {};
      window.HTMLElement.prototype.setPointerCapture = () => {};
      window.HTMLElement.prototype.releasePointerCapture = () => {};
      window.HTMLElement.prototype.hasPointerCapture = () => false;
    }
  });
  await new Promise(resolve => dom.window.setTimeout(resolve, 0));
  return dom;
}

async function waitFor(find, timeout = 1800) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = find();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  return null;
}

function resultTextSnapshot(window) {
  const result = window.document.querySelector("#eco-game-shell .game-result");
  assert.ok(result, "结算 DOM .game-result 未渲染");
  const allText = [result, ...result.querySelectorAll("*")].map(node => node.textContent);
  const visibleLines = result.textContent.split(/\r?\n/u).map(line => line.trim());
  return { result, allText, visibleLines, rendered: result.textContent };
}

function assertCleanResult(window, scenario) {
  const { result, allText, visibleLines, rendered } = resultTextSnapshot(window);
  const poisoned = allText.find(text => /(?:null|undefined)/iu.test(text));
  assert.equal(poisoned, undefined, `${scenario}: 出现字面空值：${JSON.stringify(poisoned)}`);
  assert.equal(visibleLines.some(line => line === ""), false, `${scenario}: textContent 出现空行`);
  const emptyResultLine = [...result.querySelectorAll(".game-score-line, .game-result-note, .game-effect-line, .game-result-error, .game-instruction")]
    .find(node => node.textContent.trim() === "");
  assert.equal(emptyResultLine, undefined, `${scenario}: 出现空结算行 ${emptyResultLine && emptyResultLine.className}`);
  return rendered.replace(/\s+/gu, " ").trim();
}

async function run() {
  const dom = await loadPage();
  const { window } = dom;
  const results = [];
  const failures = [];

  async function check(name, action, originNeedle) {
    try {
      const detail = await action();
      results.push(`PASS ${name}${detail ? ` :: ${detail}` : ""}`);
    } catch (error) {
      const line = sourceLine(originNeedle);
      failures.push({ name, line, error: error.message });
      results.push(`FAIL ${name} :: eco.html:${line} :: ${error.message}`);
    } finally {
      window.closeGameOverlay();
    }
  }

  async function scenario(name, action, originNeedle) {
    try {
      window.closeGameOverlay();
      await action();
      const rendered = assertCleanResult(window, name);
      results.push(`PASS ${name} :: ${rendered}`);
    } catch (error) {
      const rendered = window.document.querySelector("#eco-game-shell .game-result")?.textContent || "<无结算 DOM>";
      const line = sourceLine(originNeedle);
      failures.push({ name, line, error: error.message, rendered });
      results.push(`FAIL ${name} :: eco.html:${line} :: ${error.message} :: ${JSON.stringify(rendered)}`);
    } finally {
      window.closeGameOverlay();
    }
  }

  await check("版本角标/footer+tune", () => {
    const footerBuild = window.document.querySelector(".site-footer .build-version")?.textContent.trim();
    const tunerBuild = window.document.querySelector(".tuner-panel > summary")?.textContent.trim();
    assert.match(footerBuild || "", /^build-\d{8}-\d{4}$/u, "页脚缺少 build 版本号");
    assert.ok(tunerBuild && tunerBuild.includes(footerBuild), "tune=1 面板未显示同一 build 版本号");
  }, "const BUILD_VERSION");

  await check("打地鼠命中/+1 DOM", async () => {
    window.launchMiniGame("rat", () => {}, null, { playtest: true });
    const activeHole = await waitFor(() => window.document.querySelector(".rat-hole.active"));
    assert.ok(activeHole, "真机时间线未激活田鼠洞");
    activeHole.click();
    const scoreEffect = activeHole.querySelector(".rat-hit-burst .rat-hit-score");
    assert.ok(scoreEffect, "命中后 +1 特效未挂载到被命中的洞");
    assert.equal(scoreEffect.textContent, "+1", "命中飘字内容不正确");
    const actorLayer = Number(window.getComputedStyle(activeHole.querySelector(".rat-actor")).zIndex);
    const holeLayer = Number(window.getComputedStyle(activeHole.querySelector(".rat-hole-art > .game-prop-image")).zIndex);
    assert.ok(actorLayer > holeLayer, `田鼠层级 ${actorLayer} 没有高于洞口层级 ${holeLayer}`);
  }, "function showHitEffect(hole)");

  await check("绿潮素材/正分仅藻类", () => {
    window.launchMiniGame("algae", () => {}, null, { playtest: true });
    const water = window.document.querySelector(".algae-water");
    assert.ok(water, "绿潮场景未挂载");
    water.getBoundingClientRect = () => ({ left: 0, top: 0, right: 360, bottom: 520, width: 360, height: 520 });
    window.__flushAnimationFrames();
    const assetSources = [...water.querySelectorAll("img")].map(image => image.getAttribute("src") || "");
    assert.equal(assetSources.some(src => /(?:芦苇|species\/水藻)/u.test(src)), false, "绿潮场景引用了芦苇状素材");
    const positiveNonAlgae = [...water.querySelectorAll(".algae-floating-object")].find(node => {
      const points = Number(node.dataset.points);
      const asset = node.dataset.asset || "";
      return points > 0 && !/(?:水藻|绿藻|蓝藻|藻团)/u.test(asset);
    });
    assert.equal(positiveNonAlgae, undefined, "非藻类漂浮物被配置为正分");
  }, "function startAlgaeGame(onFinish)");

  await check("黏藻减速两秒", () => {
    window.launchMiniGame("algae", () => {}, null, { playtest: true });
    const water = window.document.querySelector(".algae-water");
    const net = water.querySelector(".algae-net");
    water.getBoundingClientRect = () => ({ left: 0, top: 0, right: 360, bottom: 520, width: 360, height: 520 });
    const originalRandom = window.Math.random;
    let randomCall = 0;
    window.Math.random = () => (randomCall++ % 5 === 0 ? 0.85 : 0.5);
    try {
      const started = window.performance.now();
      window.__flushAnimationFrames(started);
      for (let frame = 1; frame <= 150 && !net.classList.contains("sticky"); frame += 1) {
        window.__flushAnimationFrames(started + frame * 50);
      }
    } finally {
      window.Math.random = originalRandom;
    }
    assert.ok(net.classList.contains("sticky"), "捞到黏藻后网兜没有进入减速态");
    assert.match(window.document.querySelector(".algae-feedback").textContent, /减速 2 秒/u, "黏藻没有明确提示两秒减速");
    assert.equal(window.eval("GAME_TUNING.algae.stickyDuration"), 2000, "黏藻减速时长不是两秒");
  }, "function applyStickySlowdown()");

  await check("福寿螺目标重生规则", () => {
    assert.equal(window.snailTargetRespawns("rock"), false, "抓走的石头仍会重生");
    assert.equal(window.snailTargetRespawns("apple"), false, "抓走的福寿螺仍会重生");
    assert.equal(window.snailTargetRespawns("pond"), false, "误抓的田螺仍会回到原位");
  }, "function snailTargetRespawns(type)");

  await check("水葫芦三档速度", () => {
    const tuning = window.eval("GAME_TUNING.hyacinth");
    assert.ok(tuning.smallHalfSweep > tuning.mediumHalfSweep, "小株没有比中株慢");
    assert.ok(tuning.mediumHalfSweep > tuning.largeHalfSweep, "中株没有比大株慢");
    assert.ok(tuning.largeHalfSweep >= 1000, "最高难度移动条仍然过快");
    return `小/中/大单程 ${tuning.smallHalfSweep}/${tuning.mediumHalfSweep}/${tuning.largeHalfSweep}ms`;
  }, "hyacinth: {");

  await check("绿潮得分校准", () => {
    const tuning = window.eval("GAME_TUNING.algae");
    const playableIntervalSpawns = Math.max(0, Math.ceil(tuning.duration / tuning.spawnInterval) - 1);
    const totalSpawns = tuning.initialSpawnOffsets.length + playableIntervalSpawns;
    const positivePointsPerSpawn = (1 - tuning.debrisChance)
      * (tuning.ordinaryCutoff * tuning.ordinaryPoints
        + (tuning.stickyCutoff - tuning.ordinaryCutoff) * tuning.stickyPoints
        + (1 - tuning.stickyCutoff) * tuning.rarePoints);
    const typicalScore = totalSpawns * (0.65 * positivePointsPerSpawn - 0.25 * tuning.debrisChance * tuning.debrisPenalty);
    const captureRateFor50 = 50 / (totalSpawns * positivePointsPerSpawn);
    assert.ok(typicalScore >= 25 && typicalScore <= 35, `普通玩家模型得分 ${typicalScore.toFixed(1)} 不在 25-35`);
    assert.ok(captureRateFor50 >= 0.85 && captureRateFor50 <= 0.92, `满 50 所需捕获率 ${(captureRateFor50 * 100).toFixed(1)}% 未达到高操作门槛`);
    return `65% 藻团捕获率≈${typicalScore.toFixed(1)}分；避开全部杂物时，50分仍需≈${(captureRateFor50 * 100).toFixed(1)}%正分藻团`;
  }, "algae: {");

  for (const gameId of GAME_IDS) {
    await scenario(
      `演练完赛/${gameId}`,
      () => window.finishPlaytestGame(gameId, SCORE_BY_GAME[gameId]),
      "function finishPlaytestGame(gameId, score)"
    );
    await scenario(
      `演练主动退出/${gameId}`,
      () => {
        window.launchMiniGame(
          gameId,
          score => window.finishPlaytestGame(gameId, score),
          () => window.gameResultView(gameId, { ok: false, message: "演练失败", playtest: true }),
          { playtest: true, snailCount: 12 }
        );
        window.reportGameProgress(SCORE_BY_GAME[gameId]);
        window.requestGameClose();
        const confirm = window.document.querySelector(".game-exit-confirm .game-button.primary");
        assert.ok(confirm, "主动退出确认按钮未挂载");
        confirm.click();
      },
      "function requestGameClose()"
    );
  }

  await scenario("真实模式/401", async () => {
    window.fetch = async (_url, options = {}) => options.method === "POST"
      ? mockResponse(401, { message: "token expired" })
      : mockResponse(401, { message: "refresh denied" });
    await window.settleLiveGame("rat", 4);
  }, "async function settleLiveGame(gameId, score)");

  await scenario("真实模式/ok缺字段", async () => {
    window.fetch = async (_url, options = {}) => options.method === "POST"
      ? mockResponse(200, { ok: true })
      : mockResponse(401, { message: "refresh denied" });
    await window.settleLiveGame("turtle", 45);
  }, "function pondHelpEffect(gameId, score, result, prevented, rejectedSnail)");

  await scenario("真实模式/字段为null", async () => {
    window.fetch = async (_url, options = {}) => options.method === "POST"
      ? mockResponse(200, { ok: true, summary: null, result: null, message: null, effect: null })
      : mockResponse(401, { message: "refresh denied" });
    await window.settleLiveGame("snail", 3);
  }, "function pondHelpEffect(gameId, score, result, prevented, rejectedSnail)");

  results.forEach(line => console.log(line));
  console.log(`SUMMARY ${results.length - failures.length}/${results.length} scenarios passed`);
  if (failures.length) {
    console.log(`ROOT_HINT native Element.append 会把 null 参数转换成字面文本；检查 eco.html:${sourceLine("body.append(")} gameResultView 的可空参数。`);
    process.exitCode = 1;
  }
  dom.window.close();
}

run().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
