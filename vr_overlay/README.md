# vr_overlay — SteamVR subtitle overlay client (Rust)

Vendored from [RinBridge](https://github.com/KKTIME2024/RinBridge) (AGPL-3.0, see
[ACKNOWLEDGMENTS](../ACKNOWLEDGMENTS.md)). Renders the snapshot JSON received from the
Python backend over `/vr_ws`.

## Build (Windows)

```bat
cd vr_overlay
cargo build --release
```

Copy `openvr_api.dll` (from your SteamVR install) next to `target\release\RinBridgeOverlay.exe`.

CJK rendering uses system fonts (Microsoft YaHei etc.); the bundled Noto CJK asset lives in the
upstream RinBridge repo and is not vendored.

## Contract

- Reads `--config <manifest.json>` (see `src/manifest.rs` for the schema; `contract_version` must be 6).
- Connects to `bridge_url`, authenticates with `session_token`, then renders `snapshot` messages.
- Emits `EVENT <json>` lines on stderr: `overlay_ready`, `auth_failed`, `connect_failed`, `no_hmd`, `startup_error`.

## Test

```bat
cargo test
```
