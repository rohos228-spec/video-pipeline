# Настройка NucBox / fleet worker: .env + firewall + перезапуск Studio
param(
    [Parameter(Mandatory = $true)]
    [string]$HubUrl,
    [string]$AgentToken = "",
    [string]$NodeName = "",
    [switch]$SkipRestart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $Root

$setArgs = @{
    HubUrl = $HubUrl.TrimEnd("/")
}
if ($AgentToken) { $setArgs.AgentToken = $AgentToken }
if ($NodeName) { $setArgs.NodeName = $NodeName }

& (Join-Path $PSScriptRoot "Set-FleetEnv.ps1") @setArgs

Write-Host ""
Write-Host "==> firewall (admin required)" -ForegroundColor Cyan
try {
    & (Join-Path $PSScriptRoot "allow-studio-firewall.ps1")
} catch {
    Write-Host "Firewall rule failed (run PowerShell as Administrator):" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\allow-studio-firewall.ps1" -ForegroundColor Yellow
}

if (-not $SkipRestart) {
    Write-Host ""
    Write-Host "==> restart backend" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "stop-backend.ps1")
    & (Join-Path $Root "apply-local.ps1") -SkipBuild
}
