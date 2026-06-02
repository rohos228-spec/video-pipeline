# Полный прогон: API-матрица + Guardian + e2e + web pytest
#   powershell -ExecutionPolicy Bypass -File scripts\guardian\run-everything.ps1
# Живой пайплайн GPT (plan→anim_pr) может занять 30–60+ мин — см. run-full-verification.py

param(
    [switch]$SkipPipeline,
    [switch]$SkipE2E
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

Write-Host "==> 1/4 Studio audit" -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\guardian\run-studio-audit.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> 2/4 Web pytest" -ForegroundColor Cyan
& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest `
    tests/test_web_api_integration.py `
    tests/test_web_dry_run_step.py `
    tests/test_studio_version.py -q --tb=short
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipPipeline) {
    Write-Host "==> 3/4 Full API + live GPT pipeline (QA-SMOKE)" -ForegroundColor Cyan
    & (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $Root "scripts\guardian\run-full-verification.py")
    # pipeline failures may be env-specific; do not hard-exit
}

if (-not $SkipE2E) {
    Write-Host "==> 4/4 Playwright e2e" -ForegroundColor Cyan
    Push-Location (Join-Path $Root "web")
    try {
        npm run test:e2e
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

Write-Host "`nDone. Reports: docs/QA-RUN-API-*.json docs/QA-RUN-FULL-*.md" -ForegroundColor Green
