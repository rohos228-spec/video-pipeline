@echo off
chcp 65001 >nul
title video-pipeline — установка на новый ПК
echo.
echo ========================================
echo   video-pipeline — установка
echo   Пользователь: %USERNAME%
echo   Папка: %USERPROFILE%\video-pipeline
echo ========================================
echo.
echo НЕ используйте путь C:\Users\aicreator\ — у каждого ПК свой пользователь.
echo.
pause

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-NewPc.ps1" -InstallDir "%USERPROFILE%\video-pipeline" -NonInteractive
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
    echo OK. Откройте: %USERPROFILE%\video-pipeline\VideoPipelineStudio.cmd
    echo Браузер: http://127.0.0.1:8765
) else (
    echo ОШИБКА %ERR%. Скопируйте текст выше и пришлите разработчику.
)
echo.
pause
exit /b %ERR%
