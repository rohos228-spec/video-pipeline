# Создать ярлык Video Pipeline Studio на рабочем столе
# Запуск один раз: create-desktop-shortcut.cmd

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Write-Host "ОШИБКА: запустите из корня репозитория video-pipeline" -ForegroundColor Red
    exit 1
}

$TargetCmd = Join-Path $Root "STUDIO.cmd"
if (-not (Test-Path $TargetCmd)) {
    Write-Host "ОШИБКА: STUDIO.cmd не найден" -ForegroundColor Red
    exit 1
}

$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Video Pipeline Studio.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetCmd
$Shortcut.WorkingDirectory = $Root
$Shortcut.WindowStyle = 1
$Shortcut.Description = "Video Pipeline Studio — запуск, обновление, диагностика"
$Shortcut.Save()

Write-Host "Ярлык создан:" -ForegroundColor Green
Write-Host "  $ShortcutPath"
Write-Host ""
Write-Host "Двойной клик по ярлыку -> меню STUDIO.cmd"
Write-Host "Папка проекта:"
Write-Host "  $Root"
