"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const root = path.resolve(__dirname, "..");
const frontendRoot = path.join(root, "turtle-soup", "frontend");

async function main() {
  const lobbySource = fs.readFileSync(
    path.join(frontendRoot, "src", "pages", "Lobby.jsx"),
    "utf8"
  );
  const roomSource = fs.readFileSync(
    path.join(frontendRoot, "src", "pages", "Room.jsx"),
    "utf8"
  );
  assert.match(
    lobbySource,
    /<RoomIdCopy roomId=\{room\.id\} className="room-code" \/>/,
    "lobby room cards use the room ID itself as the copy target"
  );
  assert.match(
    roomSource,
    /<RoomIdCopy roomId=\{room\.id\} \/>/,
    "room header uses the room ID itself as the copy target"
  );

  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: "https://toy.cedarstar.org/soup/",
    pretendToBeVisual: true,
  });
  const previousGlobals = {};
  const globals = ["window", "document", "navigator", "HTMLElement", "Event", "MouseEvent"];
  for (const name of globals) {
    previousGlobals[name] = global[name];
    global[name] = dom.window[name];
  }
  previousGlobals.IS_REACT_ACT_ENVIRONMENT = global.IS_REACT_ACT_ENVIRONMENT;
  global.IS_REACT_ACT_ENVIRONMENT = true;

  let copiedText = null;
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: {writeText: async (value) => { copiedText = value; }},
  });

  let reactRoot;
  try {
    const esbuild = require(path.join(frontendRoot, "node_modules", "esbuild"));
    const componentPath = path.join(frontendRoot, "src", "components", "RoomIdCopy.jsx");
    const build = await esbuild.build({
      entryPoints: [componentPath],
      bundle: true,
      write: false,
      platform: "node",
      format: "cjs",
      jsx: "automatic",
      external: ["react", "react/jsx-runtime"],
    });
    const componentModule = new Module(componentPath, module);
    componentModule.filename = componentPath;
    componentModule.paths = Module._nodeModulePaths(path.dirname(componentPath));
    componentModule._compile(build.outputFiles[0].text, componentPath);

    const React = require(path.join(frontendRoot, "node_modules", "react"));
    const {createRoot} = require(path.join(frontendRoot, "node_modules", "react-dom", "client"));
    const {default: RoomIdCopy} = componentModule.exports;
    const container = dom.window.document.getElementById("root");
    reactRoot = createRoot(container);

    await React.act(async () => {
      reactRoot.render(React.createElement(RoomIdCopy, {roomId: " #KXXEwLoF "}));
    });

    const roomId = container.querySelector(".room-id-copy");
    assert.ok(roomId, "room ID text is the clickable copy target");
    assert.equal(roomId.textContent, "房间 #KXXEwLoF");
    assert.equal(container.querySelectorAll("button").length, 1, "no separate copy button is added");
    assert.equal(container.querySelectorAll(".room-id-copy").length, 1);

    await React.act(async () => {
      roomId.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true}));
    });

    assert.equal(copiedText, "KXXEwLoF", "clipboard receives the pure, case-preserved room ID");
    assert.equal(roomId.textContent, "已复制", "copy uses inline feedback instead of an alert");
  } finally {
    if (reactRoot) {
      const React = require(path.join(frontendRoot, "node_modules", "react"));
      await React.act(async () => reactRoot.unmount());
    }
    dom.window.close();
    for (const name of globals) {
      if (previousGlobals[name] === undefined) delete global[name];
      else global[name] = previousGlobals[name];
    }
    if (previousGlobals.IS_REACT_ACT_ENVIRONMENT === undefined) {
      delete global.IS_REACT_ACT_ENVIRONMENT;
    } else {
      global.IS_REACT_ACT_ENVIRONMENT = previousGlobals.IS_REACT_ACT_ENVIRONMENT;
    }
  }

  console.log("turtle room ID copy checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
