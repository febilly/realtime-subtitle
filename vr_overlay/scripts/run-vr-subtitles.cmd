@echo off
rem One double-click for Architecture B:
rem   1) start the origin/main desktop in its own visible window
rem   2) wait until its /ws port is accepting connections
rem   3) start the Rust VR overlay against ws://127.0.0.1:<port>/ws
rem
rem Usage:  run-vr-subtitles.cmd [port]      (default port 8080)
rem
rem IMPORTANT: run this from your normal desktop, NOT from a sandbox/headless
rem shell. Starting Rin as a sandbox identity corrupts SteamVR's shared
rem namespace. Start SteamVR (and Virtual Desktop) first.
setlocal
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8080"

echo [1/3] starting desktop in a new window (port %PORT%) ...
start "RealtimeSubtitle desktop" cmd /k "%~dp0start-desktop.cmd" --server-port %PORT%

echo [2/3] waiting for 127.0.0.1:%PORT% to accept connections ...
powershell -NoProfile -Command "$p=%PORT%; for($i=0;$i -lt 90;$i++){ try{ $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',$p); $c.Close(); Write-Host '[ok] port' $p 'is up'; exit 0 }catch{ Start-Sleep -Seconds 1 } }; Write-Host '[x] port' $p 'never came up'; exit 1"
if errorlevel 1 (
    echo [x] desktop port did not come up; aborting.
    pause
    exit /b 1
)

echo [3/3] starting Rust VR overlay ...
echo (this window is the overlay log; keep it open)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-vr-overlay.ps1" -Port %PORT%
echo.
echo [vr] overlay exited.
pause
endlocal
