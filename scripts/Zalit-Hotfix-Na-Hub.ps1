# NucBox -> GLAVNY PC: zalit fleet-hotfix cherez hub API (FLEET_AGENT_TOKEN, bez parolya UI).
#   powershell -ExecutionPolicy Bypass -File .\scripts\Zalit-Hotfix-Na-Hub.ps1

param(
    [string]$HubUrl = "",
    [string]$FleetToken = "",
    [switch]$NoRestart,
    [switch]$WithWeb
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $Root

function Read-DotEnvValue([string]$Name) {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return "" }
    $m = Select-String -Path $envFile -Pattern "^\s*$([regex]::Escape($Name))=(.+)$" | Select-Object -Last 1
    if (-not $m) { return "" }
    return $m.Matches.Groups[1].Value.Trim().Trim('"').Trim("'")
}

function Get-FleetToken {
    param([string]$Override)
    if ($Override) { return $Override.Trim() }
    $t = Read-DotEnvValue "FLEET_AGENT_TOKEN"
    if ($t) { return $t }
    $tokenFile = Join-Path $Root "data\fleet-agent-token.txt"
    if (Test-Path $tokenFile) {
        return (Get-Content -LiteralPath $tokenFile -Raw).Trim()
    }
    return ""
}

function Send-HubFileLocal([string]$Base, [string]$Token, [string]$RelPath, [string]$LocalPath) {
    Add-Type -AssemblyName System.Net.Http
    $remote = $RelPath.Replace("\", "/")
    $uri = "$Base/api/fleet/local/files/upload?path=" + [uri]::EscapeDataString($remote)
    $client = New-Object System.Net.Http.HttpClient
    [void]$client.DefaultRequestHeaders.TryAddWithoutValidation("Authorization", "Bearer $Token")
    $client.Timeout = [TimeSpan]::FromMinutes(10)
    $content = New-Object System.Net.Http.MultipartFormDataContent
    $stream = [System.IO.File]::OpenRead($LocalPath)
    try {
        $sc = New-Object System.Net.Http.StreamContent($stream)
        $content.Add($sc, "file", [System.IO.Path]::GetFileName($LocalPath))
        $response = $client.PostAsync($uri, $content).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "upload $remote HTTP $([int]$response.StatusCode): $body"
        }
    } finally {
        $stream.Dispose()
        $client.Dispose()
    }
}

function Test-HubImportBundle([string]$Base) {
    try {
        Invoke-WebRequest -Method POST -Uri "$Base/api/fleet/import-bundle" -TimeoutSec 8 | Out-Null
        return $true
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        return ($code -eq 401 -or $code -eq 422)
    }
}

$hotfixFiles = @(
    "app\main.py",
    "app\orchestrator\auto_advance.py",
    "app\services\animation_prompt_gpt.py",
    "app\services\project_state.py",
    "app\bots\outsee.py",
    "app\services\project_steps.py",
    "app\fleet\transfer_state.py",
    "app\fleet\client.py",
    "app\fleet\push_to_hub.py",
    "app\fleet\bundle.py",
    "app\fleet\montage_handoff.py",
    "app\fleet\pull_loop.py",
    "app\web\routers\fleet.py",
    "app\web\routers\project_ops.py",
    "app\services\project_control.py",
    "app\services\event_bus.py",
    "app\settings.py",
    "app\db.py",
    "scripts\stop-backend.ps1",
    "apply-local.ps1",
    "run-backend.ps1",
    "scripts\Proverit-Handoff-Hub.ps1",
    "scripts\Zabrat-S-Nucbox.ps1"
)

Write-Host "==> Zalit-Hotfix-Na-Hub (NucBox -> glavny PC)" -ForegroundColor Cyan
Write-Host "    repo: $Root" -ForegroundColor DarkGray

if (-not $HubUrl) { $HubUrl = Read-DotEnvValue "FLEET_HUB_URL" }
if (-not $HubUrl) { throw "FLEET_HUB_URL pust. Ukazhi -HubUrl ili v .env" }
$HubUrl = $HubUrl.Trim().TrimEnd("/")

$token = Get-FleetToken -Override $FleetToken
if (-not $token) {
    throw "Net FLEET_AGENT_TOKEN. Dolzhen byt v .env ili data\fleet-agent-token.txt (tot zhe token chto na glavnom PC)."
}

try {
    $localCfg = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/fleet/config" -TimeoutSec 5
    Write-Host "    etot PC: role=$($localCfg.role) node=$($localCfg.node_name)" -ForegroundColor Gray
} catch {
    Write-Host "WARN: lokalny backend ne otvechaet (8765), prodolzhayu k hub" -ForegroundColor Yellow
}

