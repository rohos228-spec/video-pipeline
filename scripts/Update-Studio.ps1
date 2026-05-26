# Console update + start — PowerShell 5.1 (no GUI)
# Double-click: UPDATE-STUDIO.cmd
# Or: powershell -ExecutionPolicy Bypass -File .\scripts\Update-Studio.ps1

[CmdletBinding()]
param(
    [switch]$SkipStart,
    [switch]$GitOnly
)

$ErrorActionPreference = 'Continue'
$core = Join-Path $PSScriptRoot 'StudioUpdateCore.ps1'
if (-not (Test-Path $core)) {
    Write-Host 'ERROR: StudioUpdateCore.ps1 not found' -ForegroundColor Red
    exit 1
}
. $core

$Root = Get-StudioRepoRoot -StartDir $PSScriptRoot
if (-not $Root) {
    Write-Host 'ERROR: pyproject.toml not found — cd to video-pipeline root' -ForegroundColor Red
    exit 1
}
Set-Location $Root

if ($GitOnly) {
    $ok = Invoke-StudioGit $Root
    exit $(if ($ok) { 0 } else { 1 })
}

$ok = Invoke-StudioFullUpdate -Root $Root -SkipStart:$SkipStart
exit $(if ($ok) { 0 } else { 1 })
