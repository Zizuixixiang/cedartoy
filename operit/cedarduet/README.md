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
`dist/cedarduet-operit-0.1.0.toolpkg`. It is a standard ZIP whose root contains
`manifest.json`.

For on-device testing, import the `.toolpkg` through Operit, or use the Operit
repository's `tools/toolpkg/debug_toolpkg.py` workflow. This repository's build
script does not install the package or contact a device.

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
- [Operit Compose DSL types](https://github.com/AAswordman/Operit/blob/main/examples/types/compose-dsl.d.ts)
- [Operit network types](https://github.com/AAswordman/Operit/blob/main/examples/types/network.d.ts)
- [Operit file API types](https://github.com/AAswordman/Operit/blob/main/examples/types/files.d.ts)
