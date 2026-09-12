<#
.SYNOPSIS
  Launch the Rust VR subtitle overlay against the ORIGINAL desktop (origin/main)
  raw `/ws` stream.

.DESCRIPTION
  This is the B-architecture launcher. The desktop app broadcasts its raw
  subtitle event stream (update / refine_result / clear) on
  ws://<host>:<port>/ws with no authentication. The Rust overlay consumes that
  stream directly and owns the two-line arrangement; there is no Python-side
  mirror and no `/vr_ws` snapshot hop.

  This script writes a contract v6 manifest with bridge_url ending in `/ws` and
  starts RinBridgeOverlay.exe with `--config <manifest>`.

  The desktop server must already be running. Its default port is 8080
  (config.py SERVER_PORT). If the desktop reported a different actual port,
  pass it with -Port.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File vr_overlay\scripts\start-vr-overlay.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File vr_overlay\scripts\start-vr-overlay.ps1 -Port 8081 -LogLevel DEBUG
#>
param(
    [int]$Port = 8080,
    [string]$ServerHost = "127.0.0.1",
    [string]$Exe = "",
    [string]$ManifestPath = "",
    [string]$LogDir = "",
    [string]$LogLevel = "INFO",
    [switch]$Detailed,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$vrOverlayDir = Split-Path -Parent $scriptDir   # ...\vr_overlay
$repoRoot = Split-Path -Parent $vrOverlayDir

if (-not $Exe) {
    $release = Join-Path $vrOverlayDir "target\release\RinBridgeOverlay.exe"
    $debug = Join-Path $vrOverlayDir "target\debug\RinBridgeOverlay.exe"
    if (Test-Path $release) { $Exe = $release }
    elseif (Test-Path $debug) { $Exe = $debug }
    else { throw "RinBridgeOverlay.exe not found. Build it first: cargo build --release --manifest-path vr_overlay/Cargo.toml" }
}
if (-not (Test-Path $Exe)) { throw "RinBridgeOverlay.exe not found at '$Exe'." }

if (-not $LogDir) {
    $LogDir = Join-Path $env:TEMP "rinbridge-overlay-logs"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not $ManifestPath) {
    $ManifestPath = Join-Path $LogDir "overlay_manifest.json"
}

$bridgeUrl = "ws://$ServerHost`:$Port/ws"
$manifest = [ordered]@{
    contract_version     = 6
    app_version          = "0.1.0"
    overlay_instance_id  = "realtime-subtitle-$PID"
    bridge_url           = $bridgeUrl
    session_token        = ""
    parent_pid           = $PID
    startup_deadline_ms  = 30000
    log_dir              = $LogDir
    log_level            = $LogLevel
    locale               = "zh-CN"
    logging_mode         = if ($Detailed) { "detailed" } else { "basic" }
}
$manifestJson = $manifest | ConvertTo-Json
# Write UTF-8 WITHOUT a BOM: the Rust manifest loader rejects a leading BOM.
[System.IO.File]::WriteAllText($ManifestPath, $manifestJson, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "[vr] exe       : $Exe"
Write-Host "[vr] bridge_url: $bridgeUrl"
Write-Host "[vr] manifest  : $ManifestPath"

$overlayArgs = @("--config", $ManifestPath)
if ($NoWait) {
    Start-Process -FilePath $Exe -ArgumentList $overlayArgs | Out-Null
    Write-Host "[vr] launched (detached)"
} else {
    & $Exe @overlayArgs
    Write-Host "[vr] exited with code $LASTEXITCODE"
}
