# Worker PC quick reference (copy-paste)

## First time on second PC

```powershell
# 1) Clone repo (if not yet)
cd $env:USERPROFILE\Desktop
git clone https://github.com/rohos228-spec/video-pipeline.git
cd video-pipeline
git checkout feature/fleet-montage-queue-v161

# 2) Full setup (Tailscale + build + agent)
powershell -ExecutionPolicy Bypass -File .\scripts\setup-second-pc.ps1
```

When Tailscale asks — log in with **the same account as hub**.

## Firewall (Admin PowerShell, once)

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\AiCreator\Desktop\video-pipeline\scripts\allow-studio-firewall.ps1"
```

## Update only (after git pull on hub)

```powershell
cd C:\Users\AiCreator\Desktop\video-pipeline
powershell -ExecutionPolicy Bypass -File .\scripts\setup-second-pc.ps1 -SkipTailscaleLogin
```

## Manual checks

```powershell
& "${env:ProgramFiles}\Tailscale\tailscale.exe" status
& "${env:ProgramFiles}\Tailscale\tailscale.exe" ip -4
curl http://100.72.202.35:8765/api/health
```

Hub IP: **100.72.202.35**  
Worker example: **nucbox-m6ultra** / **100.100.240.106**
