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

    echo [deps] Installing requirements.txt ...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 ( echo [error] Dependency install failed. & exit /b 1 )
) else (
    echo [venv] Reusing existing temp venv at "%VENV_DIR%".
    echo        Run "build_local.bat /fresh" to rebuild dependencies.
)

REM --- Build -------------------------------------------------
echo [build] Running PyInstaller ...
"%PY%" build_exe.py
if errorlevel 1 ( echo [error] Build failed. & exit /b 1 )

echo.
echo [done] Output: %~dp0dist\RealtimeSubtitle.exe
endlocal
