# Fleet/montage smoke check on this machine.
#   powershell -ExecutionPolicy Bypass -File .\scripts\Verify-Fleet.ps1 -ProjectId 17

param(
    [int]$ProjectId = 17
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "==> Verify-Fleet ($Root)" -ForegroundColor Cyan

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "FAIL: .venv not found" -ForegroundColor Red
    exit 1
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/fleet/config" -TimeoutSec 5
    Write-Host "OK  backend :8765  role=$($health.role)  fleet=$($health.enabled)" -ForegroundColor Green
} catch {
    Write-Host "FAIL backend not on :8765 - run .\run-backend.ps1" -ForegroundColor Red
    exit 1
}

if ($health.role -eq "hub") {
    try {
        Invoke-WebRequest -Method POST -Uri "http://127.0.0.1:8765/api/fleet/montage-ready" `
            -Headers @{ Authorization = "Bearer test" } `
            -ContentType "application/json" `
            -Body '{"project_id":1,"node_name":"test"}' `
            -TimeoutSec 5 -ErrorAction Stop | Out-Null
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -eq 404) {
            Write-Host "FAIL hub missing /api/fleet/montage-ready - git pull + apply-local" -ForegroundColor Red
        } elseif ($code -eq 401 -or $code -eq 403) {
            Write-Host "OK  /montage-ready route exists (auth required)" -ForegroundColor Green
        } else {
            Write-Host "WARN /montage-ready HTTP $code" -ForegroundColor Yellow
        }
    }
}

Write-Host "==> export bundle project #$ProjectId" -ForegroundColor Cyan
& $py -c "import asyncio; from app.db import session_scope; from app.fleet.bundle import export_project_bundle; asyncio.run((lambda: __import__('asyncio').get_event_loop().run_until_complete(_x()))())" 2>$null
$exportScript = @"
import asyncio
from app.db import session_scope
from app.fleet.bundle import export_project_bundle

async def main():
    async with session_scope() as s:
        blob, name = await export_project_bundle(s, $ProjectId)
        print(f'OK export {name} {len(blob)} bytes')

asyncio.run(main())
"@
& $py -c $exportScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL export bundle" -ForegroundColor Red
    exit 1
}

Write-Host "==> Verify-Fleet done" -ForegroundColor Green
