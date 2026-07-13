# Скачать исправления с GitHub без git pull (PowerShell 5.1, ASCII).
# Запуск из корня репозитория:
#   powershell -ExecutionPolicy Bypass -File scripts\Update-Hotfix-FromGitHub.ps1
#
# Ветка с фиксами: devin/windows-installer

$ErrorActionPreference = "Stop"

$Branch = "devin/windows-installer"
$Repo = "rohos228-spec/video-pipeline"
$BaseUrl = "https://raw.githubusercontent.com/$Repo/$Branch"

$Files = @(
    "app/hotfix_build.py",
    "app/services/chrome_recovery.py",
    "app/bots/chrome_cdp.py",
    "app/bots/chatgpt.py",
    "app/services/xlsx_versioning.py",
    "app/services/xlsx_gpt_flow.py",
    "app/services/xlsx_step_runners.py",
    "app/services/step_failure_policy.py",
    "app/services/gen_queue.py",
    "app/services/gen_queue_run.py",
    "app/services/sidebar_layout.py",
    "app/orchestrator/auto_advance.py",
    "app/web/routers/sidebar_layout.py",
    "app/orchestrator/auto_advance.py",
    "app/main.py",
    "app/services/run_sync.py",
    "app/services/project_state.py",
    "app/services/project_control.py",
    "app/services/project_steps.py",
    "app/orchestrator/graph/planner.py",
    "app/services/reset_step.py",
    "app/services/chatgpt_xlsx.py",
    "app/web/routers/projects.py",
    "app/web/studio_dry_run.py",
    "app/services/step_data_guard.py",
    "app/services/plan_validation.py",
    "scripts/Pull-Hotfix-Safe.ps1",
    "scripts/Update-Hotfix-FromGitHub.ps1",
    "PULL-HOTFIX.cmd",
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

# Verify hotfix markers landed on disk
$markers = @{
    "app\services\project_control.py" = "_set_user_stop_gate"
    "app\services\sidebar_layout.py"  = "_normalize_gen_queue"
    "app\services\xlsx_versioning.py" = "normalize_xlsx_to_reference_layout"
    "app\bots\chatgpt.py"             = "attach-guard-v85-iron-stop"
    "app\services\chrome_recovery.py" = "handle_chrome_step_failure"
    "app\hotfix_build.py"             = "hotfix-20260711-dequeue-no-autoadvance-v10"
}
$missing = 0
foreach ($rel in $markers.Keys) {
    $path = Join-Path $Root $rel
    if (-not (Test-Path $path)) {
        Write-Host "MISSING FILE: $rel" -ForegroundColor Red
        $missing++
        continue
    }
    $hit = Select-String -Path $path -Pattern $markers[$rel] -Quiet -ErrorAction SilentlyContinue
    if (-not $hit) {
        Write-Host "MARKER NOT FOUND in $rel : $($markers[$rel])" -ForegroundColor Red
        $missing++
    }
}
if ($missing -eq 0) {
    Write-Host "Hotfix markers OK (v2 stop+queue+xlsx)" -ForegroundColor Green
} else {
    Write-Host "Hotfix verify FAILED: $missing problem(s)" -ForegroundColor Red
}

# Clear stale bytecode
Get-ChildItem -Path (Join-Path $Root "app") -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    Write-Host "> stop backend" -ForegroundColor Cyan
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet 2>$null
}

$hotfixId = "unknown"
$hotfixPath = Join-Path $Root "app\hotfix_build.py"
if (Test-Path $hotfixPath) {
    $raw = Get-Content -LiteralPath $hotfixPath -Raw -Encoding UTF8
    if ($raw -match 'PIPELINE_HOTFIX_ID\s*=\s*"([^"]+)"') {
        $hotfixId = $Matches[1]
    }
}

Write-Host ""
Write-Host "Done. Restart Studio (run-backend.ps1 or VideoPipelineStudio.cmd)." -ForegroundColor Green
Write-Host "Expected logs after fix:" -ForegroundColor DarkGray
Write-Host ("  startup: hotfix=" + $hotfixId) -ForegroundColor DarkGray
Write-Host ("  GET /api/studio-version -> pipeline_hotfix: " + $hotfixId) -ForegroundColor DarkGray
Write-Host "  gen_queue: #7 done -> started #14 (stale user_stop cleared)" -ForegroundColor DarkGray
Write-Host "  STOP on running step: user_stop blocks until manual play" -ForegroundColor DarkGray

exit $(if ($fail -eq 0 -and $missing -eq 0) { 0 } else { 1 })
