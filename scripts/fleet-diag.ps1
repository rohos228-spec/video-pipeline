# Fleet: что сломано и что делать
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$port = 8765
if (Test-Path (Join-Path $Root ".env")) {
    foreach ($line in Get-Content (Join-Path $Root ".env") -Encoding UTF8) {
        if ($line -match '^\s*WEB_PORT=(.+)$') { $port = $matches[1].Trim().Trim('"') }
    }
}

Write-Host "==> prune stale + diagnostics" -ForegroundColor Cyan
try {
    $pr = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/nodes/prune-stale" -Method POST -TimeoutSec 15
    if (@($pr.pruned).Count -gt 0) {
        Write-Host "Removed ghost nodes: $($pr.pruned -join ', ')" -ForegroundColor Yellow
    }
    $d = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/diagnostics?prune=false" -TimeoutSec 15
    if ($d.ok) {
        Write-Host "OK: fleet healthy" -ForegroundColor Green
    } else {
        Write-Host "PROBLEMS:" -ForegroundColor Red
        foreach ($i in $d.issues) { Write-Host "  - $i" -ForegroundColor Yellow }
    }
    Write-Host ""
    Write-Host $d.fix -ForegroundColor DarkGray
    Write-Host ""
    foreach ($n in $d.nodes) {
        $tag = if ($n.is_self) { " (этот ПК)" } else { "" }
        Write-Host "$($n.name)$tag  projects=$($n.cached_projects)  pending=$($n.pending_pulls)  seen=$($n.last_seen_sec_ago)s ago"
    }
} catch {
    Write-Host "FAIL: backend not running? $_" -ForegroundColor Red
    exit 1
}
