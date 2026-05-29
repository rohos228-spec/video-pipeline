$ErrorActionPreference = "Continue"
. "$PSScriptRoot\VpBrowserProfile.ps1"
Set-Location (Get-VpRepoRoot)

Show-VpChromeDiagnostics -Port 29229

Write-Host "Quick fix:" -ForegroundColor Cyan
Write-Host "  1. Close ALL Chrome windows"
Write-Host "  2. Run Start-Chrome.cmd"
Write-Host ""
Write-Host "Manual start:" -ForegroundColor DarkGray
$chrome = Find-VpChromeExe
$profile = Get-VpBrowserUserDataDir
if ($chrome) {
    Write-Host "  & `"$chrome`" --remote-debugging-port=29229 --user-data-dir=`"$profile`""
}
Write-Host ""
