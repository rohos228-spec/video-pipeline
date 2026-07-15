# Авто: pull main, WEB_HOST=0.0.0.0 для fleet, перезапуск студии.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

. (Join-Path $Root "scripts\VpWebBind.ps1")

Write-Host "==> git pull origin main" -ForegroundColor Cyan
git -C $Root pull origin main 2>&1 | ForEach-Object { Write-Host $_ }

$patched = Ensure-VpFleetNetworkEnv -Root $Root
if ($patched.Count -gt 0) {
    Write-Host "==> .env обновлён:" -ForegroundColor Green
    $patched | ForEach-Object { Write-Host "    $_" -ForegroundColor Green }
} else {
    Write-Host "==> .env fleet-сеть уже настроен" -ForegroundColor Gray
}

$bind = Get-VpWebBindConfig -Root $Root
Write-Host "==> WEB_HOST=$($bind.WebHost) WEB_PORT=$($bind.WebPort)" -ForegroundColor Cyan

Write-Host "==> Остановка бэкенда..." -ForegroundColor Cyan
$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet -WaitSec 10 2>$null
}

Write-Host "==> Запуск студии..." -ForegroundColor Cyan
& powershell.exe -ExecutionPolicy Bypass -NoProfile -File (Join-Path $Root "scripts\studio.ps1") -Action 1

Start-Sleep -Seconds 3
$addrs = Get-VpListenAddresses -Port ([int]$bind.WebPort)
if (Test-VpListeningAllInterfaces -Port ([int]$bind.WebPort)) {
    Write-Host ""
    Write-Host "OK: порт $($bind.WebPort) слушает 0.0.0.0 (доступен по Tailscale)" -ForegroundColor Green
    Write-Host "    Локально: $($bind.LocalUrl)" -ForegroundColor Green
} elseif ($addrs.Count -gt 0) {
    Write-Host ""
    Write-Host "ВНИМАНИЕ: порт $($bind.WebPort) слушает только: $($addrs -join ', ')" -ForegroundColor Yellow
    Write-Host "    Перезапустите STUDIO.cmd -^> 1 после git pull" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "Бэкенд не слушает :$($bind.WebPort) — см. окно run-backend" -ForegroundColor Red
}
