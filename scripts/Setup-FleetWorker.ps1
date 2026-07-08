# Configure this PC as fleet worker (child) and connect to hub
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $Root

Write-Host "=== Setup fleet worker: $env:COMPUTERNAME ===" -ForegroundColor Cyan

# Tailscale
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
    Write-Host "Installing Tailscale ..." -ForegroundColor Yellow
    winget install --id Tailscale.Tailscale -e --accept-package-agreements --accept-source-agreements
}

$hubFound = $false
if (Test-Path (Join-Path $PSScriptRoot "Discover-FleetHub.ps1")) {
    & (Join-Path $PSScriptRoot "Discover-FleetHub.ps1")
    if ($LASTEXITCODE -eq 0) { $hubFound = $true }
}

if (-not $hubFound) {
    Write-Host "Hub not auto-detected - writing agent .env without FLEET_HUB_URL yet." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "Set-FleetEnv.ps1")
}

# Rebuild UI if fleet button missing (local dev)
if (Test-Path (Join-Path $Root "web\package.json")) {
    $topbar = Join-Path $Root "web\src\components\shell\topbar.tsx"
    if ((Test-Path $topbar) -and -not (Select-String -Path $topbar -Pattern "setFleetOpen" -Quiet)) {
        Write-Host "WARN: UI missing Network button - git pull or rebuild web" -ForegroundColor Yellow
    }
}

Write-Host "Restarting backend ..." -ForegroundColor Cyan
& (Join-Path $Root "scripts\stop-backend.ps1") -Quiet 2>$null
Start-Sleep -Seconds 2
Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1") -WorkingDirectory $Root

Start-Sleep -Seconds 12
try {
    $cfg = Invoke-RestMethod "http://127.0.0.1:8765/api/fleet/config" -TimeoutSec 5
    Write-Host "Fleet config: role=$($cfg.role) public=$($cfg.public_url) hub=$($cfg.hub_url)" -ForegroundColor Green
} catch {
    Write-Host "Backend not ready yet - check run-backend window" -ForegroundColor Yellow
}

if (-not $hubFound) {
    Write-Host ""
    Write-Host "NEXT:" -ForegroundColor Yellow
    Write-Host "  1) Log into Tailscale (see data\tailscale-login-url.txt if present)"
    Write-Host "  2) Copy data\fleet-agent-token.txt to main PC .env as FLEET_AGENT_TOKEN"
    Write-Host "  3) Run: powershell -File scripts\Discover-FleetHub.ps1"
    Write-Host "  4) Restart backend"
}
