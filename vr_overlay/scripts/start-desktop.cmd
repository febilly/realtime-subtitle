@echo off
rem Start the ORIGINAL desktop app (origin/main) visibly on the default port.
rem UTF-8 is forced because the desktop prints emoji to a GBK console otherwise.
setlocal
cd /d "%~dp0..\.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
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
