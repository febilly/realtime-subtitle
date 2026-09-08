# vr_overlay/vendor

Pinned build inputs for packaging the SteamVR overlay with the Windows release.

## openvr_api.dll

- Description: Valve OpenVR Loader DLL (runtime loader for the installed OpenVR/SteamVR runtime).
- File version: 1.1.1
- Company: Valve
- MD5: `c717e0df10265c6427011e1a8375cc6c`
- Provenance: copied out of a SteamVR installation onto this workstation before this file was pinned;
  original SteamVR version unrecorded. Re-pin from the SteamVR install on the Windows VR machine
  (`Steam/steamapps/common/SteamVR/bin/win64/openvr_api.dll`) if the overlay reports a loader mismatch.

Why vendored: GitHub CI runners have no SteamVR, and `cargo build --release` does **not** produce this
DLL (`openvr_sys` ships bindings only). The PyInstaller spec collects the overlay exe from
`vr_overlay/target/release/` and this DLL from here; both are bundled into the release under
`vr_overlay/` next to `RinBridgeOverlay.exe`, matching `config.get_vr_overlay_exe_path()` in frozen runs.

If either input is missing, the spec warns and produces a Python-only executable instead of failing
(see `RealtimeSubtitle.spec`).
