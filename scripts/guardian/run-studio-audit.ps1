# Studio Guardian: API smoke + optional Playwright (без Outsee/CDP).
#   powershell -ExecutionPolicy Bypass -File scripts\guardian\run-studio-audit.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\guardian\run-studio-audit.ps1 -E2E

param(
    [switch]$E2E,
    [string]$BaseUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

$failures = [System.Collections.Generic.List[string]]::new()

function Assert-Ok([string]$Name, [scriptblock]$Block) {
    try {
        & $Block
        Write-Host "  [ok] $Name" -ForegroundColor Green
    } catch {
        $msg = "$Name`: $($_.Exception.Message)"
        $failures.Add($msg)
        Write-Host "  [FAIL] $msg" -ForegroundColor Red
    }
}

Write-Host "==> Studio audit ($BaseUrl)" -ForegroundColor Cyan

Assert-Ok "health" {
    $h = Invoke-RestMethod "$BaseUrl/api/health" -TimeoutSec 5
    if ($h.status -ne "ok") { throw "status=$($h.status)" }
}

Assert-Ok "projects list" {
    $p = Invoke-RestMethod "$BaseUrl/api/projects" -TimeoutSec 10
    if (-not $p -or $p.Count -lt 1) { throw "no projects" }
}

Assert-Ok "studio-version" {
    $v = Invoke-RestMethod "$BaseUrl/api/studio-version" -TimeoutSec 5
    if (-not $v.build) { throw "missing build" }
    if ($null -eq $v.ui_stale) { throw "missing ui_stale" }
}

Assert-Ok "steps catalog" {
    $cat = Invoke-RestMethod "$BaseUrl/api/projects/steps/catalog" -TimeoutSec 10
    $codes = @($cat | ForEach-Object { $_.code })
    if ($codes -notcontains "plan") { throw "catalog missing plan" }
    if ($codes -notcontains "video") { throw "catalog missing video" }
}

Assert-Ok "workflows list" {
    $wfs = Invoke-RestMethod "$BaseUrl/api/workflows" -TimeoutSec 10
    if (-not ($wfs | Where-Object { $_.is_default })) { throw "no default workflow" }
}

Assert-Ok "ensure-run idempotent" {
    $p = (Invoke-RestMethod "$BaseUrl/api/projects" -TimeoutSec 10)[0]
    if (-not $p.id) { throw "no project id" }
    Invoke-RestMethod -Method POST "$BaseUrl/api/projects/$($p.id)/ensure-run" | Out-Null
    Invoke-RestMethod -Method POST "$BaseUrl/api/projects/$($p.id)/ensure-run" | Out-Null
}

Assert-Ok "dry_run plan" {
    Invoke-RestMethod -Method POST "$BaseUrl/api/projects/15/steps/plan/run?dry_run=true" | Out-Null
}

Assert-Ok "dry_run video forbidden" {
    try {
        Invoke-RestMethod -Method POST "$BaseUrl/api/projects/15/steps/video/run?dry_run=true" | Out-Null
        throw "expected 400"
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -ne 400) { throw "expected 400, got $code" }
    }
}

# Проект #15: статус vs артефакты (регрессия «frames_ready при готовых видео»)
Assert-Ok "project 15 status sanity" {
    $detail = Invoke-RestMethod "$BaseUrl/api/projects/15" -ErrorAction SilentlyContinue
    if (-not $detail) { return }
    $assets = Invoke-RestMethod "$BaseUrl/api/projects/15/assets?kind=videos" -ErrorAction SilentlyContinue
    $videos = @($assets)
    if ($videos.Count -ge 3 -and $detail.status -eq "frames_ready") {
        throw "status=frames_ready but $($videos.Count) videos on disk (ожидали videos_ready+)"
    }
}

if ($E2E) {
    Write-Host "==> Playwright e2e" -ForegroundColor Cyan
    $webDir = Join-Path $Root "web"
    Push-Location $webDir
    try {
        if (-not (Test-Path "node_modules\@playwright\test")) {
            Write-Host "    npm install ..." -ForegroundColor DarkGray
            npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        }
        if (-not (Test-Path "$env:LOCALAPPDATA\ms-playwright\chromium-*")) {
            Write-Host "    playwright install chromium ..." -ForegroundColor DarkGray
            npx playwright install chromium
        }
        npm run test:e2e
        if ($LASTEXITCODE -ne 0) { throw "test:e2e exit $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "FAILED ($($failures.Count)):" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" }
    exit 1
}
Write-Host "All checks passed." -ForegroundColor Green
