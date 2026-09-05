"use strict";

const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const version = require("../manifest.json").version;
const installerVersionTag = version.toLowerCase().replace(/[^a-z0-9]+/g, "");
const installerPackageName = `cedarduet_test_installer_${installerVersionTag}_v3`;
const toolpkgPath = path.resolve(__dirname, `../dist/cedarduet-operit-${version}.toolpkg`);
const installerPath = path.resolve(
  __dirname,
  `../dist/cedarduet-operit-test-installer-${version}-v3.js`,
);
const packageSource = fs.readFileSync(path.resolve(__dirname, "../packages/cedarduet.js"), "utf8");
const packageMetadata = JSON.parse(/\/\* METADATA\s*\n([\s\S]*?)\n\*\//.exec(packageSource)[1]);
const expectedToolCount = packageMetadata.tools.length;
const expectedArchive = fs.readFileSync(toolpkgPath);
const packagesDir = "/sdcard/Android/data/com.ai.assistance.operit/files/packages";
const targetPath = packagesDir + "/org.cedarstar.cedarduet.toolpkg";
const duplicatePath = packagesDir + "/cedarduet-operit-0.1.1.toolpkg";
const unrelatedPath = packagesDir + "/weather.toolpkg";
const unverifiedPath = packagesDir + "/broken-backup.toolpkg";

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
assert.ok(!installerSource.includes("__CEDARDUET_"), "installer has unresolved build tokens");

const installer = require(installerPath);
assert.strictEqual(typeof installer.install_cedarduet_test, "function");

function makeStoredManifestArchive(packageId, archiveVersion) {
  const name = Buffer.from("manifest.json", "utf8");
  const data = Buffer.from(JSON.stringify({
    schema_version: 1,
    toolpkg_id: packageId,
    version: archiveVersion || "9.9.9",
    main: "main.js",
    subpackages: [],
  }), "utf8");
  const header = Buffer.alloc(30);
  header.writeUInt32LE(0x04034b50, 0);
  header.writeUInt16LE(20, 4);
  header.writeUInt16LE(0, 6);
  header.writeUInt16LE(0, 8);
  header.writeUInt32LE(data.length, 18);
  header.writeUInt32LE(data.length, 22);
  header.writeUInt16LE(name.length, 26);
  return Buffer.concat([header, name, data]);
}

function createHarness(options) {
  const settings = Object.assign({
    existingRuntime: true,
    receiverCompletesAfterPoll: 3,
    corruptTargetReadback: false,
  }, options || {});
  const completed = [];
  const calls = {
    mkdir: [],
    lists: [],
    deletes: [],
    writeBinary: [],
    readBinary: [],
    broadcasts: [],
    sleeps: [],
    enabled: [],
    used: [],
    events: [],
    postBroadcastPackagePolls: 0,
  };
  const files = new Map([
    [targetPath, makeStoredManifestArchive("org.cedarstar.cedarduet", "0.1.1")],
    [duplicatePath, makeStoredManifestArchive("org.cedarstar.cedarduet", "0.1.1")],
    [unrelatedPath, makeStoredManifestArchive("org.example.weather")],
    [unverifiedPath, Buffer.from("not a zip archive", "utf8")],
  ]);
  let archiveWritten = false;
  let broadcastSent = false;
  let receiverComplete = false;
  let containerEnabled = settings.existingRuntime;
  let subpackageEnabled = settings.existingRuntime;

  function packageSnapshot() {
    const packageExists = settings.existingRuntime || archiveWritten;
    if (!packageExists) return [];
    return [
      {
        packageName: "org.cedarstar.cedarduet",
        isBuiltIn: false,
        enabled: containerEnabled,
        toolCount: 0,
      },
      {
        packageName: "cedarduet",
        isBuiltIn: false,
        enabled: subpackageEnabled,
        toolCount: archiveWritten ? expectedToolCount : 12,
      },
    ];
  }

  global.complete = function (value) { completed.push(value); };
  global.Tools = {
    Files: {
      mkdir: async function (target, recursive, environment) {
        calls.mkdir.push({ target, recursive, environment });
        return { successful: true };
      },
      list: async function (target, environment) {
        calls.lists.push({ target, environment });
        return {
          entries: Array.from(files.entries()).map(function ([filePath, data]) {
            return {
              name: filePath.slice(packagesDir.length + 1),
              isDirectory: false,
              size: data.length,
            };
          }),
        };
      },
      deleteFile: async function (filePath, recursive, environment) {
        calls.deletes.push({ filePath, recursive, environment });
        const removed = files.delete(filePath);
        return { successful: removed, details: removed ? "deleted" : "missing" };
      },
      writeBinary: async function (filePath, base64Content, environment) {
        calls.writeBinary.push({ filePath, environment });
        files.set(filePath, Buffer.from(base64Content, "base64"));
        archiveWritten = true;
        calls.events.push("archive_written");
        return { successful: true };
      },
      readBinary: async function (filePath, environment) {
        calls.readBinary.push({ filePath, environment });
        const data = files.get(filePath);
        if (!data) throw new Error("missing mock file: " + filePath);
        if (settings.corruptTargetReadback && filePath === targetPath && archiveWritten) {
          return { contentBase64: "AAAA", size: 3 };
        }
        return { contentBase64: data.toString("base64"), size: data.length };
      },
    },
    System: {
      sendBroadcast: async function (optionsValue) {
        calls.broadcasts.push(optionsValue);
        calls.events.push("broadcast_sent");
        broadcastSent = true;
        return { result: "broadcast sent" };
      },
      sleep: async function (milliseconds) {
        calls.sleeps.push(milliseconds);
      },
      usePackage: async function (packageName) {
        calls.used.push(packageName);
        calls.events.push("use_package");
        if (!receiverComplete) return "Using package: cedarduet\n- cedarduet:session_login";
        return [
          "Using package: cedarduet",
          "- cedarduet:human_login",
          "- cedarduet:human_register",
          "- cedarduet:human_duel_entry",
        ].join("\n");
      },
    },
    SoftwareSettings: {
      listSandboxPackages: async function () {
        if (broadcastSent && !receiverComplete) {
          calls.postBroadcastPackagePolls += 1;
          if (calls.postBroadcastPackagePolls >= settings.receiverCompletesAfterPoll) {
            receiverComplete = true;
            containerEnabled = true;
            subpackageEnabled = true;
            calls.events.push("receiver_complete");
          }
        }
        return { packages: packageSnapshot(), packageLoadErrors: {} };
      },
      setSandboxPackageEnabled: async function (packageName, enabled) {
        calls.enabled.push({ packageName, enabled });
        calls.events.push(`set_enabled:${packageName}:${String(enabled)}`);
        if (packageName === "org.cedarstar.cedarduet") {
          containerEnabled = enabled;
          if (!enabled) subpackageEnabled = false;
        } else if (packageName === "cedarduet") {
          subpackageEnabled = enabled;
          if (enabled) containerEnabled = true;
        }
        return {
          packageName: packageName,
          requestedEnabled: enabled,
          currentEnabled: enabled,
          message: "updated",
        };
      },
    },
  };

  return { completed, calls, files };
}

async function testUpgradeWaitsForAsyncReceiverAndCleansOnlyConfirmedDuplicates() {
  const harness = createHarness({ receiverCompletesAfterPoll: 3 });
  await installer.install_cedarduet_test({});

  assert.strictEqual(harness.completed.length, 1);
  assert.strictEqual(harness.completed[0].success, true);
  assert.strictEqual(harness.calls.postBroadcastPackagePolls >= 3, true);
  assert.strictEqual(
    harness.calls.events.indexOf("receiver_complete") < harness.calls.events.indexOf("use_package"),
    true,
    "installer activated the package before the asynchronous receiver completed",
  );

  assert.deepStrictEqual(harness.files.get(targetPath), expectedArchive);
  assert.strictEqual(harness.files.has(duplicatePath), false);
  assert.strictEqual(harness.files.has(unrelatedPath), true, "unrelated ToolPkg must be preserved");
  assert.strictEqual(harness.files.has(unverifiedPath), true, "unverified archive must be preserved");
  assert.deepStrictEqual(harness.calls.deletes, [{
    filePath: duplicatePath,
    recursive: false,
    environment: "android",
  }]);
  assert.deepStrictEqual(harness.completed[0].data.removed_duplicate_archives, [duplicatePath]);
  assert.deepStrictEqual(
    harness.completed[0].data.preserved_unverified_archives.map(function (entry) { return entry.path; }),
    [unverifiedPath],
  );
  assert.deepStrictEqual(harness.calls.enabled, [
    { packageName: "org.cedarstar.cedarduet", enabled: false },
    { packageName: "cedarduet", enabled: true },
  ]);
  assert.deepStrictEqual(harness.calls.used, ["cedarduet"]);
  assert.deepStrictEqual(harness.calls.broadcasts, [{
    action: "com.ai.assistance.operit.DEBUG_INSTALL_TOOLPKG",
    component: "com.ai.assistance.operit/.core.tools.packTool.ToolPkgDebugInstallReceiver",
    extras: {
      package_name: "org.cedarstar.cedarduet",
      file_path: targetPath,
      reset_subpackage_states: true,
    },
  }]);
  assert.strictEqual(harness.calls.sleeps.includes(1200), true);
  assert.strictEqual(harness.calls.sleeps.includes(600), true);
  assert.strictEqual(harness.completed[0].data.refresh_attempts, 3);
  assert.strictEqual(
    harness.completed[0].data.archive_sha256,
    crypto.createHash("sha256").update(expectedArchive).digest("hex"),
  );
  assert.strictEqual(harness.completed[0].data.archive_size, expectedArchive.length);
}

async function testOldRuntimeCannotSatisfySuccessWhenReceiverNeverCompletes() {
  const harness = createHarness({ receiverCompletesAfterPoll: Number.POSITIVE_INFINITY });
  await installer.install_cedarduet_test({});

  assert.strictEqual(harness.completed.length, 1);
  assert.strictEqual(harness.completed[0].success, false);
  assert.match(harness.completed[0].message, /未在等待窗口内完成/);
  assert.strictEqual(harness.calls.postBroadcastPackagePolls > 1, true);
  assert.deepStrictEqual(harness.calls.used, []);
  assert.deepStrictEqual(harness.calls.enabled, [
    { packageName: "org.cedarstar.cedarduet", enabled: false },
  ]);
}

async function testWritebackMismatchStopsBeforeBroadcast() {
  const harness = createHarness({ corruptTargetReadback: true });
  await installer.install_cedarduet_test({});

  assert.strictEqual(harness.completed.length, 1);
  assert.strictEqual(harness.completed[0].success, false);
  assert.match(harness.completed[0].message, /写回校验失败/);
  assert.deepStrictEqual(harness.calls.broadcasts, []);
  assert.deepStrictEqual(harness.calls.used, []);
}

async function main() {
  await testUpgradeWaitsForAsyncReceiverAndCleansOnlyConfirmedDuplicates();
  await testOldRuntimeCannotSatisfySuccessWhenReceiverNeverCompletes();
  await testWritebackMismatchStopsBeforeBroadcast();
  process.stdout.write("Installer tests passed\n");
}

main().catch(function (error) {
  process.stderr.write((error && error.stack) || String(error));
  process.stderr.write("\n");
  process.exitCode = 1;
});
