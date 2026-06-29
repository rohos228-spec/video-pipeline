# Проверка /api/fleet/nodes/{id}/pipeline с hub (диагностика «Internal Server Error»).
param(
    [int]$NodeId = 0,
    [string]$Hub = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Get-FleetToken {
    $cred = Join-Path $Root "data\fleet-hub-credentials.txt"
    if (-not (Test-Path $cred)) { return $null }
    $lines = Get-Content $cred -Encoding UTF8
    foreach ($line in $lines) {
        if ($line -match '^\s*token\s*[:=]\s*(.+)\s*$') { return $Matches[1].Trim() }
    }
    return $null
}

$token = Get-FleetToken
$headers = @{}
if ($token) { $headers.Authorization = "Bearer $token" }

Write-Host "Hub: $Hub" -ForegroundColor Cyan
$nodesResp = Invoke-RestMethod -Uri "$Hub/api/fleet/nodes" -Headers $headers
$nodes = @($nodesResp.nodes)
if ($nodes.Count -eq 0 -and $nodesResp.id) { $nodes = @($nodesResp) }
if ($NodeId -le 0 -and $nodes.Count -gt 0) {
    $remote = $nodes | Where-Object { -not $_.is_main } | Select-Object -First 1
    if ($remote) { $NodeId = [int]$remote.id }
    else { $NodeId = [int]$nodes[0].id }
}
if ($NodeId -le 0) {
    Write-Host "No fleet nodes in DB" -ForegroundColor Red
    exit 1
}

$node = $nodes | Where-Object { $_.id -eq $NodeId } | Select-Object -First 1
Write-Host "Node #$NodeId $($node.name) -> $($node.base_url)" -ForegroundColor Yellow

try {
    $pipe = Invoke-RestMethod -Uri "$Hub/api/fleet/nodes/$NodeId/pipeline" -Headers $headers
    Write-Host "OK: $($pipe.count) projects" -ForegroundColor Green
    $pipe.projects | Select-Object -First 5 id, slug, status | Format-Table
} catch {
    Write-Host "FAIL pipeline:" -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message }
    else { Write-Host $_ }
    exit 2
}
