# Жёсткий сброс на origin/main, перезапуск бэкенда, проверка fleet.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

Write-Host "==> git fetch + reset --hard origin/main" -ForegroundColor Cyan
git -C $Root fetch origin main 2>&1 | ForEach-Object { Write-Host $_ }
git -C $Root reset --hard origin/main 2>&1 | ForEach-Object { Write-Host $_ }
$head = (git -C $Root rev-parse --short HEAD 2>$null).Trim()
Write-Host "    HEAD=$head" -ForegroundColor Green

. (Join-Path $Root "scripts\VpWebBind.ps1")
$patched = Ensure-VpFleetNetworkEnv -Root $Root
if ($patched.Count -gt 0) {
    Write-Host "==> .env: $($patched -join ', ')" -ForegroundColor Green
}

Write-Host "==> stop backend" -ForegroundColor Cyan
$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet -WaitSec 15 2>$null
}

Write-Host "==> start backend (new window)" -ForegroundColor Cyan
$rb = Join-Path $Root "scripts\run-backend.ps1"
Start-Process powershell.exe -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $rb) -WorkingDirectory $Root

$port = (Get-VpWebBindConfig -Root $Root).WebPort
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 3
        if ($h.status -eq "ok") { break }
    } catch { }
    Start-Sleep -Seconds 1
}

Write-Host "==> test fleet local pipeline (no token)" -ForegroundColor Cyan
try {
    $pipe = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/local/pipeline" -TimeoutSec 10
    Write-Host "OK: local projects=$(@($pipe.projects).Count)  git=$head" -ForegroundColor Green
} catch {
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
    exit 1
}

Write-Host "==> prune stale fleet nodes + diagnostics" -ForegroundColor Cyan
try {
    $pr = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/nodes/prune-stale" -Method POST -TimeoutSec 15
    if (@($pr.pruned).Count -gt 0) {
        Write-Host "    removed: $($pr.pruned -join ', ')" -ForegroundColor Yellow
    }
    $d = Invoke-RestMethod "http://127.0.0.1:$port/api/fleet/diagnostics" -TimeoutSec 15
    foreach ($n in $d.nodes) {
        $tag = if ($n.is_self) { " (hub)" } else { "" }
        Write-Host "    $($n.name)$tag  seen=$($n.last_seen_sec_ago)s  projects=$($n.cached_projects)" -ForegroundColor DarkGray
    }
    if (-not $d.ok) {
        Write-Host "WARN: fleet issues remain — run FLEET-DIAG.cmd or FLEET-AGENT-UPDATE.cmd on child" -ForegroundColor Yellow
        foreach ($i in $d.issues) { Write-Host "  - $i" -ForegroundColor Yellow }
    }
} catch {
    Write-Host "WARN: fleet diagnostics skipped: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "Fleet fix complete." -ForegroundColor Green
