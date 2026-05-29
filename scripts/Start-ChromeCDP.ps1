param(
    [string]$OpenUrl = "https://chatgpt.com/",
    [switch]$ForceNew
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\VpBrowserProfile.ps1"
Set-Location (Get-VpRepoRoot)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Chrome for video-pipeline (CDP 29229)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Profile: $(Get-VpBrowserUserDataDir)" -ForegroundColor Yellow
Write-Host "Log in once - sessions are saved in this folder." -ForegroundColor DarkGray
Write-Host ""

Start-VpChromeCdp -OpenUrl $OpenUrl -ForceNew:$ForceNew

Write-Host ""
Write-Host "Keep Chrome open while the pipeline runs." -ForegroundColor Yellow
Write-Host "ChatGPT: https://chatgpt.com/" -ForegroundColor DarkGray
Write-Host "Outsee:  https://outsee.io/" -ForegroundColor DarkGray
Write-Host ""
