# =============================================================================
# video-pipeline FLEET — все команды в одном месте
# =============================================================================
# HUB (ПК монтажа)     -> блок 1 + 2
# WORKER (генерация)   -> блок 3
# =============================================================================

$Repo = "$env:USERPROFILE\Desktop\video-pipeline"
$HubToken = "vpHub_k7Nx9mQ2pL5wR8tY4zA1bC6dE3fG0hJ"
$WebUser  = "admin"
$WebPass  = "MontageHub2026"

# Hub Tailscale IP (this PC after tailscale login)
$HubTailscaleIp    = "100.72.202.35"
$WorkerTailscaleIp = "100.100.240.106"

$HubUrl    = "http://${HubTailscaleIp}:8765"
$WorkerUrl = "http://${WorkerTailscaleIp}:8765"


# =============================================================================
# БЛОК 0 — перейти в проект (любой ПК)
# =============================================================================
Set-Location $Repo
$Py = Join-Path $Repo ".venv\Scripts\python.exe"


# =============================================================================
# БЛОК 1 — HUB: установка ASR + CUDA + сборка UI (один раз на ПК монтажа)
# =============================================================================
function Setup-Hub-Once {
    Set-Location $Repo

    Write-Host "==> NVIDIA ASR (NeMo Parakeet)..." -ForegroundColor Cyan
    powershell -ExecutionPolicy Bypass -File "$Repo\scripts\install-nvidia-asr.ps1"

    Write-Host "==> PyTorch CUDA check..." -ForegroundColor Cyan
    $cuda = & $Py -c "import torch; print(torch.cuda.is_available())"
    if ($cuda -ne "True") {
        & $Py -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    }
    & $Py -c "import torch; print('cuda:', torch.cuda.is_available())"

    Write-Host "==> Web UI build..." -ForegroundColor Cyan
    Push-Location "$Repo\web"
    npm install
    npm run build
    Pop-Location

    Write-Host "OK hub setup" -ForegroundColor Green
}


# =============================================================================
# БЛОК 2 — HUB: .env + Tailscale + запуск Studio
# =============================================================================
function Start-Hub {
    param(
        [string]$TailscaleIp = $HubTailscaleIp
    )

    Set-Location $Repo
    $url = "http://${TailscaleIp}:8765"

    # авто IP если Tailscale установлен
    if (Get-Command tailscale -ErrorAction SilentlyContinue) {
        $auto = (tailscale ip -4 2>$null | Select-Object -First 1).ToString().Trim()
        if ($auto) {
            $TailscaleIp = $auto
            $url = "http://${TailscaleIp}:8765"
            Write-Host "Tailscale IP: $TailscaleIp" -ForegroundColor Green
        }
    }

    function Set-Line($key, $val) {
        $p = Join-Path $Repo ".env"
        $t = Get-Content $p -Raw
        if ($t -match "(?m)^$key=") {
            $t = [regex]::Replace($t, "(?m)^$key=.*", "$key=$val")
        } else {
            $t += "`n$key=$val"
        }
        Set-Content $p $t.TrimEnd() -Encoding UTF8
    }

    Set-Line "WEB_HOST" "0.0.0.0"
    Set-Line "WEB_PORT" "8765"
    Set-Line "ASR_BACKEND" "nvidia"
    Set-Line "FLEET_ENABLED" "true"
    Set-Line "FLEET_ROLE" "hub"
    Set-Line "FLEET_MONTAGE_HUB" "true"
    Set-Line "FLEET_HUB_IS_WORKER" "true"
    Set-Line "FLEET_AUTO_PULL" "true"
    Set-Line "FLEET_PUBLIC_URL" $url
    Set-Line "FLEET_HUB_URL" $url
    Set-Line "FLEET_AGENT_TOKEN" $HubToken
    Set-Line "FLEET_NODE_NAME" $env:COMPUTERNAME
    Set-Line "WEB_AUTH_USER" $WebUser
    Set-Line "WEB_AUTH_PASSWORD" $WebPass

    Write-Host "==> stop old backend..." -ForegroundColor Cyan
    $stop = Join-Path $Repo "scripts\stop-backend.ps1"
    if (Test-Path $stop) { & $stop -Quiet; Start-Sleep 2 }

    Write-Host "==> start Studio..." -ForegroundColor Cyan
    Write-Host "  Local:  http://127.0.0.1:8765" -ForegroundColor Green
    Write-Host "  Mesh:   $url" -ForegroundColor Green
    Write-Host "  Login:  $WebUser / $WebPass" -ForegroundColor Green

    powershell -ExecutionPolicy Bypass -File "$Repo\RUN-STUDIO.ps1"
}


