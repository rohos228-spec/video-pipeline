param(
    [Parameter(Mandatory = $true)]
    [int]$ProjectId,
    [switch]$Assemble
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$args = @("scripts\repair_fleet_montage.py", $ProjectId)
if ($Assemble.IsPresent) { $args += "--assemble" }

Write-Host "=== Repair montage hub #$ProjectId ===" -ForegroundColor Cyan
& $Py @args
