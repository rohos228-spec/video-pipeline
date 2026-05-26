cd "C:\Users\Love Space\video-pipeline"
git fetch origin cursor/fix-video-generate-button-b18c
git reset --hard origin/cursor/fix-video-generate-button-b18c
Write-Host "OK version:"
Get-Content web\STUDIO_VERSION -TotalCount 2
pause
