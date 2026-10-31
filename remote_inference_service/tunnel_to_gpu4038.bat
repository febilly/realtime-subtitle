@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SSH_HOST=gpu4038"
set "REMOTE_ROOT=/share/home/tjfbb/data/yakutan_remote_inference"
set "MAX_WAIT_SECONDS=60"

echo Shared remote inference tunnel: ws://127.0.0.1:18775
echo Checking the unified ASR+MT service on %SSH_HOST%...
ssh %SSH_HOST% "ss -ltn | grep -q ':18775 ' && ss -ltn | grep -q ':18776 '"
if not errorlevel 1 goto :open_tunnel

echo Service is not ready; starting the shared deployment on %SSH_HOST%...
ssh %SSH_HOST% "nohup env REMOTE_INFERENCE_GPU=0 %REMOTE_ROOT%/run_gpu4038.sh > %REMOTE_ROOT%/service-tunnel-$(date +%%Y%%m%%d-%%H%%M%%S).log 2>&1 < /dev/null &"
if errorlevel 1 exit /b 1

set /a WAITED_SECONDS=0
:wait_for_service
ssh %SSH_HOST% "ss -ltn | grep -q ':18775 ' && ss -ltn | grep -q ':18776 '"
if not errorlevel 1 goto :open_tunnel
if !WAITED_SECONDS! GEQ %MAX_WAIT_SECONDS% (
    echo Timed out waiting for the remote service. Check %REMOTE_ROOT%/service-tunnel-*.log.
    exit /b 1
)
timeout /t 1 /nobreak >nul
set /a WAITED_SECONDS+=1
goto :wait_for_service

:open_tunnel
powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 18775 -State Listen -ErrorAction SilentlyContinue) { exit 0 }; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo Local port 18775 is already listening. Reuse the existing tunnel in both apps.
    echo Use Test connection in Realtime Subtitle to verify its service.
    exit /b 0
)
echo Service is ready. Opening the tunnel...
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:18775:127.0.0.1:18775 %SSH_HOST%
