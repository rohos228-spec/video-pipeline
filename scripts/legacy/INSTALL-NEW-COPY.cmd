@echo off
REM Только если ставишь ВТОРУЮ копию с нуля. Не трогает твою рабочую папку.
echo This creates a NEW folder. Your projects stay in YOUR old data\state.db
echo.
set /p TARGET="Full path for NEW clone (e.g. C:\temp\video-pipeline-new): "
if "%TARGET%"=="" exit /b 1
git clone -b devin/windows-installer https://github.com/rohos228-spec/video-pipeline.git "%TARGET%"
echo Done: %TARGET%
pause
