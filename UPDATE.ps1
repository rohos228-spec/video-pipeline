cd "C:\Users\Love Space\video-pipeline"
git fetch origin cursor/ai-node-badge-title-panel-977b
git reset --hard origin/cursor/ai-node-badge-title-panel-977b
Write-Host "OK version (Studio + bot/outsee):"
Get-Content web\STUDIO_VERSION -TotalCount 2
pause
