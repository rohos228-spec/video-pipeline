# NucBox (agent): send project to main PC for montage.
#   .\Otpravit-Na-Hub.ps1
#   .\Otpravit-Na-Hub.ps1 -ProjectId 17

param(
    [int]$ProjectId = 17
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$base = "http://127.0.0.1:8765"

Write-Host "==> Otpravit-Na-Hub  project #$ProjectId" -ForegroundColor Cyan

try {
    $cfg = Invoke-RestMethod -Uri "$base/api/fleet/config" -TimeoutSec 5
} catch {
    Write-Host "FAIL: backend not running. Run .\run-backend.ps1" -ForegroundColor Red
    exit 1
}

if ($cfg.role -ne "agent") {
    Write-Host "WARN: this PC role=$($cfg.role), not agent" -ForegroundColor Yellow
}

$hub = ($cfg.hub_url -as [string]).TrimEnd("/")
if (-not $hub) {
    Write-Host "FAIL: FLEET_HUB_URL empty in .env" -ForegroundColor Red
    exit 1
}

Write-Host "    agent: $($cfg.node_name)  hub: $hub" -ForegroundColor Gray

try {
    Invoke-RestMethod -Uri "$hub/api/fleet/config" -TimeoutSec 8 | Out-Null
    Write-Host "OK  hub online" -ForegroundColor Green
} catch {
    Write-Host "FAIL: hub not responding ($hub)" -ForegroundColor Red
    Write-Host "      On MAIN PC open PowerShell in video-pipeline and run:" -ForegroundColor Yellow
    Write-Host "        .\Obnovit-i-Zapusk.ps1 -RestartOnly -SkipBuild" -ForegroundColor Yellow
    Write-Host "      File extension is .ps1 not .ps1m" -ForegroundColor Yellow
    exit 1
}

try {
    $p = Invoke-RestMethod -Uri "$base/api/projects/$ProjectId" -TimeoutSec 10
} catch {
    Write-Host "FAIL: project #$ProjectId not found" -ForegroundColor Red
    exit 1
}

Write-Host "    project: $($p.slug)  status=$($p.status)" -ForegroundColor Gray

Write-Host "==> run assemble step (send_to_main_pc must be ON in UI)" -ForegroundColor Cyan
try {
    $r = Invoke-RestMethod -Method POST -Uri "$base/api/projects/$ProjectId/steps/assemble/run" -TimeoutSec 120
    Write-Host "OK  status=$($r.status)  handoff=$($r.montage_handoff_pending)" -ForegroundColor Green
    if ($r.montage_handoff_pending) {
        Write-Host "    Sent to hub. Montage will run on main PC." -ForegroundColor Green
    } elseif ($r.status -eq "assembling") {
        Write-Host "    Local montage (send_to_main_pc OFF in assemble params)." -ForegroundColor Yellow
    }
} catch {
    $msg = $_.ErrorDetails.Message
    if (-not $msg) { $msg = $_.Exception.Message }
    Write-Host "FAIL: $msg" -ForegroundColor Red
    exit 1
}

Write-Host "==> done" -ForegroundColor Green
