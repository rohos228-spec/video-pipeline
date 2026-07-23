@echo off
setlocal
cd /d "%~dp0"
call "%~dp0FORCE-UPDATE.cmd"
if errorlevel 1 exit /b 1
echo.
echo Studio updated to v160 branch (fix/text-save-persistence-v153).
echo Restart Studio and press Ctrl+Shift+R in the browser.
pause
