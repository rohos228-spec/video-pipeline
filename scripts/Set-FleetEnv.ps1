# Обновить fleet-переменные в .env (воркер / agent)
param(
    [string]$HubUrl = "",
    [string]$AgentToken = "",
    [string]$NodeName = "",
    [string]$Root = ""
)

if (-not $Root) {
    $Root = Split-Path $PSScriptRoot -Parent
}

Set-Location -LiteralPath $Root
$envFile = Join-Path $Root ".env"
$tokenFile = Join-Path $Root "data\fleet-agent-token.txt"

if (-not (Test-Path (Join-Path $Root "data"))) {
    New-Item -ItemType Directory -Path (Join-Path $Root "data") | Out-Null
}

# Shared token (must match hub FLEET_AGENT_TOKEN)
if ($AgentToken) {
    $token = $AgentToken.Trim()
    Set-Content -LiteralPath $tokenFile -Value $token -Encoding UTF8 -NoNewline
} elseif (Test-Path $tokenFile) {
    $token = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
} else {
    $token = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    Set-Content -LiteralPath $tokenFile -Value $token -Encoding UTF8 -NoNewline
    Write-Host "New FLEET_AGENT_TOKEN -> data\fleet-agent-token.txt (copy to main PC .env)" -ForegroundColor Yellow
}

# LAN IP for this machine
$lanIp = (
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like "192.168.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1
).IPAddress
$tsExe = "C:\Program Files\Tailscale\tailscale.exe"
$tsIp = $null
if (Test-Path $tsExe) {
    try { $tsIp = (& $tsExe ip -4 2>$null).Trim() } catch {}
}
$publicIp = if ($tsIp) { $tsIp } elseif ($lanIp) { $lanIp } else { "127.0.0.1" }
$publicUrl = "http://${publicIp}:8765"
if ($NodeName) {
    $nodeName = $NodeName.Trim()
} else {
    $nodeName = [System.Net.Dns]::GetHostName()
}

$fleetBlock = @"
# === Fleet worker (auto Setup-FleetWorker.ps1) ===
FLEET_ENABLED=true
FLEET_ROLE=agent
FLEET_IS_MAIN=false
FLEET_MONTAGE_HUB=false
FLEET_AUTO_PULL=false
FLEET_HUB_IS_WORKER=true
FLEET_MONTAGE_MAX_PARALLEL=1
FLEET_NODE_NAME=$nodeName
FLEET_PUBLIC_URL=$publicUrl
FLEET_AGENT_TOKEN=$token
WEB_HOST=0.0.0.0
WEB_PORT=8765
"@

if ($HubUrl) {
    $fleetBlock += "`nFLEET_HUB_URL=$($HubUrl.TrimEnd('/'))"
} else {
    $fleetBlock += "`nFLEET_HUB_URL="
}

function Remove-EnvBlock([string]$text, [string]$marker) {
    $pattern = "(?ms)^# === $marker.*?^(?=#[^=]|$|\z)"
    if ($text -match $pattern) {
        return ($text -replace $pattern, "").TrimEnd() + "`n"
    }
    return $text
}

$raw = if (Test-Path $envFile) { Get-Content -LiteralPath $envFile -Raw -Encoding UTF8 } else { "" }
# fix broken first line (BOM / leading space)
$raw = $raw -replace "^\uFEFF?\s*", ""
$raw = Remove-EnvBlock $raw "Fleet worker"
$raw = Remove-EnvBlock $raw "Fleet / Tailscale"
$raw = ($raw.TrimEnd() + "`n`n" + $fleetBlock + "`n")

$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($envFile, $raw, $utf8)
Write-Host "Updated .env: agent $nodeName public=$publicUrl" -ForegroundColor Green
if ($HubUrl) { Write-Host "  FLEET_HUB_URL=$HubUrl" -ForegroundColor Green }
