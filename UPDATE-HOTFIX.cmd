@echo off
cd /d "%~dp0"
if not exist "%~dp0scripts\Update-Hotfix-FromGitHub.ps1" (
  echo Downloading Update-Hotfix-FromGitHub.ps1 from GitHub...
  if not exist "%~dp0scripts" mkdir "%~dp0scripts"
  powershell -ExecutionPolicy Bypass -NoProfile -Command "Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/rohos228-spec/video-pipeline/cursor/restore-legacy-prompts-fc98/scripts/Update-Hotfix-FromGitHub.ps1' -OutFile '%~dp0scripts\Update-Hotfix-FromGitHub.ps1' -UseBasicParsing"
)
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0scripts\Update-Hotfix-FromGitHub.ps1"
pause
