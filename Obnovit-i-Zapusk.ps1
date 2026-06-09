# Update (git) + restart backend. ASCII-only for Windows PowerShell 5.1.
#
# Git merge + restart:
#   powershell -ExecutionPolicy Bypass -File .\Obnovit-i-Zapusk.ps1 -SkipBuild
#
# Restart only (no git):
#   powershell -ExecutionPolicy Bypass -File .\Obnovit-i-Zapusk.ps1 -RestartOnly -SkipBuild

param(
    [Alias("TolkoRestart")]
    [switch]$RestartOnly,
    [switch]$SkipBuild,
    [switch]$NoBrowser,
    [string]$GitBranch = "devin/windows-installer"
)

function Invoke-GitStep {
    param([string]$Label, [string[]]$GitArgs)
    Write-Host "==> $Label" -ForegroundColor Cyan
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git @GitArgs 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            throw "git exit $LASTEXITCODE"
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Write-Host "ERROR: run from video-pipeline folder (need pyproject.toml)" -ForegroundColor Red
    exit 1
}

if (-not $RestartOnly) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: git not in PATH" -ForegroundColor Red
        exit 1
    }
    if (-not $GitBranch) {
        Write-Host "ERROR: GitBranch is empty" -ForegroundColor Red
        exit 1
    }
    try {
        Invoke-GitStep -Label "git fetch origin $GitBranch" -GitArgs @("fetch", "origin", $GitBranch)
        Invoke-GitStep -Label "git verify origin/$GitBranch" -GitArgs @("rev-parse", "--verify", "origin/$GitBranch")
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $curBranch = (git rev-parse --abbrev-ref HEAD 2>$null)
        $ErrorActionPreference = $prevEap
        if ($curBranch) { $curBranch = $curBranch.Trim() }
        Invoke-GitStep -Label "git merge origin/$GitBranch (current: $curBranch)" -GitArgs @("merge", "origin/$GitBranch", "--no-edit")
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $short = (git rev-parse --short HEAD 2>$null)
        $ErrorActionPreference = $prevEap
        Write-Host "    git OK: $short" -ForegroundColor Green
    } catch {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "       or skip git: .\apply-local.ps1 -SkipBuild" -ForegroundColor Yellow
        Write-Host "       or: Obnovit-i-Zapusk.ps1 -RestartOnly -SkipBuild" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "==> -RestartOnly: skip git" -ForegroundColor Yellow
}

$apply = Join-Path $Root "apply-local.ps1"
if (-not (Test-Path $apply)) {
    Write-Host "ERROR: apply-local.ps1 missing" -ForegroundColor Red
    exit 1
}

$applyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $apply)
if ($SkipBuild) { $applyArgs += "-SkipBuild" }
if ($NoBrowser) { $applyArgs += "-NoBrowser" }

Write-Host "==> restart backend (apply-local)" -ForegroundColor Cyan
& powershell.exe @applyArgs
exit $LASTEXITCODE
