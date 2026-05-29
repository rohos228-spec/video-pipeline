@echo off
cd /d "%~dp0"
if exist FORCE-UPDATE.cmd (
    call "%~dp0FORCE-UPDATE.cmd"
) else (
    call "%~dp0UPDATE-STUDIO.cmd"
)
