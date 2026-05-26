cd "C:\Users\Love Space\video-pipeline"
git fetch origin cursor/video-restore-may24-977b
git reset --hard origin/cursor/video-restore-may24-977b
Write-Host "OK version:"
Get-Content web\STUDIO_VERSION -TotalCount 2
pause
