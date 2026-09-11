@echo off
setlocal
cd /d "%~dp0"
title Video Pipeline Studio Setup

set "SETUP_PS1=%~dp0scripts\setup.ps1"
if exist "%SETUP_PS1%" goto :run_ps

echo ============================================================
echo   Video Pipeline Studio Setup
echo ============================================================
echo Project files not found locally.
echo Downloading latest version from GitHub...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dest = Join-Path $PWD 'video-pipeline'; " ^
  "if (Get-Command git -ErrorAction SilentlyContinue) { " ^
  "    Write-Host 'Cloning repository via git...' -ForegroundColor Cyan; " ^
  "    git clone --depth 1 https://github.com/rohos228-spec/video-pipeline.git $dest; " ^
  "} else { " ^
  "    Write-Host 'Downloading ZIP archive from GitHub...' -ForegroundColor Cyan; " ^
  "    $zip = Join-Path $env:TEMP 'vp_studio.zip'; " ^
  "    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
  "    Invoke-WebRequest -Uri 'https://github.com/rohos228-spec/video-pipeline/archive/refs/heads/main.zip' -OutFile $zip; " ^
  "    Expand-Archive -LiteralPath $zip -DestinationPath $env:TEMP\vp_extract -Force; " ^
  "    Remove-Item $zip -Force -ErrorAction SilentlyContinue; " ^
  "    Move-Item $env:TEMP\vp_extract\video-pipeline-main $dest -Force; " ^
  "    Remove-Item $env:TEMP\vp_extract -Recurse -Force -ErrorAction SilentlyContinue; " ^
  "}"

if exist "%~dp0video-pipeline\scripts\setup.ps1" (
    set "SETUP_PS1=%~dp0video-pipeline\scripts\setup.ps1"
    cd /d "%~dp0video-pipeline"
    goto :run_ps
)

echo [ERROR] Could not download repository from GitHub.
pause
exit /b 1

:run_ps
where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%SETUP_PS1%" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETUP_PS1%" %*
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo Setup failed with error code %ERR%.
    pause
)
exit /b %ERR%
