# Fix v0 badge: rewrite STUDIO_VERSION without UTF-8 BOM + restore web/out from git.
param(
    [string]$Root = (Join-Path $env:USERPROFILE "video-pipeline"),
    [string]$Branch = "fix/text-save-persistence-v153"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $Root

Write-Host "==> Fix studio version in $Root" -ForegroundColor Cyan

git fetch origin $Branch
git checkout $Branch
git reset --hard "origin/$Branch"
git checkout "origin/$Branch" -- web/out web/STUDIO_VERSION

$versionPath = Join-Path $Root "web\STUDIO_VERSION"
$lines = @(
    "160",
    "0defe31",
    "attach-guard-v84-download-fast",
    "xlsx_step_runners-v73"
)
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllLines($versionPath, $lines, $utf8NoBom)

Write-Host "STUDIO_VERSION:" -ForegroundColor Green
Get-Content -LiteralPath $versionPath

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $py) {
    & $py -m pip install -e $Root -q
}

Write-Host ""
Write-Host "API check (start backend first if fails):" -ForegroundColor Yellow
try {
    $sv = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 3
    Write-Host "  label=$($sv.label) build=$($sv.build)" -ForegroundColor Green
} catch {
    Write-Host "  backend not running yet — run: powershell -ExecutionPolicy Bypass -File .\run-backend.ps1" -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "Done. Browser: http://127.0.0.1:8765  (Ctrl+F5)" -ForegroundColor Green
