"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const manifestPath = path.join(root, "manifest.json");
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const archivePath = path.join(
  root,
  "dist",
  `cedarduet-operit-${manifest.version}.toolpkg`,
);

// ToolPkgManifest and ToolPkgManifestSubpackage in Operit's current
// ToolPkgParser.kt. Unknown top-level keys are tolerated by Operit, but this
// package deliberately emits only fields that the parser consumes.
const OPERIT_MANIFEST_FIELDS = new Set([
  "schema_version",
  "toolpkg_id",
  "version",
  "main",
  "display_name",
  "description",
  "logo",
  "author",
  "enabled_by_default",
  "subpackages",
  "resources",
  "wasm_modules",
  "workflow_templates",
  "workspace_templates",
]);
const PACKAGE_MANIFEST_FIELDS = [
  "description",
  "display_name",
  "main",
  "schema_version",
  "subpackages",
  "toolpkg_id",
  "version",
];
const SUBPACKAGE_FIELDS = ["entry", "id"];
const EXPECTED_ARCHIVE_ENTRIES = [
  "manifest.json",
  "main.js",
  "packages/cedarduet.js",
  "ui/duel/index.ui.js",
];

function normalizeOperitEntryPath(rawPath) {
  const normalized = String(rawPath).replace(/\\/g, "/").trim().replace(/^\/+/, "");
  if (!normalized || normalized.includes("..")) return null;
  return normalized;
}

