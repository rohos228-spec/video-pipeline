@echo off
REM Force-update GUI launcher from GitHub branch
cd /d "%~dp0"
echo Updating installer/VideoPipelineLauncher.ps1 ...
git fetch origin cursor/premium-studio-ui-3713
if errorlevel 1 (
    echo git fetch failed
    pause
    exit /b 1
)
git checkout origin/cursor/premium-studio-ui-3713 -- installer/VideoPipelineLauncher.ps1
if errorlevel 1 (
    echo git checkout failed
    pause
    exit /b 1
)
echo OK. Run VideoPipelineStudio.cmd
pause
