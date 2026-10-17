@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Studio
chcp 65001 >nul 2>&1

:start
if exist "%~dp0.venv\Scripts\python.exe" goto :have_venv

echo.
echo ============================================================
echo  Studio is not installed on this computer (.venv missing).
echo ============================================================
echo.
set /p "RUN_SETUP=Run quick setup now? (Y/n, Enter = Yes): "
if "%RUN_SETUP%"=="" goto :do_setup
if /i "%RUN_SETUP%"=="y" goto :do_setup
exit /b 1

:do_setup
call "%~dp0SETUP.cmd"
exit /b %ERRORLEVEL%

:have_venv

set "STUDIO_PS1=%~dp0scripts\studio.ps1"
set "VP_REPO_ROOT=%~dp0"
if "%VP_REPO_ROOT:~-1%"=="\" set "VP_REPO_ROOT=%VP_REPO_ROOT:~0,-1%"

REM A15 safety. studio.ps1 may update the code with "git stash" and "git reset --hard". Before that can happen
REM the local edits are saved as a patch + an archive of new files, and the operator is told WHERE.
REM This script itself never deletes or resets anything. If the edits cannot be saved, VP_HARD_RESET_ALLOWED=0
REM is exported so that studio.ps1 refuses the hard reset for this run.
set "VP_WIP_DIR=%~dp0data\backups\git-wip"
set "VP_HARD_RESET_ALLOWED=1"
set "VP_WIP_SAVED_TO="
where git >nul 2>&1
if %ERRORLEVEL% equ 0 if exist "%~dp0.git" call :save_wip

REM A15: verified copy of state.db and tracker.db at most once per 6 hours (never blocks start-up).
set "VP_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VP_PY%" call :scheduled_backup

REM STUDIO_HEALED: UTF-8 BOM on studio.ps1 so Windows PowerShell 5.1 can parse Cyrillic.
if exist "%~dp0scripts\heal-studio-ps1.ps1" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\heal-studio-ps1.ps1" -Path "%STUDIO_PS1%"
)

where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo Error code %ERR%.
    pause
)
exit /b %ERR%

:scheduled_backup
"%VP_PY%" "%~dp0scripts\backup_db.py" backup --if-older-than-h 6 --quiet
if errorlevel 1 echo [!] Scheduled database backup failed verification. See: scripts\backup_db.py verify
exit /b 0

:save_wip
set "VP_WIP_TMP=%TEMP%\vp_wip_status_%RANDOM%.txt"
git -c safe.directory=* -C "%~dp0." status --porcelain > "%VP_WIP_TMP%" 2>nul
set "VP_WIP_SIZE=0"
for %%F in ("%VP_WIP_TMP%") do set "VP_WIP_SIZE=%%~zF"
if "%VP_WIP_SIZE%"=="0" goto wip_clean
set "VP_WIP_STAMP="
for /f %%S in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "VP_WIP_STAMP=%%S"
if not defined VP_WIP_STAMP goto wip_failed
set "VP_WIP_OUT=%VP_WIP_DIR%\wip-%VP_WIP_STAMP%"
mkdir "%VP_WIP_OUT%" >nul 2>&1
git -c safe.directory=* -C "%~dp0." diff HEAD --binary > "%VP_WIP_OUT%\tracked.patch" 2>nul
git -c safe.directory=* -C "%~dp0." ls-files --others --exclude-standard > "%VP_WIP_OUT%\untracked.txt" 2>nul
git -c safe.directory=* -C "%~dp0." status --porcelain > "%VP_WIP_OUT%\status.txt" 2>nul
git -c safe.directory=* -C "%~dp0." rev-parse HEAD > "%VP_WIP_OUT%\base-commit.txt" 2>nul
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $o='%VP_WIP_OUT%'; if (-not (Test-Path -LiteralPath (Join-Path $o 'tracked.patch'))) { exit 3 }; $f = @(Get-Content -LiteralPath (Join-Path $o 'untracked.txt') -Encoding UTF8 | Where-Object { $_ }); if ($f.Count -gt 0) { Set-Location -LiteralPath '%~dp0.'; Compress-Archive -LiteralPath $f -DestinationPath (Join-Path $o 'untracked.zip') -Force }" >nul 2>&1
if errorlevel 1 goto wip_failed
set "VP_WIP_SAVED_TO=%VP_WIP_OUT%"
del "%VP_WIP_TMP%" >nul 2>&1
echo [i] Local code edits saved to: %VP_WIP_OUT%
echo     tracked.patch: tracked changes, restore with git apply
echo     untracked.zip: new files, if any. This script deleted and reset nothing.
exit /b 0

:wip_clean
del "%VP_WIP_TMP%" >nul 2>&1
exit /b 0

:wip_failed
set "VP_HARD_RESET_ALLOWED=0"
del "%VP_WIP_TMP%" >nul 2>&1
echo [!] Local edits could NOT be saved. git hard reset is forbidden for this run.
exit /b 1
