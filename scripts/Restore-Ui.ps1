# Force-restore web/out UI from devin/windows-installer (Materials / Network buttons).
# Run: powershell -ExecutionPolicy Bypass -File scripts\Restore-Ui.ps1

$ErrorActionPreference = "Stop"
$Branch = "devin/windows-installer"

function Get-RepoRoot {
    param([string]$Start)
    $dir = $Start
    for ($i = 0; $i -lt 12; $i++) {
        if (Test-Path (Join-Path $dir "pyproject.toml")) {
            return (Resolve-Path -LiteralPath $dir).Path
        }
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return $null
}

$Root = Get-RepoRoot -Start $PSScriptRoot
if (-not $Root) { $Root = Get-RepoRoot -Start (Get-Location).Path }
if (-not $Root) {
    Write-Host "ERROR: pyproject.toml not found" -ForegroundColor Red
    exit 1
}

Set-Location $Root
Write-Host "=== Restore UI ($Branch) ===" -ForegroundColor Cyan
Write-Host "Repo: $Root" -ForegroundColor DarkGray

$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet 2>$null
}

git fetch origin $Branch
git checkout $Branch 2>$null
git reset --hard "origin/$Branch"

$out = Join-Path $Root "web\out"
if (Test-Path $out) {
    Remove-Item -LiteralPath $out -Recurse -Force
}
git checkout "origin/$Branch" -- web/out web/STUDIO_VERSION scripts/Pull-Hotfix-Safe.ps1 PULL-HOTFIX.cmd

Write-Host ""
Write-Host "STUDIO_VERSION:" -ForegroundColor Green
Get-Content (Join-Path $Root "web\STUDIO_VERSION")

$js = Get-ChildItem (Join-Path $Root "web\out\_next\static\chunks\app") -Filter "page-*.js" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $js) {
    Write-Host "ERROR: web/out page bundle missing" -ForegroundColor Red
    exit 1
}
$text = Get-Content -LiteralPath $js.FullName -Raw -Encoding UTF8
foreach ($needle in @("Сеть", "Материалы", "Кадры")) {
    if ($text -notmatch [regex]::Escape($needle)) {
        Write-Host "ERROR: bundle missing $needle" -ForegroundColor Red
        exit 1
    }
}
Write-Host "OK: web/out has Сеть + Материалы + Кадры" -ForegroundColor Green
Write-Host ""
Write-Host "Restart Studio, browser Ctrl+F5" -ForegroundColor Yellow
exit 0