# =============================================================================
# БЛОК 3 — WORKER: .env + запуск agent (на каждом ПК генерации)
# =============================================================================
function Start-Worker {
    param(
        [string]$HubIp = $HubTailscaleIp,
        [string]$WorkerIp = $WorkerTailscaleIp,
        [string]$Token = $HubToken
    )

    Set-Location $Repo
    $hub = "http://${HubIp}:8765"
    $self = "http://${WorkerIp}:8765"

    if (Get-Command tailscale -ErrorAction SilentlyContinue) {
        $auto = (tailscale ip -4 2>$null | Select-Object -First 1).ToString().Trim()
        if ($auto) { $self = "http://${auto}:8765"; Write-Host "Worker Tailscale: $auto" -ForegroundColor Green }
    }

    function Set-Line($key, $val) {
        $p = Join-Path $Repo ".env"
        if (-not (Test-Path $p)) { Copy-Item "$Repo\.env.example" $p }
        $t = Get-Content $p -Raw
        if ($t -match "(?m)^$key=") {
            $t = [regex]::Replace($t, "(?m)^$key=.*", "$key=$val")
        } else { $t += "`n$key=$val" }
        Set-Content $p $t.TrimEnd() -Encoding UTF8
    }

    Set-Line "WEB_HOST" "0.0.0.0"
    Set-Line "WEB_PORT" "8765"
    Set-Line "FLEET_ENABLED" "true"
    Set-Line "FLEET_ROLE" "agent"
    Set-Line "FLEET_MONTAGE_HUB" "false"
    Set-Line "FLEET_AUTO_PULL" "false"
    Set-Line "FLEET_HUB_URL" $hub
    Set-Line "FLEET_PUBLIC_URL" $self
    Set-Line "FLEET_AGENT_TOKEN" $Token
    Set-Line "FLEET_NODE_NAME" $env:COMPUTERNAME
    Set-Line "FLEET_IS_MAIN" "false"
    Set-Line "TELEGRAM_ENABLED" "false"

    Write-Host "Agent '$env:COMPUTERNAME' -> hub $hub" -ForegroundColor Cyan
    powershell -ExecutionPolicy Bypass -File "$Repo\RUN-STUDIO.ps1"
}


# =============================================================================
# БЛОК 4 — проверки
# =============================================================================
function Test-FleetLogin {
    $body = @{ username = $WebUser; password = $WebPass } | ConvertTo-Json
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/auth/login" -Method POST -ContentType "application/json" -Body $body
    Write-Host "Login OK, token length: $($r.token.Length)"
    return $r.token
}

function Test-FleetNodes {
    param([string]$Token)
    if (-not $Token) { $Token = Test-FleetLogin }
    $h = @{ Authorization = "Bearer $Token" }
    $nodes = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/fleet/nodes" -Headers $h
    $nodes | Format-Table id, name, status, base_url -AutoSize
}


# =============================================================================
# ЧТО ЗАПУСКАТЬ:
# =============================================================================
#
# HUB (этот ПК, первый раз):
#   . .\scripts\fleet-all.ps1
#   Setup-Hub-Once
#   Start-Hub
#
# HUB (каждый день):
#   . .\scripts\fleet-all.ps1
#   Start-Hub
#
# WORKER (другой ПК):
#   . .\scripts\fleet-all.ps1
#   # отредактируй $HubTailscaleIp в начале файла
#   Start-Worker
#
# Проверка на hub:
#   . .\scripts\fleet-all.ps1
#   Test-FleetNodes
#
# =============================================================================

Write-Host @"

Fleet script loaded from: $Repo\scripts\fleet-all.ps1

HUB first time:   Setup-Hub-Once  then  Start-Hub
HUB daily:        Start-Hub
WORKER:           Start-Worker   (set `$HubTailscaleIp at top of file)

Login:  $WebUser / $WebPass
Token:  $HubToken

"@ -ForegroundColor Cyan
