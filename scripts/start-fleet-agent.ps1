# Connect this PC to fleet hub as agent and restart Studio.
param(
    [Parameter(Mandatory = $true)]
    [string]$HubUrl,
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$NodeName = "",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $Root

Write-Host "=== start-fleet-agent: $env:COMPUTERNAME ===" -ForegroundColor Cyan
Write-Host "Root: $Root" -ForegroundColor Gray

$HubUrl = $HubUrl.Trim().TrimEnd("/")
$Token = $Token.Trim()
if (-not $NodeName) {
    $NodeName = [System.Net.Dns]::GetHostName()
}

# Verify hub is reachable
try {
    $cfg = Invoke-RestMethod -Uri "$HubUrl/api/fleet/config" -TimeoutSec 8
    Write-Host "Hub OK: role=$($cfg.role) node=$($cfg.node_name) montage_hub=$($cfg.montage_hub)" -ForegroundColor Green
} catch {
    Write-Host "WARN: hub not reachable at $HubUrl - saving config anyway" -ForegroundColor Yellow
    Write-Host "      $($_.Exception.Message)" -ForegroundColor Gray
}

$setArgs = @{
    HubUrl     = $HubUrl
    AgentToken = $Token
    NodeName   = $NodeName
}
& (Join-Path $PSScriptRoot "Set-FleetEnv.ps1") @setArgs

if (-not $NoRestart) {
    Write-Host "Restarting backend ..." -ForegroundColor Cyan
    & (Join-Path $Root "scripts\stop-backend.ps1") -Quiet 2>$null
    Start-Sleep -Seconds 2
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Start-Sleep -Seconds 12
    try {
        $local = Invoke-RestMethod "http://127.0.0.1:8765/api/fleet/config" -TimeoutSec 8
        Write-Host "Local: role=$($local.role) hub=$($local.hub_url) public=$($local.public_url)" -ForegroundColor Green
    } catch {
        Write-Host "Backend starting - check run-backend window" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. Open Studio -> Set (Network tab). Hub: $HubUrl" -ForegroundColor Cyan
