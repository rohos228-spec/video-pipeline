# Hub PC: install Tailscale, login, patch .env with real 100.x IP.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

Write-Host ""
Write-Host "=== HUB: Tailscale setup ===" -ForegroundColor Cyan
Write-Host "Log in with the SAME account as nucbox-m6ultra (100.100.240.106)" -ForegroundColor Yellow
Write-Host ""

if (-not (Get-TailscaleExe)) {
    & (Join-Path $PSScriptRoot "install-tailscale.ps1")
    if (-not (Get-TailscaleExe)) {
        Write-Host "Open Tailscale from Start menu, log in, then run this script again." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host "Opening Tailscale login (browser)..." -ForegroundColor Cyan
$null = Invoke-Tailscale up

Start-Sleep -Seconds 2
$hubIp = Get-TailscaleIp4
if (-not $hubIp) {
    Write-Host ""
    Write-Host "No Tailscale IP yet - finish login in browser, then run:" -ForegroundColor Red
    Write-Host "  tailscale ip -4" -ForegroundColor White
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\setup-hub-tailscale.ps1" -ForegroundColor White
    exit 1
}

$hubUrl = "http://${hubIp}:8765"
$token = "vpHub_k7Nx9mQ2pL5wR8tY4zA1bC6dE3fG0hJ"

Write-Host ""
Write-Host "Hub Tailscale IP: $hubIp" -ForegroundColor Green
Write-Host "Hub Studio URL:   $hubUrl" -ForegroundColor Green

$EnvPath = Join-Path $Root ".env"
$text = Get-Content $EnvPath -Raw -Encoding UTF8
Set-EnvLine -Text ([ref]$text) -Key "FLEET_PUBLIC_URL" -Value $hubUrl
Set-EnvLine -Text ([ref]$text) -Key "FLEET_HUB_URL" -Value $hubUrl
Set-EnvLine -Text ([ref]$text) -Key "WEB_HOST" -Value "0.0.0.0"
Set-Content -LiteralPath $EnvPath -Value $text.TrimEnd() -Encoding UTF8

Write-Host ".env updated (was fake 100.0.0.1)" -ForegroundColor Green
Write-Host ""
Write-Host "Known worker: nucbox-m6ultra -> http://100.100.240.106:8765" -ForegroundColor Cyan
Write-Host ""
Write-Host "On NUC run:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-fleet-worker.ps1 -SkipTailscale -StartStudio" -ForegroundColor White
Write-Host ""
Write-Host "Or on NUC:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\start-fleet-agent.ps1 -HubUrl `"$hubUrl`" -Token `"$token`" -NodeName nucbox-m6ultra" -ForegroundColor White
Write-Host ""
Write-Host "Restart Studio on this hub:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File .\RUN-STUDIO.ps1" -ForegroundColor White
