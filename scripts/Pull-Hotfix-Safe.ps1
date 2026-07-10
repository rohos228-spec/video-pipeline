# Безопасный git pull hotfix-ветки, когда локальные файлы мешают merge.
# Запуск из корня репозитория:
#   powershell -ExecutionPolicy Bypass -File scripts\Pull-Hotfix-Safe.ps1
#
# По умолчанию: stash (включая untracked) → pull devin/windows-installer.
# -HardReset: сбросить локальные изменения и совпасть с origin (без stash).

param(
    [switch]$HardReset
)

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
if (-not $Root) {
    $Root = Get-RepoRoot -Start (Get-Location).Path
}
if (-not $Root) {
    Write-Host "ERROR: pyproject.toml not found" -ForegroundColor Red
    exit 1
}

Set-Location $Root
Write-Host "=== Pull hotfix ($Branch) ===" -ForegroundColor Cyan
Write-Host "Repo: $Root" -ForegroundColor DarkGray

git fetch origin $Branch

if ($HardReset) {
    Write-Host "> git reset --hard origin/$Branch" -ForegroundColor Yellow
    git reset --hard "origin/$Branch"
} else {
    $dirty = git status --porcelain
    if ($dirty) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        Write-Host "> git stash push -u (backup: before-hotfix-pull-$stamp)" -ForegroundColor Yellow
        git stash push -u -m "before-hotfix-pull-$stamp"
    }
    $cur = git rev-parse --abbrev-ref HEAD
    if ($cur -ne $Branch) {
        Write-Host "> git checkout $Branch" -ForegroundColor Yellow
        git checkout $Branch
    }
    Write-Host "> git pull origin $Branch" -ForegroundColor Yellow
    git pull origin $Branch
}

# Clear stale bytecode
Get-ChildItem -Path (Join-Path $Root "app") -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$marker = Join-Path $Root "app\hotfix_build.py"
if (Test-Path $marker) {
    $line = Select-String -Path $marker -Pattern "PIPELINE_HOTFIX_ID" | Select-Object -First 1
    Write-Host "Hotfix marker: $($line.Line.Trim())" -ForegroundColor Green
} else {
    Write-Host "WARN: app\hotfix_build.py not found — pull incomplete?" -ForegroundColor Red
    exit 1
}

$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    Write-Host "> stop backend" -ForegroundColor Cyan
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet 2>$null
}

Write-Host ""
Write-Host "Done. Restart Studio, then open:" -ForegroundColor Green
Write-Host "  http://127.0.0.1:8765/api/studio-version" -ForegroundColor DarkGray
Write-Host "Expect: pipeline_hotfix = hotfix-20260710-stop-queue-xlsx-v2" -ForegroundColor DarkGray

exit 0
