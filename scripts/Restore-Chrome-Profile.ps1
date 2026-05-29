param(
    [Parameter(Mandatory = $true)]
    [string]$FromPath
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\VpBrowserProfile.ps1"

$src = $FromPath.Trim('"')
if (-not (Test-Path $src)) {
    Write-Host "ERROR: not found: $src" -ForegroundColor Red
    exit 1
}

$dest = Get-VpBrowserUserDataDir
Write-Host ""
Write-Host "Copy Chrome profile:" -ForegroundColor Cyan
Write-Host "  from: $src"
Write-Host "  to:   $dest"
Write-Host ""

$chrome = Get-Process -Name chrome -ErrorAction SilentlyContinue
if ($chrome) {
    Write-Host "Close ALL Chrome windows and press Enter..." -ForegroundColor Yellow
    Read-Host | Out-Null
}

if (Test-Path $dest) {
    $backup = "$dest.backup_$(Get-Date -Format yyyyMMdd_HHmmss)"
    Write-Host "Backup current profile -> $backup" -ForegroundColor DarkGray
    Move-Item -LiteralPath $dest -Destination $backup
}

New-Item -ItemType Directory -Path $dest -Force | Out-Null
Copy-Item -Path (Join-Path $src "*") -Destination $dest -Recurse -Force
Clear-VpChromeProfileLocks -UserDataDir $dest

Write-Host ""
Write-Host "[ok] Profile copied. Run Start-Chrome.cmd" -ForegroundColor Green
Write-Host ""
