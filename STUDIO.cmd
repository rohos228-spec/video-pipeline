@echo off
REM Единый лаунчер Video Pipeline Studio — двойной клик в Проводнике
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Video Pipeline Studio
set "STUDIO_PS1=%~dp0scripts\studio.ps1"
set "STUDIO_ACTION=%~1"
where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
) else (
    REM PS 5.1: явно читаем UTF-8 (BOM + fallback Get-Content -Encoding UTF8)
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p='%STUDIO_PS1%'; $a='%STUDIO_ACTION%'; $code=Get-Content -LiteralPath $p -Raw -Encoding UTF8; $sb=[ScriptBlock]::Create($code); if ($a) { & $sb -Action $a } else { & $sb }"
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo Завершено с ошибкой (код %ERR%^).
    pause
)
exit /b %ERR%
