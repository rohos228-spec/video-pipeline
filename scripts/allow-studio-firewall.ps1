# Allow inbound TCP 8765 for fleet/Tailscale (run as Administrator once).
param([int]$Port = 8765)

$ErrorActionPreference = "Stop"
$ruleName = "Video Pipeline Studio $Port"

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Firewall rule already exists: $ruleName" -ForegroundColor Green
    exit 0
}

Write-Host "Creating firewall rule: $ruleName (TCP $Port inbound)" -ForegroundColor Cyan
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort $Port `
    -Action Allow `
    -Profile Domain,Private,Public | Out-Null

Write-Host "OK - port $Port open for Tailscale/fleet" -ForegroundColor Green
