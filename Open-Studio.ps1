# Start Studio from ANY PowerShell folder (uses this script's directory as repo root).
#
#   powershell -ExecutionPolicy Bypass -File "C:\path\to\YOUR\video-pipeline\Open-Studio.ps1"

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Write-Host "ERROR: Open-Studio.ps1 is not inside video-pipeline (no pyproject.toml)." -ForegroundColor Red
    Write-Host "Expected repo, e.g.:" -ForegroundColor Yellow
    Write-Host "  folder with pyproject.toml (your video-pipeline)" -ForegroundColor Yellow
    exit 1
}

Set-Location $Root
Write-Host "Repo: $Root" -ForegroundColor Cyan
Write-Host ""

$start = Join-Path $Root "START-STUDIO.cmd"
if (-not (Test-Path $start)) {
    Write-Host "ERROR: START-STUDIO.cmd missing. Run git pull in this folder." -ForegroundColor Red
    exit 1
}

& $start
exit $LASTEXITCODE
