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
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'vr_overlay\vendor\openvr_api.dll'))) {
    Fail-Environment 'vr_overlay/vendor/openvr_api.dll is missing'
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
exit $LASTEXITCODE
