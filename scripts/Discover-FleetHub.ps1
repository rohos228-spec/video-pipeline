# Найти главный ПК (fleet hub) в LAN или Tailscale и записать FLEET_HUB_URL в .env
param(
    [string]$AgentToken = ""
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $Root

$ts = "C:\Program Files\Tailscale\tailscale.exe"
$candidates = @()

# Tailscale peers
if (Test-Path $ts) {
    $status = & $ts status 2>&1 | Out-String
    if ($status -notmatch "NeedsLogin|Logged out") {
        foreach ($m in [regex]::Matches($status, "\b(100\.\d+\.\d+\.\d+)\b")) {
            $candidates += $m.Groups[1].Value
        }
    }
}

# LAN neighbors (ARP)
foreach ($m in (arp -a | Select-String "192\.168\.\d+\.\d+")) {
    if ($m -match "(192\.168\.\d+\.\d+)") {
        $ip = $Matches[1]
        if ($ip -notmatch "\.255$") { $candidates += $ip }
    }
}

$candidates = $candidates | Select-Object -Unique
Write-Host "Probing $($candidates.Count) hosts for fleet hub on :8765 ..." -ForegroundColor Cyan

$hubUrl = $null
$hubName = $null
$selfIps = @("127.0.0.1", "::1")
if (Test-Path $ts) {
    try {
        $selfIp = (& $ts ip -4 2>$null).Trim()
        if ($selfIp) { $selfIps += $selfIp }
    } catch {}
}
$lanSelf = (
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like "192.168.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1
).IPAddress
if ($lanSelf) { $selfIps += $lanSelf }

foreach ($ip in $candidates) {
    if ($selfIps -contains $ip) { continue }
    $url = "http://${ip}:8765"
    try {
        $cfg = Invoke-RestMethod -Uri "$url/api/fleet/config" -TimeoutSec 3
        $role = [string]$cfg.role
        $montage = [bool]$cfg.montage_hub
        $isMain = [bool]$cfg.is_main
        Write-Host "  $url role=$role montage_hub=$montage is_main=$isMain node=$($cfg.node_name)" -ForegroundColor Gray
        if ($montage -or ($role -eq "hub" -and $isMain)) {
            $hubUrl = $url
            $hubName = $cfg.node_name
            break
        }
    } catch {
        # not a studio host
    }
}

if (-not $hubUrl) {
    Write-Host "Hub not found. Start Studio on main PC or log into Tailscale first." -ForegroundColor Yellow
    if (Test-Path $ts) {
        $login = & $ts status 2>&1 | Select-String "login.tailscale.com" | ForEach-Object { $_.Line -replace ".*(https://login.tailscale.com/\S+).*", '$1' }
        if ($login) {
            Write-Host "Tailscale login: $login" -ForegroundColor Yellow
            $login | Set-Content -Path (Join-Path $Root "data\tailscale-login-url.txt") -Encoding UTF8
        }
    }
    exit 1
}

Write-Host "Found hub: $hubName at $hubUrl" -ForegroundColor Green
$setArgs = @{ HubUrl = $hubUrl }
if ($AgentToken) { $setArgs.AgentToken = $AgentToken }
& (Join-Path $PSScriptRoot "Set-FleetEnv.ps1") @setArgs
exit $LASTEXITCODE
