@echo off
rem B-architecture launcher: connect the Rust overlay to the original desktop
rem raw /ws stream. Start the desktop app first (default port 8080).
rem Usage:  start-vr-overlay.cmd [port]
setlocal
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8080"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-vr-overlay.ps1" -Port %PORT%
endlocal
