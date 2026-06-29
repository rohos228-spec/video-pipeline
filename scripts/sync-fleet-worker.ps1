# Worker (NUC): same git branch as hub + build UI + fleet .env + start agent.
param(
    [string]$HubUrl = "http://100.72.202.35:8765",
    [string]$Token = "vpHub_k7Nx9mQ2pL5wR8tY4zA1bC6dE3fG0hJ",
    [string]$Branch = "",
    [switch]$StartStudio
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

if (-not $Branch) {
    $manifestPath = Join-Path $Root "fleet\manifest.json"
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            if ($m.git_branch) { $Branch = [string]$m.git_branch }
        } catch { }
    }
}
if (-not $Branch) {
    $Branch = (git branch --show-current).Trim()
}
if (-not $Branch) {
    $Branch = "feature/fleet-montage-queue-v161"
}

Write-Host "=== Fleet worker sync ===" -ForegroundColor Cyan
Write-Host "Branch: $Branch" -ForegroundColor White
Write-Host "Hub:    $HubUrl" -ForegroundColor White

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
git -C $Root fetch origin $Branch 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "git fetch failed - is branch pushed to GitHub?" }
git -C $Root checkout -B $Branch "origin/$Branch" 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    git -C $Root checkout -B $Branch 2>&1 | Out-Host
    git -C $Root reset --hard "origin/$Branch" 2>&1 | Out-Host
}
$ErrorActionPreference = $prevEap
if ($LASTEXITCODE -ne 0) { throw "git checkout $Branch failed" }
Write-Host "Git: $(git -C $Root rev-parse --short HEAD)" -ForegroundColor Green

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    throw "No .venv - run install.ps1 once on this PC"
}

Write-Host "Building web UI..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "web")
& npm install
if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
& npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
Pop-Location

& (Join-Path $PSScriptRoot "bootstrap-fleet-worker.ps1") -SkipTailscale -SkipBuild

Write-Host "Starting agent..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "start-fleet-agent.ps1") -HubUrl $HubUrl -Token $Token -NodeName "nucbox-m6ultra"
