@echo off
rem Start the ORIGINAL desktop app (origin/main) visibly.
rem
rem Two Windows-specific hazards are handled here:
rem  1) pywebview uses a SHARED WebView2 user-data folder
rem     (%APPDATA%\pywebview\EBWebView). Orphaned msedgewebview2 processes from
rem     a previous run lock it, and the next start fails with
rem     0x800700AA (ERROR_BUSY) -> a blank/broken window whose buttons do
rem     nothing. We clear this app's webview orphans first.
rem  2) The desktop prints emoji; a GBK console aborts it. Force UTF-8.
setlocal
cd /d "%~dp0..\.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo [prep] clearing orphaned webview / floating-overlay processes ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe'\" | Where-Object { $_.CommandLine -like '*pywebview*EBWebView*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*overlay_window.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo ============================================================
echo  RealtimeSubtitle desktop (origin/main, raw /ws)
echo  Working dir : %CD%
echo  Port        : default 8080 (override with --server-port N)
echo  Keep this window open. Close it to stop the desktop.
echo ============================================================
echo.
python server.py %*
echo.
echo [desktop] exited with code %ERRORLEVEL%
pause
endlocal
