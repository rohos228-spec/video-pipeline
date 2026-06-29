# Hub: забрать проект с NucBox (worker) -> import bundle -> очередь монтажа
param(
    [Parameter(Mandatory = $true)]
    [int]$ProjectId,

    [string]$NodeName = "nucbox-m6ultra",
    [int]$NodeId = 0,
    [string]$HubUrl = "",
    [switch]$NoAssemble,
    [int]$PollSec = 8,
    [int]$TimeoutMin = 120
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

function Read-EnvValue {
    param([string]$Key, [string]$Default = "")
    $envPath = Join-Path $Root ".env"
    if (-not (Test-Path $envPath)) { return $Default }
    $map = Read-DotEnv -Path $envPath
    if ($map.ContainsKey($Key) -and $map[$Key]) { return $map[$Key].Trim().Trim('"') }
    return $Default
}

if (-not $HubUrl) {
    $HubUrl = Read-EnvValue "FLEET_HUB_URL" "http://127.0.0.1:8765"
}
$HubUrl = $HubUrl.TrimEnd("/")

$WebUser = Read-EnvValue "WEB_AUTH_USER" "admin"
$WebPass = Read-EnvValue "WEB_AUTH_PASSWORD" "MontageHub2026"

Write-Host "=== Zabrat s NucBox -> Hub ===" -ForegroundColor Cyan
Write-Host "Hub:       $HubUrl" -ForegroundColor Gray
Write-Host "ProjectId: $ProjectId (ID na worker)" -ForegroundColor Gray
Write-Host "Node:      $(if ($NodeId -gt 0) { "id=$NodeId" } else { $NodeName })" -ForegroundColor Gray

try {
    $cfg = Invoke-RestMethod -Uri "$HubUrl/api/fleet/config" -TimeoutSec 20
    Write-Host ("Fleet: role=" + $cfg.role + " montage_hub=" + $cfg.montage_hub) -ForegroundColor Gray
} catch {
    Write-Host "FAIL: hub ne otvechaet na $HubUrl" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

$loginBody = @{ username = $WebUser; password = $WebPass } | ConvertTo-Json -Compress
try {
    $login = Invoke-RestMethod -Uri "$HubUrl/api/auth/login" -Method POST `
        -ContentType "application/json" -Body $loginBody -TimeoutSec 30
} catch {
    Write-Host "FAIL: login ($WebUser)" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
$headers = @{ Authorization = "Bearer $($login.token)" }

try {
    $nodes = @(Invoke-RestMethod -Uri "$HubUrl/api/fleet/nodes" -Headers $headers -TimeoutSec 30)
} catch {
    Write-Host "FAIL: /api/fleet/nodes" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

if ($nodes.Count -eq 0) {
    Write-Host "FAIL: net fleet nodes" -ForegroundColor Red
    exit 1
}

$node = $null
if ($NodeId -gt 0) {
    $node = $nodes | Where-Object { $_.id -eq $NodeId } | Select-Object -First 1
} else {
    $node = $nodes | Where-Object { $_.name -eq $NodeName } | Select-Object -First 1
    if (-not $node) {
        $node = $nodes | Where-Object { $_.name -like "*$NodeName*" } | Select-Object -First 1
    }
    if (-not $node) {
        $node = $nodes | Where-Object { -not $_.is_main -and $_.status -eq "online" } | Select-Object -First 1
    }
}

if (-not $node) {
    Write-Host "FAIL: node ne naiden. Dostupnye:" -ForegroundColor Red
    $nodes | Format-Table id, name, status, base_url -AutoSize
    exit 1
}

Write-Host ("Worker: #{0} {1} ({2}) -> {3}" -f $node.id, $node.name, $node.status, $node.base_url) -ForegroundColor Green

$pullBody = @{ run_assemble = (-not $NoAssemble.IsPresent) } | ConvertTo-Json -Compress
$pullUri = "$HubUrl/api/fleet/nodes/$($node.id)/projects/$ProjectId/pull-to-main"

try {
    $started = Invoke-RestMethod -Uri $pullUri -Method POST -Headers $headers `
        -ContentType "application/json" -Body $pullBody -TimeoutSec 120
} catch {
    Write-Host "FAIL: pull-to-main" -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
    else { Write-Host $_.Exception.Message -ForegroundColor Red }
    exit 1
}

Write-Host ($started | ConvertTo-Json -Compress) -ForegroundColor Yellow
if ($started.reason -eq "already running") {
    Write-Host "Uzhe idet zagruzka — smotri progress v UI (Set / kanvas)." -ForegroundColor Yellow
}

if ($started.local -eq $true) {
    Write-Host "OK: lokalnyj proekt na hub, queued=$($started.queued)" -ForegroundColor Green
    exit 0
}

$deadline = (Get-Date).AddMinutes($TimeoutMin)
Write-Host "Zagruzka bundle (do $TimeoutMin min)..." -ForegroundColor Cyan

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds $PollSec
    try {
        $active = Invoke-RestMethod -Uri "$HubUrl/api/fleet/transfers/active" -Headers $headers -TimeoutSec 30
    } catch {
        Write-Host ("poll error: " + $_.Exception.Message) -ForegroundColor DarkYellow
        continue
    }

    $t = $null
    if ($active.transfers) {
        $t = @($active.transfers) | Where-Object {
            $_.source_project_id -eq $ProjectId -or $_.project_id -eq $ProjectId
        } | Select-Object -First 1
        if (-not $t) {
            $t = @($active.transfers) | Where-Object { $_.source_node -eq $node.name } | Select-Object -First 1
        }
    }

    if ($t) {
        $pct = if ($null -ne $t.percent) { $t.percent } else { "?" }
        $msg = if ($t.message) { $t.message } else { $t.phase }
        Write-Host ("  [{0}%] {1} — {2}" -f $pct, $t.phase, $msg) -ForegroundColor Gray
        if ($t.status -eq "done") {
            Write-Host "OK: import zavershen." -ForegroundColor Green
            if ($t.slug) { Write-Host ("Hub slug: " + $t.slug) -ForegroundColor Green }
            exit 0
        }
        if ($t.status -eq "error") {
            Write-Host ("FAIL: " + $msg) -ForegroundColor Red
            exit 1
        }
        continue
    }

    # Transfer propal iz active — veroyatno gotovo (import na hub s drugim id)
    Write-Host "Active transfer net — prover logi hub / UI Set." -ForegroundColor Yellow
    Write-Host "Esli bundle skachalsya, proekt mozhet byt pod novym # na hub." -ForegroundColor Yellow
    break
}

Write-Host "Timeout ili neizvestnoe sostoyanie — smotri logi Studio na hub." -ForegroundColor Yellow
exit 2
