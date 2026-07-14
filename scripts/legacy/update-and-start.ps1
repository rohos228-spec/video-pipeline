# Legacy wrapper — use scripts/Update-Studio.ps1 or UPDATE-STUDIO.cmd
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$SkipNpm,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
$core = Join-Path $Root 'scripts\StudioUpdateCore.ps1'
if (-not (Test-Path $core)) {
    Write-Host 'ERROR: run git pull — scripts/StudioUpdateCore.ps1 missing' -ForegroundColor Red
    exit 1
}
. $core

$repo = Get-StudioRepoRoot -StartDir $Root
if (-not $repo) {
    Write-Host 'ERROR: not in video-pipeline repo' -ForegroundColor Red
    exit 1
}
Set-Location $repo

if ($NoLaunch) {
    $ok = Invoke-StudioUpdateOnly -Root $repo
    exit $(if ($ok) { 0 } else { 1 })
}

if ($BackendOnly) {
    if (-not (Invoke-StudioUpdateOnly -Root $repo)) { exit 1 }
    Stop-StudioBackend $repo
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $repo 'run-backend.ps1')
    ) -WorkingDirectory $repo
    Start-Sleep -Seconds 2
    Start-Process 'http://127.0.0.1:8765'
    exit 0
}

$ok = Invoke-StudioFullUpdate -Root $repo
exit $(if ($ok) { 0 } else { 1 })
