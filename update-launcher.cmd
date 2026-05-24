@echo off
REM Force-update GUI launcher from main branch
cd /d "%~dp0"
echo Updating installer/VideoPipelineLauncher.ps1 ...
git fetch origin devin/windows-installer
if errorlevel 1 (
    echo git fetch failed
    pause
    exit /b 1
)
git checkout origin/devin/windows-installer -- installer/VideoPipelineLauncher.ps1
if errorlevel 1 (
    echo git checkout failed
    pause
    exit /b 1
)
echo OK. Run VideoPipelineStudio.cmd
pause
