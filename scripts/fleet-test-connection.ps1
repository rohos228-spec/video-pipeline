# Проверка fleet API: local pipeline + заголовок Authorization.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

. (Join-Path $Root "scripts\VpWebBind.ps1")

function Get-VpEnvFileValueLocal([string]$Key, [string]$Default = "") {
    Get-VpEnvFileValue -Root $Root -Key $Key -Default $Default
}

$token = Get-VpEnvFileValueLocal "FLEET_AGENT_TOKEN" ""
$port = Get-VpEnvFileValueLocal "WEB_PORT" "8765"
$headers = @{}
if ($token) {
    $headers["Authorization"] = "Bearer $token"
}

Write-Host "==> GET http://127.0.0.1:$port/api/fleet/local/pipeline (без токена)" -ForegroundColor Cyan
try {
    $res = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/local/pipeline" -TimeoutSec 10
    $count = @($res.projects).Count
    Write-Host "OK: projects=$count" -ForegroundColor Green
} catch {
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
    exit 1
}

$hubUrl = Get-VpEnvFileValueLocal "FLEET_HUB_URL" ""
$role = (Get-VpEnvFileValueLocal "FLEET_ROLE" "hub").ToLowerInvariant()
if ($role -eq "hub") {
    Write-Host "==> GET /api/fleet/nodes (hub)" -ForegroundColor Cyan
    try {
        $webUser = Get-VpEnvFileValueLocal "WEB_AUTH_USER" ""
        $webPass = Get-VpEnvFileValueLocal "WEB_AUTH_PASSWORD" ""
        $nodeHeaders = @{}
        if ($webUser -and $webPass) {
            $login = Invoke-RestMethod "http://127.0.0.1:$port/api/auth/login" -Method POST `
                -ContentType "application/json" `
                -Body (@{ username = $webUser; password = $webPass } | ConvertTo-Json)
            if ($login.token) {
                $nodeHeaders["Authorization"] = "Bearer $($login.token)"
            }
        }
        $nodes = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/nodes" -Headers $nodeHeaders -TimeoutSec 10
        Write-Host "OK: nodes=$($nodes.Count)" -ForegroundColor Green
        foreach ($n in $nodes) {
            $selfName = Get-VpEnvFileValueLocal "FLEET_NODE_NAME" ""
            if ($n.name -eq $selfName) { continue }
            if ($n.base_url -match "127\.0\.0\.1|localhost") {
                Write-Host "WARN: $($n.name) has localhost URL $($n.base_url) — fix FLEET_PUBLIC_URL on agent" -ForegroundColor Yellow
            }
            Write-Host "==> pipeline via hub node $($n.name) ($($n.id))" -ForegroundColor Cyan
            $pipe = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/nodes/$($n.id)/pipeline" -Headers $nodeHeaders -TimeoutSec 15
            Write-Host "OK: $($n.name) projects=$(@($pipe.projects).Count)" -ForegroundColor Green
        }
    } catch {
        Write-Host "FAIL hub: $($_.Exception.Message)" -ForegroundColor Red
        if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
        exit 1
    }
}

Write-Host "Fleet test passed." -ForegroundColor Green
