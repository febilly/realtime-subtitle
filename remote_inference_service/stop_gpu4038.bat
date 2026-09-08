@echo off
setlocal EnableExtensions
set "SSH_HOST=gpu4038"
echo Stopping shared remote inference on %SSH_HOST%...
ssh %SSH_HOST% "bash -s" < "%~dp0stop_gpu4038_remote.sh"
if errorlevel 1 exit /b 1
echo Service stopped.
