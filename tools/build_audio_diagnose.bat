@echo off
REM Build the desktop-audio diagnostic into a single console exe.
REM Output: dist\AudioDiagnose.exe
setlocal
cd /d "%~dp0.."

set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

"%PY%" -m PyInstaller ^
  --noconfirm --clean --onefile --console ^
  --name AudioDiagnose ^
  --collect-all soundcard ^
  --exclude-module PySide6 --exclude-module shiboken6 ^
  --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module tkinter ^
  --exclude-module matplotlib --exclude-module aiohttp --exclude-module webview ^
  --exclude-module scipy --exclude-module PIL --exclude-module pandas ^
  --workpath build\audio_diagnose ^
  --specpath build\audio_diagnose ^
  tools\audio_diagnose.py

echo.
echo === output: dist\AudioDiagnose.exe ===
endlocal
