# CedarDuet Operit ToolPkg

V1 ToolPkg for CedarToy's existing CedarDuet service. The package keeps one
server-issued Operit session per `callerCardId`; `callerName` and `chatId` are
sent only as context and are never used as a player identity.

## Build and test

Requirements: Python 3 and Node.js. No npm dependencies are downloaded.

```bash
cd /opt/cedartoy/operit/cedarduet
npm test
npm run build
python3 -m zipfile -l dist/cedarduet-operit-0.1.0.toolpkg
```

The build is deterministic and creates
`dist/cedarduet-operit-0.1.0.toolpkg` and
`dist/cedarduet-operit-test-installer-0.1.0.js`. The `.toolpkg` is a standard
ZIP whose root contains `manifest.json`. The generated `.js` is a normal Operit
sandbox package with the exact `.toolpkg` archive embedded in it.

## Install on a phone without ADB

The package-manager `+` picker in current Operit imports plain `.hjson`, `.js`,
or `.ts` sandbox packages; it is not a direct `.toolpkg` picker. Use the
generated JavaScript installer instead:

1. Put `dist/cedarduet-operit-test-installer-0.1.0.js` anywhere selectable on
   the phone, such as Downloads.
2. In Operit Package Manager, tap the bottom-right `+` and select that `.js`.
3. Make sure `cedarduet_test_installer` is enabled.
4. In a chat, ask Operit: `加载 cedarduet_test_installer，然后调用
   cedarduet_test_installer:install_cedarduet_test。`
5. A successful result says that ToolPkg `org.cedarstar.cedarduet` was
   installed and subpackage `cedarduet` was enabled. The main sidebar should
   then show “双弈”.
6. Call `cedarduet:session_status` to confirm the tools load. A first-time
   tester should get the expected “not logged in” result, then use
   `session_register` or `session_login`.

The installer uses the same current official flow as
`operit_editor:debug_install_toolpkg`: it writes the archive with
`Tools.Files.writeBinary`, verifies the bytes with `readBinary`, dispatches the
explicit `DEBUG_INSTALL_TOOLPKG` broadcast, enables the `cedarduet` subpackage,
and loads it with `usePackage`. It does not use a guessed marketplace API,
download code, ADB, or user-managed Android/data access. Older Operit builds
without these APIs or the debug-install receiver are rejected with an upgrade
message. After installation, the installer sandbox package can be disabled or
removed without removing CedarDuet.

The raw `.toolpkg` remains available for the Operit repository's
`tools/toolpkg/debug_toolpkg.py` developer workflow. This repository's build
script does not install the package, contact a device, or contact production.

## Setup and tools

The optional `CEDARTOY_BASE_URL` environment variable defaults to
`https://toy.cedarstar.org`. Non-HTTPS URLs are rejected except localhost test
URLs.

Call `session_register` for a new machine or `session_login` for an existing
machine. The returned credential is stored under the ToolPkg persistent config
directory, in a file keyed by the current `callerCardId`; it is not returned to
the model. `session_logout` removes and revokes only that Operit credential.

Binding during register/login requires both a logged-in human token and literal
`confirm_binding=true`. `bind_human` provides the same explicit-confirmation
flow later. Neither path uses or consumes a binding code.

The Duel tools are `rooms`, `new`, `join`, `accept`, `reject`, `state`, `move`,
`resign`, `leave`, `rematch`, and `chips`. For wait-capable operations, the
initial action is sent once. Every continuation is a fresh `state` request
containing only `room_id` and `wait=true`, so a move or message is never replayed
by the package.

The main-sidebar “双弈” UI obtains a 60-second, one-use web ticket and embeds the
existing `/duel/` site in a restricted WebView. The long-lived human token is
kept out of the URL and is cleared from UI state after ticket creation.

## Upstream format references

- [Operit ToolPkg format guide](https://github.com/AAswordman/Operit/blob/main/docs/TOOLPKG_FORMAT_GUIDE.md)
- [Operit package examples and JS/TS/HJSON formats](https://github.com/AAswordman/Operit/tree/main/examples)
- [Official `operit_editor` in-app ToolPkg installer](https://github.com/AAswordman/Operit/blob/main/examples/operit_editor.ts)
- [Operit Compose DSL types](https://github.com/AAswordman/Operit/blob/main/examples/types/compose-dsl.d.ts)
- [Operit network types](https://github.com/AAswordman/Operit/blob/main/examples/types/network.d.ts)
- [Operit file API types](https://github.com/AAswordman/Operit/blob/main/examples/types/files.d.ts)
