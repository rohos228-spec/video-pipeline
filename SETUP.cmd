@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Studio Setup

set "SETUP_PS1=%~dp0scripts\setup.ps1"
if exist "%SETUP_PS1%" goto :run_ps

if exist "%~dp0video-pipeline\scripts\setup.ps1" (
    set "SETUP_PS1=%~dp0video-pipeline\scripts\setup.ps1"
    cd /d "%~dp0video-pipeline"
    goto :run_ps
)

echo ============================================================
echo   Video Pipeline Studio Setup
echo ============================================================
echo Project files not found locally.
echo Downloading latest version from GitHub (branch: main)...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dest = Join-Path $PWD 'video-pipeline'; " ^
  "if (Get-Command git -ErrorAction SilentlyContinue) { " ^
  "    Write-Host 'Cloning repository via git...' -ForegroundColor Cyan; " ^
  "    git clone --depth 1 -b main https://github.com/rohos228-spec/video-pipeline.git $dest; " ^
  "} else { " ^
  "    Write-Host 'Git not found. Downloading ZIP archive from GitHub...' -ForegroundColor Cyan; " ^
  "    $zip = Join-Path $env:TEMP 'vp_studio.zip'; " ^
  "    $extractDir = Join-Path $env:TEMP 'vp_extract'; " ^
  "    if (Test-Path $zip) { Remove-Item $zip -Force -ErrorAction SilentlyContinue }; " ^
  "    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue }; " ^
  "    $urls = @( " ^
  "        'https://github.com/rohos228-spec/video-pipeline/archive/refs/heads/main.zip', " ^
  "        'https://codeload.github.com/rohos228-spec/video-pipeline/zip/refs/heads/main', " ^
  "        'https://ghproxy.net/https://github.com/rohos228-spec/video-pipeline/archive/refs/heads/main.zip' " ^
  "    ); " ^
  "    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
  "    $downloaded = $false; " ^
  "    foreach ($u in $urls) { " ^
  "        Write-Host ('Trying ' + $u + '...') -ForegroundColor Cyan; " ^
  "        if (Get-Command curl.exe -ErrorAction SilentlyContinue) { " ^
  "            & curl.exe -L -k -s --connect-timeout 15 --max-time 180 --retry 1 -o $zip $u; " ^
  "            if ((Test-Path $zip) -and (Get-Item $zip).Length -gt 100000) { " ^
  "                $downloaded = $true; " ^
  "                Write-Host ('Downloaded ' + [math]::Round((Get-Item $zip).Length / 1MB, 2) + ' MB') -ForegroundColor Green; " ^
  "                break; " ^
  "            } " ^
  "        } " ^
  "        try { " ^
  "            $wc = New-Object System.Net.WebClient; " ^
  "            $wc.Headers.Add('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'); " ^
  "            $wc.DownloadFile($u, $zip); " ^
  "            if ((Test-Path $zip) -and (Get-Item $zip).Length -gt 100000) { " ^
  "                $downloaded = $true; " ^
  "                Write-Host ('Downloaded ' + [math]::Round((Get-Item $zip).Length / 1MB, 2) + ' MB') -ForegroundColor Green; " ^
  "                break; " ^
  "            } " ^
  "        } catch {} " ^
  "        try { " ^
  "            Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing -TimeoutSec 90; " ^
  "            if ((Test-Path $zip) -and (Get-Item $zip).Length -gt 100000) { " ^
  "                $downloaded = $true; " ^
  "                Write-Host ('Downloaded ' + [math]::Round((Get-Item $zip).Length / 1MB, 2) + ' MB') -ForegroundColor Green; " ^
  "                break; " ^
  "            } " ^
  "        } catch {} " ^
  "    }; " ^
  "    if ($downloaded -and (Test-Path $zip)) { " ^
  "        Write-Host 'Extracting project files...' -ForegroundColor Cyan; " ^
  "        Expand-Archive -LiteralPath $zip -DestinationPath $extractDir -Force; " ^
  "        Remove-Item $zip -Force -ErrorAction SilentlyContinue; " ^
  "        $inner = Get-ChildItem -LiteralPath $extractDir | Where-Object { $_.PSIsContainer } | Select-Object -First 1; " ^
  "        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue }; " ^
  "        if ($inner) { " ^
  "            Move-Item -LiteralPath $inner.FullName -Destination $dest -Force; " ^
  "        } else { " ^
  "            Move-Item -LiteralPath $extractDir -Destination $dest -Force; " ^
  "        }; " ^
  "        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue; " ^
  "        Write-Host 'Project files ready.' -ForegroundColor Green; " ^
  "    } else { " ^
  "        Write-Host '[ERROR] Failed to download repository from GitHub.' -ForegroundColor Red; " ^
  "        Write-Host 'Check your internet connection or VPN.' -ForegroundColor Yellow; " ^
  "    } " ^
  "}"

if exist "%~dp0scripts\setup.ps1" (
    set "SETUP_PS1=%~dp0scripts\setup.ps1"
    goto :run_ps
)

if exist "%~dp0video-pipeline\scripts\setup.ps1" (
    set "SETUP_PS1=%~dp0video-pipeline\scripts\setup.ps1"
    cd /d "%~dp0video-pipeline"
    goto :run_ps
)

echo.
echo [ERROR] Could not download or find repository files.
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
