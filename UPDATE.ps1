cd "C:\Users\Love Space\video-pipeline"
git fetch origin cursor/video-wait-like-images-977b
git reset --hard origin/cursor/video-wait-like-images-977b
Write-Host "OK version:"
Get-Content web\STUDIO_VERSION -TotalCount 2
pause
