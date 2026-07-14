# Chrome window for outsee.io login (bot uses CDP :29229).
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\scripts\VpBrowserProfile.ps1"
Set-Location (Get-VpRepoRoot)

$outseeUrl = "https://outsee.io/video?model=veo-3-1-fast"
$userDataDir = Get-VpBrowserUserDataDir
$cdpPort = 29229

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  CHROME FOR OUTSEE" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Profile: $userDataDir" -ForegroundColor Yellow
Write-Host "CDP port: $cdpPort" -ForegroundColor DarkGray
Write-Host ""

Start-VpChromeCdp -Port $cdpPort -OpenUrl $outseeUrl

Write-Host ""
Write-Host "After login press Enter for Generate button scan..." -ForegroundColor Cyan
Read-Host | Out-Null

$venvPy = Join-Path (Get-VpRepoRoot) ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    & $venvPy -m app.bots.outsee recon-generate video
    Write-Host ""
    Write-Host "Files: data\outsee_dumps\recon_generate_video_*" -ForegroundColor Yellow
} else {
    Write-Host "venv not found - run install.ps1 first" -ForegroundColor Red
}

Write-Host ""
Write-Host "Do not close Chrome while generating." -ForegroundColor Yellow
pause
