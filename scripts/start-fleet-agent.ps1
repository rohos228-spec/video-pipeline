# Agent video-pipeline (воркер генерации) — подключается к hub через Tailscale
param(
    [string]$HubUrl = "http://100.x.x.x:8765",
    [string]$Token = "change-me-long-secret",
    [string]$NodeName = $env:COMPUTERNAME
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$env:FLEET_ENABLED = "true"
$env:FLEET_ROLE = "agent"
$env:FLEET_MONTAGE_HUB = "false"
$env:FLEET_HUB_URL = $HubUrl
$env:FLEET_AGENT_TOKEN = $Token
$env:FLEET_NODE_NAME = $NodeName
$env:FLEET_IS_MAIN = "false"
$env:ASR_BACKEND = "whisper"

# Tailscale IP этого agent (для heartbeat hub → agent)
if (-not $env:FLEET_PUBLIC_URL) {
    Write-Host "Задайте FLEET_PUBLIC_URL=http://<tailscale-ip>:8765 в .env или env"
}

Write-Host "Starting agent '$NodeName' → hub $HubUrl"
& (Join-Path $Root "RUN-STUDIO.ps1")
