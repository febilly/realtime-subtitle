# vr_overlay — SteamVR subtitle overlay client (Rust)

Vendored from [RinBridge](https://github.com/KKTIME2024/RinBridge) (AGPL-3.0, see
[ACKNOWLEDGMENTS](../ACKNOWLEDGMENTS.md)). Renders subtitles received from the
desktop WebSocket over `/ws`. The Rust client keeps exactly two independently
replaceable lines: the latest source and the latest translation. A completed
`refine_result` replaces the translation line as soon as it arrives; it does
not wait for the next source sentence.

## Build (Windows)

```bat
cd vr_overlay
cargo build --release
```

Copy `openvr_api.dll` (from your SteamVR install) next to `target\release\RinBridgeOverlay.exe`.

CJK rendering uses system fonts (Microsoft YaHei etc.); the bundled Noto CJK asset lives in the
upstream RinBridge repo and is not vendored.

## Architecture

The Rust overlay is the single owner of the two-line arrangement. It consumes
the desktop's raw `/ws` event stream (`update` / `refine_result` / `clear`) and
computes which source sentence and which translation are visible. There is no
Python-side subtitle mirror and no snapshot hop in the product path.

This matters for the known N→N-1 defect: a translation that arrives after the
next source sentence is attributed to its own sentence by `llm_sentence_id`,
never to whichever sentence happens to be newest.

## Contract

- Reads `--config <manifest.json>` (see `src/manifest.rs` for the schema; `contract_version` must be 6).
- For `bridge_url=ws://127.0.0.1:<port>/ws`, connects as a normal desktop
  WebSocket client (no authentication) and consumes `update`, `refine_result`,
  and `clear` events. This is the **product** protocol.
- The top visible row is translation (`primary_text`); the bottom visible row
  is source/original speech (`secondary_text`). Each row replaces independently.
- `update.non_final_tokens` is the live snapshot and updates either row
  immediately. `update.final_tokens` is an ordered incremental stream with
  explicit separator, replay, and cumulative-prefix rules. A `refine_result`
  updates the translation line immediately and is stale only relative to the
  sentence currently owned by that line.
- Any manifest path **other than** `/ws` selects the authenticated hub snapshot
  protocol (a single `snapshot` message carrying pre-arranged blocks). That
  protocol is retained for standalone hub/probe tests; it is not the desktop
  product path. Startup emits
  `[overlay][BRIDGE] protocol=<snapshot|desktop_ws> vr_ws_path=<bool>` so a real
  run can prove which protocol it selected.
- Emits `EVENT <json>` lines on stderr: `overlay_ready`, `auth_failed`, `connect_failed`, `no_hmd`, `startup_error`.

## Launch

Start the desktop app first (its default `SERVER_PORT` is `8080`), then run:

```bat
vr_overlay\scripts\start-vr-overlay.cmd
```

The launcher writes a contract-v6 manifest with `bridge_url=ws://127.0.0.1:<port>/ws`
and starts `RinBridgeOverlay.exe --config <manifest>`. Pass a non-default port as
the first argument (`start-vr-overlay.cmd 8081`) or use the PowerShell script
directly (`start-vr-overlay.ps1 -Port 8081 -LogLevel DEBUG`).

The executable logs its own identity at startup
(`[overlay][BUILD] exe=... size=... mtime_unix=... version=...`) so a run can
prove which binary is actually executing.

## Verification

```powershell
powershell -ExecutionPolicy Bypass -File .\vr_overlay\scripts\verify.ps1
```

The script distinguishes missing Windows build prerequisites from Rust test or
compilation failures. Automated tests stop at the frame submission interface;
one physical HMD smoke test is still required.
