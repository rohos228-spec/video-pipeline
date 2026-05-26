@echo off
REM Deprecated: use VideoPipelineStudio.cmd -> * Update + Start (does the same + git + pip)
cd /d "%~dp0"
echo Use VideoPipelineStudio.cmd and press * Update + Start
echo.
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0installer\VideoPipelineLauncher.ps1"
pause
