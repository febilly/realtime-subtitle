@echo off
REM ============================================================
REM  Local build script for RealtimeSubtitle.exe
REM  Mirrors the GitHub Action (.github/workflows/build-and-release.yml)
REM  but builds inside an isolated temp venv so your global Python
REM  environment is never touched.
REM
REM  Usage:
REM    build_local.bat            Build (reuses the temp venv if present)
REM    build_local.bat /fresh     Recreate the venv from scratch, then build
REM    build_local.bat /clean     Delete the temp venv and exit
REM
REM  This file is git-excluded via .git/info/exclude (local only).
REM ============================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "VENV_DIR=%TEMP%\rts-build-venv"
set "PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_STAMP=%VENV_DIR%\.requirements.sha256"

if /i "%~1"=="/clean" (
    echo [clean] Removing "%VENV_DIR%" ...
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
    echo [clean] Done.
    goto :eof
)

if /i "%~1"=="/fresh" (
    if exist "%VENV_DIR%" (
        echo [fresh] Removing existing venv "%VENV_DIR%" ...
        rmdir /s /q "%VENV_DIR%"
    )
)

REM --- Create the venv if it does not exist -------------------
if not exist "%PY%" (
    echo [venv] Creating temp venv at "%VENV_DIR%" ...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [error] Failed to create venv. Is Python on PATH?
        exit /b 1
    )
    echo [deps] Upgrading pip ...
    "%PY%" -m pip install --upgrade pip
    if errorlevel 1 ( echo [error] pip upgrade failed. & exit /b 1 )
) else (
    echo [venv] Reusing existing temp venv at "%VENV_DIR%".
    echo        Run "build_local.bat /fresh" to rebuild dependencies.
)

REM --- Keep the venv in sync with requirements.txt -------------
REM  The venv lives in %TEMP% and survives checkouts, so dependencies added
REM  later (openvr, PySide6, onnxruntime, ...) would otherwise stay missing
REM  until someone remembered to run /fresh. The spec now fails the build
REM  when openvr is absent, which makes a stale venv look like a code
REM  regression. Re-install only when requirements.txt actually changed.
set "REQ_CHANGED="
"%PY%" -c "import hashlib,pathlib,sys;h=hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest();p=pathlib.Path(r'%REQ_STAMP%');sys.exit(0 if p.is_file() and p.read_text(encoding='ascii').strip()==h else 1)"
if errorlevel 1 set "REQ_CHANGED=1"
if not defined REQ_CHANGED goto :deps_ready

echo [deps] requirements.txt changed; installing ...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 ( echo [error] Dependency install failed. & exit /b 1 )

REM --- Recompile the PyInstaller bootloader -------------------
REM  The stock wheel ships a bootloader binary shared by every PyInstaller
REM  build on earth (malware included), so plenty of AV engines flag it on
REM  sight. Rebuilding it here gives the shipped exe a different signature.
REM  Mirrors the same step in .github/workflows/build-and-release.yml.
REM  Requires MSVC build tools; the sdist bundles a prebuilt bootloader too,
REM  so PYINSTALLER_COMPILE_BOOTLOADER is what forces an actual compile.
REM  The reinstall is driven from Python so the pinned version can be read
REM  back without fighting cmd's quoting rules inside a for /f.
echo [deps] Recompiling PyInstaller bootloader from source ...
set "PYINSTALLER_COMPILE_BOOTLOADER=1"
"%PY%" -c "import subprocess,sys,PyInstaller; sys.exit(subprocess.call([sys.executable,'-m','pip','install','--no-binary','pyinstaller','--no-cache-dir','--force-reinstall','--no-deps','pyinstaller==' + PyInstaller.__version__]))"
set "PYINSTALLER_COMPILE_BOOTLOADER="
if errorlevel 1 (
    echo [error] Bootloader compile failed. Are the MSVC build tools installed?
    exit /b 1
)

"%PY%" -c "import hashlib,pathlib;h=hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest();pathlib.Path(r'%REQ_STAMP%').write_text(h,encoding='ascii')"

:deps_ready
REM --- Build -------------------------------------------------
REM  Never inherit these from the shell: build_with_serveraddr.bat uses the
REM  same variables to switch to the hosted/onedir artifact.
set "RTS_ONEDIR="
set "RTS_DIST_NAME="

if exist "dist\RealtimeSubtitle.exe" (
    del /q "dist\RealtimeSubtitle.exe"
    if exist "dist\RealtimeSubtitle.exe" (
        echo [error] Cannot replace dist\RealtimeSubtitle.exe. Close the running app first.
        exit /b 1
    )
)

echo [build] Running PyInstaller ...
"%PY%" build_exe.py
if errorlevel 1 ( echo [error] Build failed. & exit /b 1 )

if not exist "dist\RealtimeSubtitle.exe" (
    echo [error] Expected build output not found: dist\RealtimeSubtitle.exe
    exit /b 1
)

echo.
echo [done] Output: %~dp0dist\RealtimeSubtitle.exe
endlocal
