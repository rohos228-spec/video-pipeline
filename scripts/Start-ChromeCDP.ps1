# Запуск Chrome с CDP :29229 и профилем .vp_browser_data (все логины сохраняются тут).
param(
    [string]$OpenUrl = "https://chatgpt.com/",
    [switch]$ForceNew
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\VpBrowserProfile.ps1"
Set-Location (Get-VpRepoRoot)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Chrome для video-pipeline (CDP 29229)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Профиль: $(Get-VpBrowserUserDataDir)" -ForegroundColor Yellow
Write-Host "Залогинься один раз — дальше сессии сохраняются." -ForegroundColor DarkGray
Write-Host ""

Start-VpChromeCdp -OpenUrl $OpenUrl -ForceNew:$ForceNew

Write-Host ""
Write-Host "Не закрывай это окно Chrome, пока идёт пайплайн." -ForegroundColor Yellow
Write-Host "ChatGPT: https://chatgpt.com/" -ForegroundColor DarkGray
Write-Host "Outsee:  https://outsee.io/" -ForegroundColor DarkGray
Write-Host ""
