# Мастер настройки Fleet: роль hub/agent + FLEET_HUB_URL для дочернего ПК.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

. (Join-Path $Root "scripts\VpWebBind.ps1")

Write-Host "=== Fleet setup ===" -ForegroundColor Cyan
Write-Host "Репозиторий: $Root" -ForegroundColor DarkGray
Write-Host ""

$role = Read-Host "Роль этого ПК [hub/agent] (Enter=hub)"
if (-not $role) { $role = "hub" }
$role = $role.Trim().ToLowerInvariant()
if ($role -notin @("hub", "agent")) {
    Write-Host "Неизвестная роль: $role" -ForegroundColor Red
    exit 1
}

Set-VpEnvFileValue -Root $Root -Key "FLEET_ENABLED" -Value "true"
Set-VpEnvFileValue -Root $Root -Key "FLEET_ROLE" -Value $role
Set-VpEnvFileValue -Root $Root -Key "WEB_HOST" -Value "0.0.0.0"

if ($role -eq "hub") {
    Set-VpEnvFileValue -Root $Root -Key "FLEET_IS_MAIN" -Value "true"
    $ts = Get-VpTailscaleIPv4
    if ($ts) {
        $port = (Get-VpWebBindConfig -Root $Root).WebPort
        Set-VpEnvFileValue -Root $Root -Key "FLEET_PUBLIC_URL" -Value "http://${ts}:$port"
        Set-VpEnvFileValue -Root $Root -Key "FLEET_HUB_URL" -Value "http://${ts}:$port"
    }
    Write-Host "Hub настроен. Перезапустите Studio." -ForegroundColor Green
} else {
    Set-VpEnvFileValue -Root $Root -Key "FLEET_IS_MAIN" -Value "false"
    $hubIp = Read-Host "Tailscale IP главного ПК (например 100.72.202.35)"
    $hubIp = $hubIp.Trim().Replace("http://", "").Replace("https://", "").Split("/")[0]
    if (-not $hubIp) {
        Write-Host "IP не задан" -ForegroundColor Red
        exit 1
    }
    if ($hubIp -notmatch ":") { $hubIp = "${hubIp}:8765" }
    if ($hubIp -notmatch "^https?://") { $hubIp = "http://$hubIp" }
    Set-VpEnvFileValue -Root $Root -Key "FLEET_HUB_URL" -Value $hubIp
    $null = Ensure-VpFleetNetworkEnv -Root $Root
    Write-Host "Agent настроен: FLEET_HUB_URL=$hubIp" -ForegroundColor Green
    Write-Host "Дальше: git pull origin main + перезапуск Studio" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Готово. Проверка: scripts\fleet-diag.ps1 (на hub после запуска бэкенда)" -ForegroundColor DarkGray
