# Открыть TCP 8765 для Studio (Tailscale / LAN). Запуск от администратора.
$ErrorActionPreference = "Stop"
$ruleName = "VideoPipeline Studio 8765"

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Firewall rule already exists: $ruleName" -ForegroundColor Green
    exit 0
}

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 8765 `
    -Profile Any | Out-Null

Write-Host "OK: inbound TCP 8765 allowed ($ruleName)" -ForegroundColor Green
