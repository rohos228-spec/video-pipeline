# Creates Desktop shortcut to Video Pipeline Studio GUI
# Run once: powershell -ExecutionPolicy Bypass -File .\create-desktop-shortcut.ps1

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Write-Host "ERROR: run from video-pipeline root" -ForegroundColor Red
    exit 1
}

$TargetCmd = Join-Path $Root "VideoPipelineStudio.cmd"
if (-not (Test-Path $TargetCmd)) {
    Write-Host "ERROR: VideoPipelineStudio.cmd not found" -ForegroundColor Red
    exit 1
}

$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Video Pipeline Studio.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetCmd
$Shortcut.WorkingDirectory = $Root
$Shortcut.WindowStyle = 1
$Shortcut.Description = "Video Pipeline Studio - install, update, run"
$Shortcut.Save()

Write-Host "Shortcut created:" -ForegroundColor Green
Write-Host "  $ShortcutPath"
Write-Host ""
Write-Host "Double-click it from Desktop (or copy .lnk anywhere)."
Write-Host "The shortcut always starts the menu from your project folder:"
Write-Host "  $Root"
