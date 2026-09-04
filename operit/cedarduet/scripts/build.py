#!/usr/bin/env python3
"""Build the CedarDuet ToolPkg and its self-contained Operit test installer."""

import base64
import hashlib
import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "manifest.json",
    "main.js",
    "packages/cedarduet.js",
    "ui/duel/index.ui.js",
)
METADATA_PATTERN = re.compile(r"/\* METADATA\s*\n(.*?)\n\*/", re.DOTALL)
INSTALLER_TEMPLATE = "installer/cedarduet_test_installer.js.in"
INSTALLER_TOKENS = {
    "__CEDARDUET_TOOLPKG_VERSION__",
    "__CEDARDUET_TOOLPKG_SIZE__",
    "__CEDARDUET_TOOLPKG_SHA256__",
    "__CEDARDUET_TOOLPKG_BASE64_CHUNKS__",
}


def validate_sources(manifest):
    if manifest.get("schema_version") != 1:
        raise SystemExit("manifest schema_version must be 1")
    if manifest.get("main") != "main.js":
        raise SystemExit("manifest main must point to main.js")
    subpackages = manifest.get("subpackages") or []
    if not any(
        item.get("id") == "cedarduet"
        and item.get("entry") == "packages/cedarduet.js"
        for item in subpackages
        if isinstance(item, dict)
    ):
        raise SystemExit("manifest must declare the cedarduet subpackage")

    for relative in RUNTIME_FILES:
        if not (ROOT / relative).is_file():
            raise SystemExit(f"missing runtime file: {relative}")

    package_source = (ROOT / "packages/cedarduet.js").read_text(encoding="utf-8")
    match = METADATA_PATTERN.search(package_source)
    if not match:
        raise SystemExit("packages/cedarduet.js is missing METADATA")
    metadata = json.loads(match.group(1))
    declared_tools = {tool.get("name") for tool in metadata.get("tools", [])}
    required_tools = {
        "session_register", "session_login", "session_status", "session_logout",
        "bind_human", "duel_web_ticket", "rooms", "new", "join", "accept",
        "reject", "state", "move", "resign", "leave", "rematch", "chips",
    }
    missing = sorted(required_tools - declared_tools)
    if missing:
        raise SystemExit("missing required tools: " + ", ".join(missing))

    if not (ROOT / INSTALLER_TEMPLATE).is_file():
        raise SystemExit(f"missing installer template: {INSTALLER_TEMPLATE}")


def build_toolpkg(manifest, output_dir):
    output = output_dir / f"cedarduet-operit-{manifest['version']}.toolpkg"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in RUNTIME_FILES:
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (ROOT / relative).read_bytes())
    return output


def build_installer(manifest, toolpkg_output, output_dir):
    archive = toolpkg_output.read_bytes()
    payload = base64.b64encode(archive).decode("ascii")
    chunks = "\n".join(
        f'  "{payload[offset:offset + 100]}",'
        for offset in range(0, len(payload), 100)
    )
    replacements = {
        "__CEDARDUET_TOOLPKG_VERSION__": str(manifest["version"]),
        "__CEDARDUET_TOOLPKG_SIZE__": str(len(archive)),
        "__CEDARDUET_TOOLPKG_SHA256__": hashlib.sha256(archive).hexdigest(),
        "__CEDARDUET_TOOLPKG_BASE64_CHUNKS__": chunks,
    }
    source = (ROOT / INSTALLER_TEMPLATE).read_text(encoding="utf-8")
    for token, value in replacements.items():
        source = source.replace(token, value)
    unresolved = sorted(token for token in INSTALLER_TOKENS if token in source)
    if unresolved:
        raise SystemExit("unresolved installer tokens: " + ", ".join(unresolved))

    output = output_dir / f"cedarduet-operit-test-installer-{manifest['version']}.js"
    output.write_text(source, encoding="utf-8", newline="\n")
    return output


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    validate_sources(manifest)
    output_dir = ROOT / "dist"
    output_dir.mkdir(exist_ok=True)
    toolpkg_output = build_toolpkg(manifest, output_dir)
    installer_output = build_installer(manifest, toolpkg_output, output_dir)
    print(toolpkg_output)
    print(installer_output)


if __name__ == "__main__":
    main()
