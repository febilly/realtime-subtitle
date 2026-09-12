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

`openvr_sys` 2.1.3 statically links OpenVR's client binding (`openvr_api64`);
that static loader reads the registered `openvrpaths.vrpath` and loads the
installed SteamVR `vrclient_x64.dll`. Do **not** copy `openvr_api.dll` next to
`RinBridgeOverlay.exe`; no sidecar DLL is needed or supported.

CJK rendering uses system fonts (Microsoft YaHei etc.); the bundled Noto CJK asset lives in the
upstream RinBridge repo and is not vendored.

## Contract

- Reads `--config <manifest.json>` (see `src/manifest.rs` for the schema; `contract_version` must be 6).
- For `bridge_url=ws://127.0.0.1:<port>/ws`, connects as a normal desktop
  WebSocket client and consumes `update`, `refine_result`, and `clear` events;
  no Python-side subtitle mirror is required.
- For `bridge_url=ws://127.0.0.1:<port>/vr_ws`, uses the packaged desktop's
  authenticated snapshot channel. This connection survives recognition
  refreshes and supplies the initial line state and VR calibration. `/vr_ws`
  is not an alias for `/ws`.
- The top visible row is translation (`primary_text`); the bottom visible row
  is source/original speech (`secondary_text`). Each row replaces independently.
- `update.non_final_tokens` is the live snapshot and updates either row
  immediately. `update.final_tokens` is an ordered incremental stream with
  explicit separator, replay, and cumulative-prefix rules. A `refine_result`
  updates the translation line immediately and is stale only relative to the
  sentence currently owned by that line.
- Any manifest path other than `/ws`, including `/vr_ws`, uses the
  authenticated snapshot protocol. Startup emits
  `[overlay][BRIDGE] protocol=<snapshot|desktop_ws> vr_ws_path=<bool>` so a
  real run can prove which protocol it selected.
- Emits `EVENT <json>` lines on stderr: `overlay_ready`, `auth_failed`, `connect_failed`, `no_hmd`, `startup_error`.

## Verification

```powershell
powershell -ExecutionPolicy Bypass -File .\vr_overlay\scripts\verify.ps1
```

The script distinguishes missing Windows build prerequisites from Rust test or
compilation failures. Automated tests stop at the frame submission interface;
one physical HMD smoke test is still required.
