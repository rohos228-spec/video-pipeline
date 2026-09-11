@echo off
setlocal EnableExtensions
title Video Pipeline Studio Setup
chcp 65001 >nul 2>&1

set "SCRIPT_DIR=%~dp0"
set "SETUP_PS1=%SCRIPT_DIR%scripts\setup.ps1"

if exist "%SETUP_PS1%" goto :run_setup

echo.
echo ============================================================
echo   Video Pipeline Studio: Автономный установщик
echo ============================================================
echo.
echo [!] Файлы Студии не обнаружены в текущей папке.
echo [*] Загрузка актуальной версии Video Pipeline Studio с GitHub...
echo.

where git >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [+] Клонирование репозитория через Git...
    git clone https://github.com/rohos228-spec/video-pipeline.git "%SCRIPT_DIR%video-pipeline"
    if exist "%SCRIPT_DIR%video-pipeline\scripts\setup.ps1" (
        cd /d "%SCRIPT_DIR%video-pipeline"
        set "SETUP_PS1=%SCRIPT_DIR%video-pipeline\scripts\setup.ps1"
        goto :run_setup
    )
)

echo [+] Загрузка архива репозитория с GitHub...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$zip = Join-Path $env:TEMP 'vp_studio_main.zip'; $dest = '%SCRIPT_DIR%video-pipeline-temp'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Write-Host '    Скачивание архива...' -ForegroundColor Cyan; Invoke-WebRequest -Uri 'https://github.com/rohos228-spec/video-pipeline/archive/refs/heads/main.zip' -OutFile $zip; Write-Host '    Распаковка архива...' -ForegroundColor Cyan; Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force; Remove-Item $zip -Force -ErrorAction SilentlyContinue; $extracted = Join-Path $dest 'video-pipeline-main'; $finalDir = '%SCRIPT_DIR%video-pipeline'; if (Test-Path $finalDir) { Remove-Item $finalDir -Recurse -Force }; Move-Item $extracted $finalDir; Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue;"

if exist "%SCRIPT_DIR%video-pipeline\scripts\setup.ps1" (
    cd /d "%SCRIPT_DIR%video-pipeline"
    set "SETUP_PS1=%SCRIPT_DIR%video-pipeline\scripts\setup.ps1"
    goto :run_setup
)

echo.
echo [ОШИБКА] Не удалось автоматически загрузить файлы проекта с GitHub.
echo Пожалуйста, скачайте репозиторий вручную (Code / Download ZIP) и запустите SETUP.cmd внутри папки.
echo.
pause
exit /b 1

:run_setup
where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%SETUP_PS1%" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETUP_PS1%" %*
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo Ошибка установки [код %ERR%].
    pause
)
exit /b %ERR%
