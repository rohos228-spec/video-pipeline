# Bootstrap this PC as fleet worker (agent) and optionally start Studio.
param(
    [string]$HubUrl = "",
    [string]$AgentToken = "",
    [string]$Root = "",
    [switch]$StartStudio,
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Split-Path $PSScriptRoot -Parent
}
Set-Location -LiteralPath $Root

Write-Host "=== bootstrap-fleet-worker: $env:COMPUTERNAME ===" -ForegroundColor Cyan
Write-Host "Root: $Root" -ForegroundColor Gray

if (-not $SkipPull -and (Test-Path (Join-Path $Root ".git"))) {
    Write-Host "git pull ..." -ForegroundColor Cyan
    git pull --ff-only 2>&1 | ForEach-Object { Write-Host $_ }
}

$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
    Write-Host "Installing Tailscale ..." -ForegroundColor Yellow
    winget install --id Tailscale.Tailscale -e --accept-package-agreements --accept-source-agreements
}

$dataDir = Join-Path $Root "data"
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}

$tokenFile = Join-Path $dataDir "fleet-agent-token.txt"
if ($AgentToken) {
    Set-Content -LiteralPath $tokenFile -Value $AgentToken.Trim() -Encoding UTF8 -NoNewline
    Write-Host "Agent token saved -> data\fleet-agent-token.txt" -ForegroundColor Green
} elseif (-not (Test-Path $tokenFile)) {
    throw "Agent token missing. Pass -AgentToken or create data\fleet-agent-token.txt"
}

$hubFound = $false
if ($HubUrl) {
    $HubUrl = $HubUrl.Trim().TrimEnd("/")
    Write-Host "Using hub URL: $HubUrl" -ForegroundColor Green
    & (Join-Path $PSScriptRoot "Set-FleetEnv.ps1") -HubUrl $HubUrl -AgentToken (Get-Content -LiteralPath $tokenFile -Raw).Trim()
    $hubFound = $true
} else {
    $discover = Join-Path $PSScriptRoot "Discover-FleetHub.ps1"
    if (Test-Path $discover) {
        & $discover -AgentToken (Get-Content -LiteralPath $tokenFile -Raw).Trim()
        if ($LASTEXITCODE -eq 0) { $hubFound = $true }
    }
    if (-not $hubFound) {
        Write-Host "Hub not found yet - configuring agent without FLEET_HUB_URL." -ForegroundColor Yellow
        & (Join-Path $PSScriptRoot "Set-FleetEnv.ps1") -AgentToken (Get-Content -LiteralPath $tokenFile -Raw).Trim()
    }
}

if ($StartStudio) {
    Write-Host "Restarting Studio backend ..." -ForegroundColor Cyan
    & (Join-Path $Root "scripts\stop-backend.ps1") -Quiet 2>$null
    Start-Sleep -Seconds 2
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Start-Sleep -Seconds 12
    try {
        $cfg = Invoke-RestMethod "http://127.0.0.1:8765/api/fleet/config" -TimeoutSec 8
        Write-Host "Fleet: role=$($cfg.role) public=$($cfg.public_url) hub=$($cfg.hub_url)" -ForegroundColor Green
    } catch {
        Write-Host "Backend starting - check run-backend window" -ForegroundColor Yellow
    }
}

if (-not $hubFound) {
    Write-Host ""
    Write-Host "Hub offline or not on Tailscale yet. When main PC is up:" -ForegroundColor Yellow
    Write-Host "  powershell -File scripts\Discover-FleetHub.ps1" -ForegroundColor Yellow
    Write-Host "  powershell -File scripts\stop-backend.ps1 -Quiet; powershell -File run-backend.ps1" -ForegroundColor Yellow
}