$headers = @{ Authorization = "Bearer $token" }
try {
    $hubCfg = Invoke-RestMethod -Uri "$HubUrl/api/fleet/config" -TimeoutSec 15
} catch {
    throw "Hub ne otvechaet ($HubUrl): $($_.Exception.Message)"
}
Write-Host "OK  hub online: $HubUrl role=$($hubCfg.role) montage_hub=$($hubCfg.montage_hub)" -ForegroundColor Green

try {
    $hubInfo = Invoke-RestMethod -Uri "$HubUrl/api/fleet/local/info" -Headers $headers -TimeoutSec 15
    Write-Host "    hub repo: $($hubInfo.pipeline_root)" -ForegroundColor Gray
} catch {
    throw "Hub otklonil token (403/401). Prover chto FLEET_AGENT_TOKEN na NucBox = FLEET_AGENT_TOKEN na glavnom PC v .env"
}

$missing = @()
foreach ($rel in $hotfixFiles) {
    if (-not (Test-Path (Join-Path $Root $rel))) { $missing += $rel }
}
if ($missing.Count -gt 0) {
    throw ("Net fajlov v repozitorii:`n  " + ($missing -join "`n  "))
}

Write-Host "==> upload $($hotfixFiles.Count) fajlov na glavny PC (fleet token) ..." -ForegroundColor Cyan
$n = 0
foreach ($rel in $hotfixFiles) {
    $n++
    $local = Join-Path $Root $rel
    $remote = $rel.Replace("\", "/")
    Write-Host "  [$n/$($hotfixFiles.Count)] $remote" -ForegroundColor Gray
    Send-HubFileLocal -Base $HubUrl -Token $token -RelPath $remote -LocalPath $local
}

if ($WithWeb) {
    Write-Host "==> npm run build (lokalno) + upload web/out ..." -ForegroundColor Cyan
    Push-Location (Join-Path $Root "web")
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    $webOut = Join-Path $Root "web\out"
    Get-ChildItem -Path $webOut -Recurse -File | ForEach-Object {
        $rel = "web/out/" + ($_.FullName.Substring($webOut.Length + 1).Replace("\", "/"))
        Send-HubFileLocal -Base $HubUrl -Token $token -RelPath $rel -LocalPath $_.FullName
    }
}

if (-not $NoRestart) {
    Write-Host "==> restart backend na glavnom PC ..." -ForegroundColor Cyan
    $restartCmd = "& '.\scripts\stop-backend.ps1' -Quiet; Start-Sleep -Seconds 2; Start-Process powershell.exe -WindowStyle Normal -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','.\run-backend.ps1','-NoPause'"
    $body = @{
        command     = $restartCmd
        timeout_sec = 120
    } | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Method POST -Uri "$HubUrl/api/fleet/local/powershell" `
            -Headers (@{ "Content-Type" = "application/json"; Authorization = "Bearer $token" }) `
            -Body $body -TimeoutSec 130
        if ($r.exit_code -ne 0) {
            Write-Host "WARN: restart exit $($r.exit_code)" -ForegroundColor Yellow
            if ($r.stderr) { Write-Host $r.stderr -ForegroundColor Red }
            if ($r.stdout) { Write-Host $r.stdout -ForegroundColor Gray }
        } else {
            Write-Host "OK  restart command sent" -ForegroundColor Green
        }
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        if ($code -eq 503 -or $code -eq 502 -or $code -eq 500) {
            Write-Host "OK  hub ubil sebya pri restart (HTTP $code) - eto normalno, zhdu podnyatiya..." -ForegroundColor Yellow
        } else {
            throw
        }
    }
    Write-Host "    zhdu hub 8765 (do 120 sek) ..." -ForegroundColor Gray
    $ok = $false
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Seconds 5
        try {
            Invoke-RestMethod -Uri "$HubUrl/api/fleet/config" -TimeoutSec 5 | Out-Null
            $ok = $true
            break
        } catch { }
    }
    if (-not $ok) {
        Write-Host "WARN: hub ne otvechaet posle avto-restart." -ForegroundColor Yellow
        Write-Host "      Na GLAVNOM PC otkroj: C:\Users\AiCreator\Desktop\video-pipeline" -ForegroundColor Yellow
        Write-Host "      i zapusti: powershell -ep bypass -File .\run-backend.ps1" -ForegroundColor Yellow
    } else {
        Write-Host "OK  hub snova online" -ForegroundColor Green
    }
}

if (Test-HubImportBundle -Base $HubUrl) {
    Write-Host "OK  /api/fleet/import-bundle EST. Hub gotov prinimat bundle." -ForegroundColor Green
} else {
    Write-Host "FAIL: import-bundle vse eshche NET (404). Smotri put repozitoriya na glavnom PC." -ForegroundColor Red
    exit 1
}

Write-Host "==> GOTOVO. Teper: apply-local.ps1 -SkipBuild, potom Otpravit #17" -ForegroundColor Green
