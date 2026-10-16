@echo off
title Создание ярлыка Video Pipeline Studio
chcp 65001 >nul 2>&1
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "$desktop = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop); " ^
    "$shortcutPath = Join-Path $desktop 'Video Pipeline Studio.lnk'; " ^
    "$target = Join-Path '%~dp0' 'STUDIO.cmd'; " ^
    "$wsh = New-Object -ComObject WScript.Shell; " ^
    "$sc = $wsh.CreateShortcut($shortcutPath); " ^
    "$sc.TargetPath = $target; " ^
    "$sc.Arguments = '1'; " ^
    "$sc.WorkingDirectory = '%~dp0'; " ^
    "$icon = Join-Path '%~dp0' 'assets\icon.ico'; " ^
    "if (Test-Path $icon) { $sc.IconLocation = \"$icon,0\" }; " ^
    "$sc.Description = 'Video Pipeline Studio'; " ^
    "$sc.Save(); " ^
    "Write-Host '[OK] Ярлык создан на Рабочем столе с иконкой Студии!' -ForegroundColor Green"

echo.
echo Нажмите любую клавишу для закрытия...
pause >nul
