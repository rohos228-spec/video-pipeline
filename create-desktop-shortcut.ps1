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

$UpdateVerPath = Join-Path $Desktop "Video Pipeline - Obnovit versiyu.lnk"
$UpdateVer = $WshShell.CreateShortcut($UpdateVerPath)
$UpdateVer.TargetPath = Join-Path $Root "ОБНОВИТЬ-ВЕРСИЮ.cmd"
$UpdateVer.WorkingDirectory = $Root
$UpdateVer.WindowStyle = 1
$UpdateVer.Description = "git pull + UI v109+ + restart backend"
$UpdateVer.Save()

$StartStudioPath = Join-Path $Desktop "Video Pipeline - Start Studio.lnk"
$StartStudio = $WshShell.CreateShortcut($StartStudioPath)
$StartStudio.TargetPath = Join-Path $Root "Open-Studio.cmd"
$StartStudio.WorkingDirectory = $Root
$StartStudio.WindowStyle = 1
$StartStudio.Description = "Start backend on :8765 and open browser"
$StartStudio.Save()

Write-Host "Shortcuts created:" -ForegroundColor Green
Write-Host "  $ShortcutPath"
Write-Host "  $UpdateVerPath"
Write-Host "  $StartStudioPath"
Write-Host ""
Write-Host "Double-click from Desktop (WorkingDirectory = repo; not C:\Users\...)."
Write-Host "Repo folder:"
Write-Host "  $Root"
