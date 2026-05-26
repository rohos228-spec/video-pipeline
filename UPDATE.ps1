cd "C:\Users\Love Space\video-pipeline"
git fetch origin devin/windows-installer
git reset --hard origin/devin/windows-installer
Write-Host "OK version (Studio + bot/outsee):"
Get-Content web\STUDIO_VERSION -TotalCount 2
pause
