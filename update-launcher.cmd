@echo off
REM Force-update launcher + run-backend (fixes backend.log lock)
cd /d "%~dp0"
echo Updating launcher and run-backend.ps1 ...
git fetch origin fix/text-save-persistence-v153
git checkout origin/fix/text-save-persistence-v153 -- installer/VideoPipelineLauncher.ps1 run-backend.ps1 scripts/stop-backend.ps1 stop-backend.cmd VideoPipelineStudio.cmd
findstr /C:"RUN_BACKEND_ID=session-log-v2" run-backend.ps1 >nul
if errorlevel 1 (
    echo [WARN] run-backend.ps1 still old - run fix-update-files.cmd
) else (
    echo [OK] run-backend.ps1 updated
)
echo OK. Run VideoPipelineStudio.cmd then * Update + Start
pause
