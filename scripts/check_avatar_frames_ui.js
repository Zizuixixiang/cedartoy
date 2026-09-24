"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {JSDOM} = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const items = [
  {key: "cedartoy_1w_decoration", name: "1W 小机器人"},
  {key: "cedartoy_1w", name: "1W 万人纪念框"},
];

async function main() {
  let owned = items;
  let selected = items[0].key;
  let failSave = false;
  let failLoad = false;
  let pendingLoad = null;
  const writes = [];
  const user = () => ({id: 1, username: "南杉", is_ai: false, avatar: {type: "emoji", value: "🌲"}, avatar_frame: selected});
  const response = (data, ok = true) => ({ok, status: ok ? 200 : 400, json: async () => data});
  const dom = new JSDOM(html, {
    url: "https://toy.cedarstar.org/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(window) {
      window.localStorage.setItem("cedartoy_token", "test-token");
      window.fetch = async (url, options = {}) => {
        if (url === "/api/auth/avatar-frames") {
          if (options.method === "POST") {
            writes.push(JSON.parse(options.body));
            if (failSave) return response({error: "保存失败测试"}, false);
            selected = JSON.parse(options.body).selected;
            return response({ok: true, user: user()});
          }
          if (pendingLoad) await pendingLoad;
          return failLoad ? response({error: "读取失败测试"}, false) : response({items: owned, selected});
        }
        if (url === "/api/auth/me") return response({user: user(), bindings: []});
        if (url === "/api/auth/avatar") return response({supported: true, type: "emoji"});
        if (url === "/api/announcements") return response({announcements: [], unread_count: 0, authenticated: true});
        return response({});
      };
    },
  });
  const {window} = dom;
  const byId = (id) => window.document.getElementById(id);
  const options = () => [...byId("avatarFrameGrid").querySelectorAll("button")];
  const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
  try {
    for (const script of window.document.querySelectorAll("script:not([src])")) new vm.Script(script.textContent);
    await tick();
    window.renderMine();
    assert.ok(byId("mineAvatarFrameOpen"));
    byId("mineAvatarFrameOpen").click();
    await tick();
    assert.deepEqual(options().map((button) => button.lastElementChild.textContent.trim()), ["无", "1W 小机器人", "1W 万人纪念框"]);
    assert.ok(options()[0].querySelector(".avatar-frame-none"));
    assert.equal(options()[1].getAttribute("aria-pressed"), "true");
    assert.ok(options()[1].querySelector(".avatar-decoration"));
    assert.ok(options()[2].querySelector(".avatar-frame"));
    options()[2].click();
    assert.equal(options()[2].getAttribute("aria-pressed"), "true");
    assert.equal(writes.length, 0, "preselection never sends a write");
    assert.ok(byId("topAvatarOpen").querySelector(".avatar-decoration"), "preselection leaves live avatar unchanged");
    byId("avatarFrameModal").querySelector(".avatar-frame-close").click();
    assert.equal(byId("avatarFrameModal").classList.contains("show"), false);
    await window.openAvatarFrameModal();
    assert.equal(options()[1].getAttribute("aria-pressed"), "true", "close discards draft");
    options()[2].click();
    byId("avatarFrameSubmit").click();
    byId("avatarFrameSubmit").click();
    await tick();
    assert.deepEqual(writes, [{selected: "cedartoy_1w"}], "save sends exactly one write");
    for (const selector of ["#topAvatarOpen", "#mineContent .mine-profile-avatar"]) {
      const avatar = window.document.querySelector(selector);
      assert.ok(avatar.querySelector(".avatar-frame"));
      assert.equal(avatar.querySelector(".avatar-emoji").textContent, "🌲");
    }
    await window.openAvatarFrameModal();
    options()[0].click();
    await window.saveAvatarFrame();
    assert.equal(selected, null);
    assert.equal(byId("topAvatarOpen").querySelector("img"), null);
    for (const close of [
      () => byId("avatarFrameModal").querySelector(".modal-actions [data-close-modal]").click(),
      () => byId("avatarFrameModal").click(),
      () => window.document.dispatchEvent(new window.KeyboardEvent("keydown", {key: "Escape"})),
    ]) {
      await window.openAvatarFrameModal();
      options()[1].click();
      close();
      assert.equal(writes.length, 2, "cancel, backdrop and Escape do not save");
    }
    await window.openAvatarFrameModal();
    options()[1].click();
    failSave = true;
    await window.saveAvatarFrame();
    assert.equal(byId("avatarFrameMsg").textContent, "保存失败测试");
    assert.equal(byId("avatarFrameSubmit").disabled, false);
    assert.equal(selected, null);
    failSave = false;
    await window.saveAvatarFrame();
    assert.equal(selected, items[0].key, "failed saves can retry");
    for (const [selector, size, font] of [["#topAvatarOpen", "30px", "18px"], ["#mineContent .mine-profile-avatar", "52px", "30px"]]) {
      const avatar = window.document.querySelector(selector);
      const css = window.getComputedStyle(avatar);
      assert.deepEqual([css.width, css.height, css.fontSize], [size, size, font]);
      assert.equal(window.getComputedStyle(avatar.querySelector("img")).pointerEvents, "none");
    }
    owned = [];
    selected = null;
    await window.openAvatarFrameModal();
    assert.equal(options().length, 1, "empty inventory offers only none");
    failLoad = true;
    await window.openAvatarFrameModal();
    assert.equal(byId("avatarFrameSubmit").disabled, true);
    assert.equal(byId("avatarFrameMsg").textContent, "读取失败测试");
    failLoad = false;
    let resolveLoad;
    pendingLoad = new Promise((resolve) => {resolveLoad = resolve;});
    const opening = window.openAvatarFrameModal();
    window.closeModals();
    resolveLoad();
    await opening;
    assert.equal(byId("avatarFrameGrid").children.length, 0, "late response cannot restore a closed draft");
    byId("topAvatarOpen").click();
    assert.equal(byId("avatarModal").classList.contains("show"), true, "Emoji editor remains clickable");
    console.log("PASS: JS syntax; inventory/none previews; draft vs save; all close paths; save retry; empty/error/late loads; avatar sizing and Emoji edit");
  } finally {
    window.close();
  }
}

main().catch((err) => {console.error(err); process.exitCode = 1;});
