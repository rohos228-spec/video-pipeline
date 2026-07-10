# Скачать исправления с GitHub без git pull (PowerShell 5.1, ASCII).
# Запуск из корня репозитория:
#   powershell -ExecutionPolicy Bypass -File scripts\Update-Hotfix-FromGitHub.ps1
#
# Ветка с фиксами: cursor/restore-legacy-prompts-fc98 (PR #89)

$ErrorActionPreference = "Stop"

$Branch = "cursor/restore-legacy-prompts-fc98"
$Repo = "rohos228-spec/video-pipeline"
$BaseUrl = "https://raw.githubusercontent.com/$Repo/$Branch"

$Files = @(
    "app/bots/chatgpt.py",
    "app/services/xlsx_versioning.py",
    "app/services/gen_queue.py",
    "app/services/gen_queue_run.py",
    "app/orchestrator/auto_advance.py",
    "app/main.py",
    "app/services/run_sync.py",
    "app/services/project_state.py",
    "app/services/project_control.py",
    "app/services/step_data_guard.py",
    "app/services/plan_validation.py",
    "web/src/lib/node-run-status.ts"
)

function Get-RepoRoot {
    param([string]$Start)
    $dir = $Start
    for ($i = 0; $i -lt 12; $i++) {
        if (Test-Path (Join-Path $dir "pyproject.toml")) {
            return (Resolve-Path -LiteralPath $dir).Path
        }
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return $null
}

$Root = Get-RepoRoot -Start $PSScriptRoot
if (-not $Root) {
    $Root = Get-RepoRoot -Start (Get-Location).Path
}
if (-not $Root) {
    Write-Host "ERROR: pyproject.toml not found" -ForegroundColor Red
    exit 1
}

Write-Host "=== Hotfix update ($Branch) ===" -ForegroundColor Cyan
Write-Host "Repo: $Root" -ForegroundColor DarkGray

$ok = 0
$fail = 0
foreach ($rel in $Files) {
    $dest = Join-Path $Root ($rel -replace "/", "\")
    $parent = Split-Path -Parent $dest
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $url = "$BaseUrl/$rel"
    Write-Host "> $rel" -ForegroundColor Gray
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        $ok++
    } catch {
        Write-Host "  FAIL: $_" -ForegroundColor Red
        $fail++
    }
}

Write-Host ""
Write-Host "Downloaded: $ok  Failed: $fail" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })

# Clear stale bytecode
Get-ChildItem -Path (Join-Path $Root "app") -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    Write-Host "> stop backend" -ForegroundColor Cyan
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet 2>$null
}

Write-Host ""
Write-Host "Done. Restart Studio (run-backend.ps1 or VideoPipelineStudio.cmd)." -ForegroundColor Green
Write-Host "Expected logs after fix:" -ForegroundColor DarkGray
Write-Host "  plan xlsx: global-label, al=Skachat -> plan.xlsx (extra GPT sheets OK)" -ForegroundColor DarkGray
Write-Host "  script txt: plain-label / api-variants -> voiceover.txt (~10s)" -ForegroundColor DarkGray
Write-Host "  queue: auto_advance #N plan_ready - zhdem ochered, blokiruet #M" -ForegroundColor DarkGray

exit $(if ($fail -eq 0) { 0 } else { 1 })
