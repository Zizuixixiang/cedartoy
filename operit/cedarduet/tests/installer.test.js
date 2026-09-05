"use strict";

const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const version = require("../manifest.json").version;
const installerVersionTag = version.toLowerCase().replace(/[^a-z0-9]+/g, "");
const installerPackageName = `cedarduet_test_installer_${installerVersionTag}`;
const toolpkgPath = path.resolve(__dirname, `../dist/cedarduet-operit-${version}.toolpkg`);
const installerPath = path.resolve(
  __dirname,
  `../dist/cedarduet-operit-test-installer-${version}-v2.js`,
);
const expectedArchive = fs.readFileSync(toolpkgPath);
const completed = [];
const calls = {
  mkdir: [],
  writeBinary: [],
  readBinary: [],
  broadcasts: [],
  enabled: [],
  used: [],
};
let installedBase64 = "";
let broadcastSent = false;

global.complete = function (value) { completed.push(value); };
global.Tools = {
  Files: {
    mkdir: async function (target, recursive, environment) {
      calls.mkdir.push({ target, recursive, environment });
      return { successful: true };
    },
    writeBinary: async function (target, base64Content, environment) {
      calls.writeBinary.push({ target, environment });
      installedBase64 = base64Content;
      return { successful: true };
    },
    readBinary: async function (target, environment) {
      calls.readBinary.push({ target, environment });
      return {
        contentBase64: installedBase64,
        size: Buffer.from(installedBase64, "base64").length,
      };
    },
  },
  System: {
    sendBroadcast: async function (options) {
      calls.broadcasts.push(options);
      broadcastSent = true;
      return { result: "broadcast sent" };
    },
    sleep: async function () {},
    usePackage: async function (packageName) {
      calls.used.push(packageName);
      return "package loaded";
    },
  },
  SoftwareSettings: {
    listSandboxPackages: async function () {
      return {
        packages: broadcastSent
          ? [{ packageName: "org.cedarstar.cedarduet", isBuiltIn: false, enabled: true }]
          : [],
        packageLoadErrors: {},
      };
    },
    setSandboxPackageEnabled: async function (packageName, enabled) {
      calls.enabled.push({ packageName, enabled });
      return { packageName, currentEnabled: enabled, message: "updated" };
    },
  },
};

const installerSource = fs.readFileSync(installerPath, "utf8");
const metadataMatch = /\/\* METADATA\s*\n([\s\S]*?)\n\*\//.exec(installerSource);
assert.ok(metadataMatch, "installer is missing METADATA");
const metadata = JSON.parse(metadataMatch[1]);
assert.strictEqual(metadata.name, installerPackageName);
assert.deepStrictEqual(
  metadata.tools.map(function (tool) { return tool.name; }),
  ["install_cedarduet_test"],
);
assert.strictEqual(
  `${metadata.name}:${metadata.tools[0].name}`,
  `${installerPackageName}:install_cedarduet_test`,
);
assert.ok(!installerSource.includes("__CEDARDUET_TOOLPKG_"), "installer has unresolved build tokens");

const installer = require(installerPath);
assert.strictEqual(typeof installer.install_cedarduet_test, "function");

async function main() {
  await installer.install_cedarduet_test({});
  assert.strictEqual(completed.length, 1);
  assert.strictEqual(completed[0].success, true);

  const writtenArchive = Buffer.from(installedBase64, "base64");
  assert.deepStrictEqual(writtenArchive, expectedArchive);
  assert.strictEqual(
    completed[0].data.archive_sha256,
    crypto.createHash("sha256").update(expectedArchive).digest("hex"),
  );
  assert.strictEqual(completed[0].data.archive_size, expectedArchive.length);

  assert.deepStrictEqual(calls.mkdir, [{
    target: "/sdcard/Android/data/com.ai.assistance.operit/files/packages",
    recursive: true,
    environment: "android",
  }]);
  assert.deepStrictEqual(calls.writeBinary, [{
    target: "/sdcard/Android/data/com.ai.assistance.operit/files/packages/org.cedarstar.cedarduet.toolpkg",
    environment: "android",
  }]);
  assert.deepStrictEqual(calls.broadcasts, [{
    action: "com.ai.assistance.operit.DEBUG_INSTALL_TOOLPKG",
    component: "com.ai.assistance.operit/.core.tools.packTool.ToolPkgDebugInstallReceiver",
    extras: {
      package_name: "org.cedarstar.cedarduet",
      file_path: "/sdcard/Android/data/com.ai.assistance.operit/files/packages/org.cedarstar.cedarduet.toolpkg",
      reset_subpackage_states: true,
    },
  }]);
  assert.deepStrictEqual(calls.enabled, [{ packageName: "cedarduet", enabled: true }]);
  assert.deepStrictEqual(calls.used, ["cedarduet"]);

  const successfulBroadcastCount = calls.broadcasts.length;
  const realReadBinary = global.Tools.Files.readBinary;
  global.Tools.Files.readBinary = async function () {
    return { contentBase64: "AAAA", size: 3 };
  };
  completed.length = 0;
  await installer.install_cedarduet_test({});
  global.Tools.Files.readBinary = realReadBinary;
  assert.strictEqual(completed.length, 1);
  assert.strictEqual(completed[0].success, false);
  assert.match(completed[0].message, /写回校验失败/);
  assert.strictEqual(calls.broadcasts.length, successfulBroadcastCount);
  process.stdout.write("Installer tests passed\n");
}

main().catch(function (error) {
  process.stderr.write((error && error.stack) || String(error));
  process.stderr.write("\n");
  process.exitCode = 1;
});
