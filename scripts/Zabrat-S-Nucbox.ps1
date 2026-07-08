# GLAVNY PC (hub): skachat proekt s NucBox i zapustit montazh.
#   powershell -ExecutionPolicy Bypass -File .\scripts\Zabrat-S-Nucbox.ps1 -ProjectId 17

param(
    [int]$ProjectId = 17,
    [string]$NodeName = "nucbox-m6ultra",
    [string]$Base = "http://127.0.0.1:8765",
    [string]$User = "",
    [string]$Password = "",
    [string]$FleetToken = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Read-DotEnvValue([string]$Name) {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return "" }
    $m = Select-String -Path $envFile -Pattern "^\s*$([regex]::Escape($Name))=(.+)$" | Select-Object -Last 1
    if (-not $m) { return "" }
    return $m.Matches.Groups[1].Value.Trim().Trim('"').Trim("'")
}

function Get-FleetToken([string]$Override) {
    if ($Override) { return $Override.Trim() }
    $t = Read-DotEnvValue "FLEET_AGENT_TOKEN"
    if ($t) { return $t }
    $tokenFile = Join-Path $Root "data\fleet-agent-token.txt"
    if (Test-Path $tokenFile) { return (Get-Content -LiteralPath $tokenFile -Raw).Trim() }
    return ""
}

Write-Host "==> Zabrat-S-Nucbox  project #$ProjectId  node=$NodeName" -ForegroundColor Cyan

$cfg = Invoke-RestMethod -Uri "$Base/api/fleet/config" -TimeoutSec 8
if ($cfg.role -ne "hub") {
    Write-Host "FAIL: etot PC role=$($cfg.role). Zapuskaj tolko na GLAVNOM PC." -ForegroundColor Red
    exit 1
}

$headers = @{ "Content-Type" = "application/json" }
$token = Get-FleetToken -Override $FleetToken
if ($token) {
    $headers["Authorization"] = "Bearer $token"
    Write-Host "OK  auth: fleet token" -ForegroundColor Green
} elseif ($cfg.auth_required) {
    if (-not $User) { $User = Read-DotEnvValue "WEB_AUTH_USER" }
    if (-not $Password) { $Password = Read-DotEnvValue "WEB_AUTH_PASSWORD" }
    if (-not $User -or -not $Password) {
        Write-Host "FAIL: nuzhen FLEET_AGENT_TOKEN v .env ili -User -Password" -ForegroundColor Red
        exit 1
    }
    $login = Invoke-RestMethod -Method POST -Uri "$Base/api/auth/login" -Headers $headers `
        -Body (@{ username = $User; password = $Password } | ConvertTo-Json) -TimeoutSec 15
    if (-not $login.token) { throw "Hub login failed" }
    $headers["Authorization"] = "Bearer $($login.token)"
    Write-Host "OK  auth: web login" -ForegroundColor Green
}

$nodes = Invoke-RestMethod -Uri "$Base/api/fleet/nodes" -Headers $headers -TimeoutSec 15
$node = $nodes | Where-Object { $_.name -eq $NodeName } | Select-Object -First 1
if (-not $node) {
    Write-Host "FAIL: node '$NodeName' ne najden. Nodes: $($nodes.name -join ', ')" -ForegroundColor Red
    Write-Host "      Vklyuchi backend na NucBox i podozhdi 30 sek." -ForegroundColor Yellow
    exit 1
}
Write-Host "    node id=$($node.id) status=$($node.status) url=$($node.base_url)" -ForegroundColor Gray
if ($node.status -ne "online") {
    Write-Host "WARN: NucBox offline. Vklyuchi backend na NucBox." -ForegroundColor Yellow
}

Write-Host "==> skachivanie bundle s NucBox (10-40 min dlya bolshogo proekta)..." -ForegroundColor Cyan
Write-Host "    smotri okno backend: fleet pull download XX%" -ForegroundColor Yellow
try {
    $r = Invoke-RestMethod -Method POST -Uri "$Base/api/fleet/nodes/$($node.id)/projects/$ProjectId/pull-to-main" `
        -Headers $headers -Body '{"run_assemble":true}' -TimeoutSec 7200
    Write-Host "OK  $($r | ConvertTo-Json -Compress)" -ForegroundColor Green
    Write-Host "==> GOTOVO. Montazh na etom PC. Log: data\backend.log" -ForegroundColor Green
} catch {
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
    exit 1
}
