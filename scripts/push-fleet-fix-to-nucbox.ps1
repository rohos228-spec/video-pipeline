# Hub -> NucBox: upload fixed Python files + restart Studio on worker.
param(
    [string]$WorkerUrl = "http://100.100.240.106:8765",
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

if (-not $Token) {
    $envPath = Join-Path $Root ".env"
    if (Test-Path $envPath) {
        $line = Get-Content $envPath | Where-Object { $_ -match '^\s*FLEET_AGENT_TOKEN=' } | Select-Object -First 1
        if ($line) { $Token = ($line -split '=', 2)[1].Trim().Trim('"') }
    }
}
if (-not $Token) { throw "FLEET_AGENT_TOKEN not found in .env" }

$files = @(
    "app/fleet/bundle.py",
    "app/fleet/client.py",
    "app/fleet/pull_loop.py",
    "app/web/routers/fleet.py"
)

Write-Host "=== Push fleet fix -> $WorkerUrl ===" -ForegroundColor Cyan

foreach ($rel in $files) {
    $local = Join-Path $Root ($rel -replace '/', '\')
    if (-not (Test-Path $local)) { throw "missing $local" }
    $uri = "$WorkerUrl/api/fleet/local/files/upload?path=$rel&authorization=Bearer%20$Token"
    Write-Host "  upload $rel" -ForegroundColor Gray
    curl.exe -sS -X POST $uri -F "file=@$local" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "upload failed: $rel" }
}

$restartCmd = @'
cd $env:USERPROFILE\Desktop\video-pipeline
if (-not (Test-Path .\pyproject.toml)) { cd C:\Users\AiCreator\Desktop\video-pipeline }
powershell -ExecutionPolicy Bypass -File .\scripts\stop-backend.ps1 -Quiet
Start-Sleep 2
powershell -ExecutionPolicy Bypass -File .\apply-local.ps1 -SkipBuild -NoBrowser
'@

Write-Host "  restart Studio on NucBox..." -ForegroundColor Gray
$body = @{ command = $restartCmd; timeout_sec = 120 } | ConvertTo-Json -Compress
$psUri = "$WorkerUrl/api/fleet/local/powershell?authorization=Bearer%20$Token"
curl.exe -sS -X POST $psUri -H "Content-Type: application/json" -d $body | Out-Host

Write-Host "Done. NucBox updated." -ForegroundColor Green
