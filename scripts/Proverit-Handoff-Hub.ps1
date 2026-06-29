# Проверка: hub готов принять bundle (import-bundle)
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$hub = "http://100.72.202.35:8765"
if ($env:FLEET_HUB_URL) { $hub = $env:FLEET_HUB_URL.TrimEnd("/") }

Write-Host "Hub: $hub" -ForegroundColor Cyan
try {
    $cfg = Invoke-RestMethod -Uri "$hub/api/fleet/config" -TimeoutSec 15
    Write-Host ("  role=" + $cfg.role + " montage_hub=" + $cfg.montage_hub) -ForegroundColor Gray
} catch {
    Write-Host "FAIL: hub не отвечает" -ForegroundColor Red
    exit 1
}

try {
    Invoke-WebRequest -Method POST -Uri "$hub/api/fleet/import-bundle" -TimeoutSec 5 -ErrorAction Stop | Out-Null
} catch {
    $code = $null
    if ($_.Exception.Response) {
        $code = [int]$_.Exception.Response.StatusCode
    }
    if ($code -eq 401 -or $code -eq 422) {
        Write-Host "OK: /api/fleet/import-bundle есть (нужен token+file — это норма)" -ForegroundColor Green
        exit 0
    }
    if ($code -eq 404) {
        Write-Host "FAIL: hub БЕЗ import-bundle — запусти FLEET-HOTFIX.cmd на главном ПК" -ForegroundColor Red
        exit 1
    }
    Write-Host ("FAIL: import-bundle HTTP " + ($code ?? $_.Exception.Message)) -ForegroundColor Red
    exit 1
}