function crc32(data) {
  if (!crc32.table) {
    crc32.table = Array.from({ length: 256 }, function (_, index) {
      let value = index;
      for (let bit = 0; bit < 8; bit += 1) {
        value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
      }
      return value >>> 0;
    });
  }
  let crc = 0xffffffff;
  for (const byte of data) {
    crc = crc32.table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function parseOfficialStoredZip(archive) {
  assert.ok(archive.length >= 22, "archive is too short for ZIP EOCD");
  const eocdOffset = archive.length - 22;
  assert.strictEqual(archive.readUInt32LE(eocdOffset), 0x06054b50, "EOCD must be last");
  assert.strictEqual(archive.readUInt16LE(eocdOffset + 4), 0, "multi-disk ZIP is unsupported");
  assert.strictEqual(archive.readUInt16LE(eocdOffset + 6), 0, "multi-disk ZIP is unsupported");
  const diskEntries = archive.readUInt16LE(eocdOffset + 8);
  const totalEntries = archive.readUInt16LE(eocdOffset + 10);
  const centralSize = archive.readUInt32LE(eocdOffset + 12);
  const centralOffset = archive.readUInt32LE(eocdOffset + 16);
  assert.strictEqual(archive.readUInt16LE(eocdOffset + 20), 0, "ZIP comment must be empty");
  assert.strictEqual(diskEntries, totalEntries, "all entries must be on one disk");
  assert.strictEqual(centralOffset + centralSize, eocdOffset, "central directory must be contiguous");

  const entries = [];
  let cursor = centralOffset;
  for (let index = 0; index < totalEntries; index += 1) {
    assert.strictEqual(archive.readUInt32LE(cursor), 0x02014b50, "bad central header");
    const versionMadeBy = archive.readUInt16LE(cursor + 4);
    const versionNeeded = archive.readUInt16LE(cursor + 6);
    const flags = archive.readUInt16LE(cursor + 8);
    const method = archive.readUInt16LE(cursor + 10);
    const modifiedTime = archive.readUInt16LE(cursor + 12);
    const modifiedDate = archive.readUInt16LE(cursor + 14);
    const checksum = archive.readUInt32LE(cursor + 16);
    const compressedSize = archive.readUInt32LE(cursor + 20);
    const size = archive.readUInt32LE(cursor + 24);
    const nameLength = archive.readUInt16LE(cursor + 28);
    const extraLength = archive.readUInt16LE(cursor + 30);
    const commentLength = archive.readUInt16LE(cursor + 32);
    const diskStart = archive.readUInt16LE(cursor + 34);
    const internalAttributes = archive.readUInt16LE(cursor + 36);
    const externalAttributes = archive.readUInt32LE(cursor + 38);
    const localOffset = archive.readUInt32LE(cursor + 42);
    const name = archive.subarray(cursor + 46, cursor + 46 + nameLength).toString("utf8");

    // Exact characteristics emitted by the current official pack-toolpkg.mjs.
    assert.strictEqual(versionMadeBy, 20, `${name}: version-made-by must match official packer`);
    assert.strictEqual(versionNeeded, 20, `${name}: version-needed must match official packer`);
    assert.strictEqual(flags, 0, `${name}: ZIP flags must be zero`);
    assert.strictEqual(method, 0, `${name}: entry must use ZIP_STORED`);
    assert.strictEqual(modifiedTime, 0, `${name}: timestamp must match official packer`);
    assert.strictEqual(modifiedDate, 0, `${name}: timestamp must match official packer`);
    assert.strictEqual(compressedSize, size, `${name}: stored sizes must match`);
    assert.strictEqual(extraLength, 0, `${name}: ZIP extra fields are not emitted`);
    assert.strictEqual(commentLength, 0, `${name}: ZIP comments are not emitted`);
    assert.strictEqual(diskStart, 0, `${name}: entry must start on disk zero`);
    assert.strictEqual(internalAttributes, 0, `${name}: internal attributes must be zero`);
    assert.strictEqual(externalAttributes, 0, `${name}: external attributes must be zero`);
    assert.strictEqual(normalizeOperitEntryPath(name), name, `${name}: invalid Operit entry path`);

    assert.strictEqual(archive.readUInt32LE(localOffset), 0x04034b50, `${name}: bad local header`);
    assert.strictEqual(archive.readUInt16LE(localOffset + 4), 20, `${name}: bad local version`);
    assert.strictEqual(archive.readUInt16LE(localOffset + 6), 0, `${name}: local flags must be zero`);
    assert.strictEqual(archive.readUInt16LE(localOffset + 8), 0, `${name}: local entry must be stored`);
    assert.strictEqual(archive.readUInt16LE(localOffset + 10), 0, `${name}: local timestamp mismatch`);
    assert.strictEqual(archive.readUInt16LE(localOffset + 12), 0, `${name}: local timestamp mismatch`);
    assert.strictEqual(archive.readUInt32LE(localOffset + 14), checksum, `${name}: CRC mismatch`);
    assert.strictEqual(archive.readUInt32LE(localOffset + 18), size, `${name}: local size mismatch`);
    assert.strictEqual(archive.readUInt32LE(localOffset + 22), size, `${name}: local size mismatch`);
    const localNameLength = archive.readUInt16LE(localOffset + 26);
    const localExtraLength = archive.readUInt16LE(localOffset + 28);
    assert.strictEqual(localExtraLength, 0, `${name}: local extra field must be empty`);
    const localName = archive
      .subarray(localOffset + 30, localOffset + 30 + localNameLength)
      .toString("utf8");
    assert.strictEqual(localName, name, `${name}: local and central names differ`);
    const dataOffset = localOffset + 30 + localNameLength + localExtraLength;
    const data = archive.subarray(dataOffset, dataOffset + size);
    assert.strictEqual(crc32(data), checksum, `${name}: content CRC mismatch`);
    entries.push({ name: name, data: data, localOffset: localOffset });
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  assert.strictEqual(cursor, centralOffset + centralSize, "central directory size mismatch");
  return entries;
}

function testManifestAgainstCurrentOperitModel() {
  Object.keys(manifest).forEach(function (key) {
    assert.ok(OPERIT_MANIFEST_FIELDS.has(key), `manifest field ignored by current parser: ${key}`);
  });
  assert.deepStrictEqual(Object.keys(manifest).sort(), PACKAGE_MANIFEST_FIELDS);
  assert.strictEqual(manifest.schema_version, 1);
  assert.strictEqual(manifest.toolpkg_id, "org.cedarstar.cedarduet");
  assert.strictEqual(manifest.version, "0.1.2");
  assert.strictEqual(manifest.main, "main.js");
  assert.strictEqual(typeof manifest.display_name, "string");
  assert.ok(manifest.display_name.trim());
  assert.strictEqual(typeof manifest.description, "string");
  assert.ok(manifest.description.trim());
  assert.ok(Array.isArray(manifest.subpackages));
  assert.strictEqual(manifest.subpackages.length, 1);
  const subpackage = manifest.subpackages[0];
  assert.deepStrictEqual(Object.keys(subpackage).sort(), SUBPACKAGE_FIELDS);
  assert.strictEqual(subpackage.id, "cedarduet");
  assert.strictEqual(subpackage.entry, "packages/cedarduet.js");
  assert.ok(normalizeOperitEntryPath(manifest.main));
  assert.ok(normalizeOperitEntryPath(subpackage.entry));
}

function testSubpackageAgainstCurrentOperitParser() {
  const source = fs.readFileSync(path.join(root, manifest.subpackages[0].entry), "utf8");
  const metadataMatch = /\/\*\s*METADATA\s*([\s\S]*?)\*\//.exec(source);
  assert.ok(metadataMatch, "subpackage must have the METADATA block read by parseJsPackage");
  const metadata = JSON.parse(metadataMatch[1].trim());
  assert.strictEqual(metadata.name, manifest.subpackages[0].id);
  assert.ok(metadata.description, "ToolPackage.description is required");
  assert.strictEqual(metadata.enabledByDefault, true, "subpackage must remain enabled by default");
  assert.ok(Array.isArray(metadata.tools) && metadata.tools.length > 0);
  metadata.tools.forEach(function (tool) {
    assert.strictEqual(typeof tool.name, "string");
    assert.ok(tool.name.trim());
    assert.ok(tool.description, `${tool.name}: ToolPackage description is required`);
    assert.ok(Array.isArray(tool.parameters), `${tool.name}: parameters must be an array`);
    const escapedName = tool.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const functionPatterns = [
      new RegExp(`async\\s+function\\s+${escapedName}\\s*\\(`),
      new RegExp(`function\\s+${escapedName}\\s*\\(`),
      new RegExp(`exports\\.${escapedName}\\s*=\\s*(?:async\\s+)?function`),
      new RegExp(`(?:const|let|var)\\s+${escapedName}\\s*=\\s*(?:async\\s+)?\\(`),
      new RegExp(`exports\\.${escapedName}\\s*=\\s*(?:async\\s+)?\\(?`),
    ];
    assert.ok(
      functionPatterns.some(function (pattern) { return pattern.test(source); }),
      `${tool.name}: current Operit function validator cannot find export`,
    );
  });
}

function testMainRegistrationAgainstCurrentOperitBridge(entryNames, packagedMain) {
  const routes = [];
  const navigation = [];
  const sandbox = {
    exports: {},
    require: function (request) {
      throw new Error(`main registration must not preload module: ${request}`);
    },
    ToolPkg: {
      registerUiRoute: function (definition) {
        assert.ok(definition && typeof definition === "object");
        assert.strictEqual(
          typeof definition.screen,
          "string",
          "current JsToolPkgRegistration explicitly supports a string screen path",
        );
        routes.push(JSON.parse(JSON.stringify(definition)));
      },
      registerNavigationEntry: function (definition) {
        navigation.push(JSON.parse(JSON.stringify(definition)));
      },
    },
  };
  vm.runInNewContext(packagedMain, sandbox, { filename: manifest.main });
  assert.strictEqual(typeof sandbox.exports.registerToolPkg, "function");
  assert.strictEqual(sandbox.exports.registerToolPkg(), true);
  assert.strictEqual(routes.length, 1);
  assert.strictEqual(navigation.length, 1);

  const route = routes[0];
  assert.strictEqual(route.id, "duel");
  assert.strictEqual(route.runtime, "compose_dsl");
  assert.strictEqual(route.route, "toolpkg:org.cedarstar.cedarduet:ui:duel");
  assert.strictEqual(route.screen, "ui/duel/index.ui.js");
  const normalizedScreen = normalizeOperitEntryPath(route.screen);
  assert.ok(normalizedScreen, "screen fails ToolPkgArchiveParser.normalizeZipEntryPath");
  assert.ok(entryNames.has(normalizedScreen.toLowerCase()), "registered screen is absent from ZIP");

  const entry = navigation[0];
  assert.strictEqual(entry.id, "duel_sidebar");
  assert.strictEqual(entry.route, route.route);
  assert.strictEqual(entry.surface, "main_sidebar_plugins");
  assert.strictEqual(entry.icon, "SportsEsports", "icon must use the official example string");
  assert.strictEqual(typeof entry.icon, "string");
}

function testArchiveAgainstCurrentOperitLoader() {
  const archive = fs.readFileSync(archivePath);
  const entries = parseOfficialStoredZip(archive);
  assert.deepStrictEqual(entries.map(function (entry) { return entry.name; }), EXPECTED_ARCHIVE_ENTRIES);
  assert.strictEqual(entries[0].name, "manifest.json");
  assert.strictEqual(entries[0].localOffset, 0, "root manifest must be the first local ZIP entry");
  assert.strictEqual(
    entries.filter(function (entry) {
      return entry.name.toLowerCase() === "manifest.json" || entry.name.toLowerCase() === "manifest.hjson";
    }).length,
    1,
    "archive must contain exactly one root manifest",
  );
  const lowerNames = entries.map(function (entry) { return entry.name.toLowerCase(); });
  assert.strictEqual(new Set(lowerNames).size, lowerNames.length, "case-insensitive ZIP paths must be unique");

  const byName = new Map(entries.map(function (entry) { return [entry.name, entry.data]; }));
  EXPECTED_ARCHIVE_ENTRIES.forEach(function (relative) {
    assert.deepStrictEqual(
      byName.get(relative),
      fs.readFileSync(path.join(root, relative)),
      `${relative}: packaged bytes differ from source`,
    );
  });
  assert.deepStrictEqual(JSON.parse(byName.get("manifest.json").toString("utf8")), manifest);
  testMainRegistrationAgainstCurrentOperitBridge(
    new Set(lowerNames),
    byName.get(manifest.main).toString("utf8"),
  );
}

testManifestAgainstCurrentOperitModel();
testSubpackageAgainstCurrentOperitParser();
testArchiveAgainstCurrentOperitLoader();
process.stdout.write("Operit parser compatibility tests passed\n");
