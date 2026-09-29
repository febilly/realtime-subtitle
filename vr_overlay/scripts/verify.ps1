$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repoRoot

function Fail-Environment([string]$Message) {
    Write-Error "ENVIRONMENT PREFLIGHT FAILED: $Message" -ErrorAction Continue
    exit 20
}

foreach ($toolName in @('cargo', 'rustc', 'cmake')) {
    if (-not (Get-Command $toolName -ErrorAction SilentlyContinue)) {
        Fail-Environment "missing $toolName on PATH"
    }
}

$msbuildCandidates = @(
    (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe'),
    (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe')
)
$msbuild = $msbuildCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $msbuild) {
    Fail-Environment 'MSBuild.exe was not found in the supported Visual Studio locations'
}

$sdkIncludeCandidates = @(
    (Join-Path ${env:ProgramFiles} 'Windows Kits\10\Include'),
    (Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\Include')
)
$sdkIncludeRoot = $sdkIncludeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $sdkIncludeRoot) {
    Fail-Environment "Windows SDK include root is unavailable: $($sdkIncludeCandidates -join ', ')"
}

$changed = @(
    (git diff --name-only origin/main...HEAD)
    (git diff --name-only)
    (git diff --cached --name-only)
    (git ls-files --others --exclude-standard)
) | Where-Object { $_ -and $_.Trim() }
$outside = $changed | Sort-Object -Unique | Where-Object { $_ -notlike 'vr_overlay/*' }
if ($outside) {
    Write-Error "Rust-only PR boundary violated: $($outside -join ', ')" -ErrorAction Continue
    exit 21
}

git diff --check origin/main...HEAD
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git diff --check --cached
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cargo test --manifest-path vr_overlay/Cargo.toml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cargo build --manifest-path vr_overlay/Cargo.toml --release
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

function Assert-NoStrayDlls([string]$releaseDir) {
    $stray = @(Get-ChildItem -LiteralPath $releaseDir -Filter '*.dll' -File)
    if ($stray.Count -ne 0) {
        throw "release dir contains DLLs: $($stray.Name -join ', ')"
    }
}

function Assert-SingleFilePackage([string]$exe) {
    $stage = Join-Path ([System.IO.Path]::GetTempPath()) `
        ("rin-package-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    Copy-Item -LiteralPath $exe -Destination (Join-Path $stage 'RinBridgeOverlay.exe')
    $files = @(Get-ChildItem -LiteralPath $stage -File -Recurse)
    if ($files.Count -ne 1 -or $files[0].Name -ne 'RinBridgeOverlay.exe') {
        throw "runtime package must contain exactly RinBridgeOverlay.exe; found: $($files.Name -join ', ')"
    }
    return $stage
}

function Assert-ContractProbe([string]$exe) {
    $outputPath = Join-Path ([System.IO.Path]::GetTempPath()) `
        ("rin-contract-" + [guid]::NewGuid().ToString('N') + '.txt')
    $process = $null
    try {
        $process = Start-Process -FilePath $exe `
            -ArgumentList '--check-startup-contract' `
            -Wait -PassThru -NoNewWindow -RedirectStandardOutput $outputPath
        $out = (Get-Content -LiteralPath $outputPath -Raw).Trim()
        if ($process.ExitCode -ne 0 -or $out -ne '{"contract_version":7}') {
            throw "no-sidecar contract probe failed: exit=$($process.ExitCode) output=$out"
        }
    }
    finally {
        if ($process) {
            $process.Dispose()
        }
        if (Test-Path -LiteralPath $outputPath) {
            Remove-Item -LiteralPath $outputPath -Force
        }
    }
}

$releaseDir = Join-Path $repoRoot 'vr_overlay\target\release'
Assert-NoStrayDlls $releaseDir
$stage = Assert-SingleFilePackage (Join-Path $releaseDir 'RinBridgeOverlay.exe')
try {
    Assert-ContractProbe (Join-Path $stage 'RinBridgeOverlay.exe')
}
finally {
    $removed = $false
    for ($attempt = 1; $attempt -le 5 -and -not $removed; $attempt++) {
        try {
            Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction Stop
            $removed = $true
        }
        catch {
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Milliseconds 100
        }
    }
}
