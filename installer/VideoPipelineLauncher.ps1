# Video Pipeline Studio GUI launcher (ASCII-only for Windows PowerShell 5.x)
# Double-click VideoPipelineStudio.cmd in repo root

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = (Get-Location).Path
}
Set-Location $Root

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    if ($machine -or $user) {
        $env:Path = "$machine;$user"
    }
}

Refresh-Path

$script:LogBox = $null
$script:StatusLbl = $null
$script:LauncherLogFile = Join-Path $Root "data\launcher.log"

function Write-Log([string]$Text, [string]$Color = "Black") {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $Text"
    try {
        $logDir = Split-Path -Parent $script:LauncherLogFile
        if (-not (Test-Path $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
        Add-Content -Path $script:LauncherLogFile -Value $line -Encoding UTF8
    } catch { }
    if (-not $script:LogBox) {
        Write-Host $line
        return
    }
    $hadSelection = $script:LogBox.SelectionLength -gt 0
    $script:LogBox.SelectionColor = $Color
    $script:LogBox.AppendText("$line`r`n")
    if (-not $hadSelection) {
        $script:LogBox.ScrollToCaret()
    }
}

function Get-NpmCmd {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) { return $npm.Source }
    $guess = Join-Path ${env:ProgramFiles} "nodejs\npm.cmd"
    if (Test-Path $guess) { return $guess }
    return $null
}

function Test-BackendReady([int]$TimeoutSec = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-StudioVersionLabel {
    $vf = Join-Path $Root "web\STUDIO_VERSION"
    if (-not (Test-Path $vf)) { return "?" }
    $lines = Get-Content $vf -ErrorAction SilentlyContinue
    if (-not $lines -or $lines.Count -lt 1) { return "?" }
    $build = $lines[0].Trim()
    $sha = "dev"
    if ($lines.Count -gt 1 -and $lines[1].Trim()) {
        $raw = $lines[1].Trim()
        $sha = if ($raw.Length -gt 7) { $raw.Substring(0, 7) } else { $raw }
    }
    if ($sha -eq "dev") { return "v$build" }
    return "v$build · $sha"
}

function Test-WebUiBuilt {
    return Test-Path (Join-Path $Root "web\out\index.html")
}

function Warn-WebUiMissing {
    if (Test-WebUiBuilt) { return $true }
    Write-Log "web/out/index.html missing - run button 5 Update all or 6 Build Web UI" "DarkOrange"
    return $false
}

function Test-WebBuildStale {
    $out = Join-Path $Root "web\out\index.html"
    if (-not (Test-Path $out)) { return $true }
    $outTime = (Get-Item $out).LastWriteTimeUtc
    $verFile = Join-Path $Root "web\STUDIO_VERSION"
    if ((Test-Path $verFile) -and (Get-Item $verFile).LastWriteTimeUtc -gt $outTime) {
        return $true
    }
    $srcRoot = Join-Path $Root "web\src"
    if (Test-Path $srcRoot) {
        $newestSrc = Get-ChildItem $srcRoot -Recurse -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
        if ($newestSrc -and $newestSrc.LastWriteTimeUtc -gt $outTime) {
            return $true
        }
    }
    return $false
}

function Ensure-WebBuilt {
    if (-not (Test-WebBuildStale)) { return $true }
    if (Test-Path (Join-Path $Root "web\out\index.html")) {
        Write-Log "Web UI stale (sources newer than web/out) - rebuilding..." "DarkOrange"
    } else {
        Write-Log "web/out missing - building UI (npm install + build)..." "DarkOrange"
    }
    $npm = Get-NpmCmd
    if (-not $npm) {
        Write-Log "npm not found. Run button 1 Full install or install Node.js" "DarkRed"
        return $false
    }
    return Invoke-Cmd "Build Web UI" {
        Push-Location (Join-Path $Root "web")
        & $npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        & $npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        Pop-Location
        if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
            throw "web/out/index.html still missing after build"
        }
    }
}

function Start-BackendWindow {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
}

function Copy-LauncherLogs {
    if (-not $script:LogBox) { return }
    $text = if ($script:LogBox.SelectionLength -gt 0) {
        $script:LogBox.SelectedText
    } else {
        $script:LogBox.Text
    }
    if ([string]::IsNullOrWhiteSpace($text)) {
        [System.Windows.Forms.MessageBox]::Show(
            "Log is empty.",
            "Copy logs",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        return
    }
    try {
        [System.Windows.Forms.Clipboard]::Clear()
        [System.Windows.Forms.Clipboard]::SetText($text)
        [System.Windows.Forms.MessageBox]::Show(
            "Copied to clipboard.`n`nBackup file:`n$($script:LauncherLogFile)",
            "Copy logs",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            "Clipboard failed: $($_.Exception.Message)`n`nOpen log file instead:`n$($script:LauncherLogFile)",
            "Copy logs",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        ) | Out-Null
    }
}

function Open-LauncherLogFile {
    if (-not (Test-Path $script:LauncherLogFile)) {
        Write-Log "Log file not created yet" "DarkOrange"
        return
    }
    Start-Process notepad $script:LauncherLogFile
}

function Invoke-Cmd([string]$Label, [scriptblock]$Block) {
    Write-Log "> $Label" "DarkBlue"
    try {
        & $Block
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            throw "exit code $LASTEXITCODE"
        }
        Write-Log "OK $Label" "DarkGreen"
        return $true
    }
    catch {
        Write-Log "FAIL $Label`: $($_.Exception.Message)" "DarkRed"
        return $false
    }
}

function Invoke-ExternalLog([string]$Label, [string]$FileName, [string[]]$ArgList) {
    Write-Log "> $Label" "DarkBlue"
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $FileName
        $psi.Arguments = ($ArgList -join " ")
        $psi.WorkingDirectory = $Root
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        while (-not $p.StandardOutput.EndOfStream) {
            $line = $p.StandardOutput.ReadLine()
            if ($line) { Write-Log $line "Gray" }
        }
        while (-not $p.StandardError.EndOfStream) {
            $line = $p.StandardError.ReadLine()
            if ($line) { Write-Log $line "DarkOrange" }
        }
        $p.WaitForExit()
        if ($p.ExitCode -ne 0) { throw "exit code $($p.ExitCode)" }
        Write-Log "OK $Label" "DarkGreen"
        return $true
    }
    catch {
        Write-Log "FAIL $Label`: $($_.Exception.Message)" "DarkRed"
        return $false
    }
}

function Get-VenvPython {
    $p = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $p) { return $p }
    return $null
}

function Test-Installed {
    return (Test-Path (Get-VenvPython)) -and (Test-Path (Join-Path $Root ".env"))
}

function Get-GitBranch {
    try {
        $b = git -C $Root rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return $b.Trim() }
    } catch { }
    return "?"
}

function Get-GitHead {
    try {
        $h = git -C $Root rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return $h.Trim() }
    } catch { }
    return "?"
}

function Update-StatusLabel {
    if (-not $script:StatusLbl) { return }
    $installed = Test-Installed
    $branch = Get-GitBranch
    $uiVer = Get-StudioVersionLabel
    $webBuilt = Test-Path (Join-Path $Root "web\out\index.html")
    $webStale = Test-WebBuildStale
    $status = if ($installed) { "Installed" } else { "Need install" }
    if ($webBuilt -and $webStale) {
        $web = "UI stale - rebuild"
    } elseif ($webBuilt) {
        $web = "UI $uiVer"
    } else {
        $web = "UI not built"
    }
    $script:StatusLbl.Text = "Status: $status | Git: $branch | $web"
    $script:StatusLbl.ForeColor = if ($installed) {
        [System.Drawing.Color]::DarkGreen
    } else {
        [System.Drawing.Color]::DarkOrange
    }
}

function Sync-ProjectFromGit {
    $beforeHead = Get-GitHead
    $branch = Get-GitBranch
    Write-Log "Git: branch=$branch commit=$beforeHead" "Gray"
    if (-not (Invoke-Cmd "git fetch origin" {
        git -C $Root fetch origin 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    })) {
        return $false
    }
    $pulled = $false
    if ($branch -and $branch -ne "?") {
        if (Invoke-Cmd "git pull origin $branch" {
            git -C $Root checkout $branch 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            if ($LASTEXITCODE -ne 0) { throw "git checkout $branch failed" }
            git -C $Root pull origin $branch 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            if ($LASTEXITCODE -ne 0) { throw "git pull $branch failed" }
        })) {
            $pulled = $true
        }
    }
    if (-not $pulled) {
        if (-not (Invoke-Cmd "git pull origin devin/windows-installer" {
            git -C $Root checkout devin/windows-installer 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }
            git -C $Root pull origin devin/windows-installer 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            if ($LASTEXITCODE -ne 0) { throw "git pull failed" }
        })) {
            return $false
        }
    }
    $afterHead = Get-GitHead
    if ($beforeHead -eq $afterHead) {
        Write-Log "Git up to date ($afterHead)" "Gray"
    } else {
        Write-Log "Git: $beforeHead -> $afterHead" "DarkGreen"
    }
    return $true
}

function Sync-PythonAndWeb {
    param(
        [switch]$AlwaysBuildUi
    )
    $py = Get-VenvPython
    if (-not $py) {
        Write-Log "No venv - run button 1 Full install" "DarkOrange"
        return $false
    }
    if (-not (Invoke-Cmd "pip install -e .[dev]" {
        & $py -m pip install -e ".[dev]" 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    })) {
        return $false
    }
    $needBuild = $AlwaysBuildUi -or (Test-WebBuildStale) -or (-not (Test-WebUiBuilt))
    if (-not $needBuild) {
        Write-Log "Web UI already built: $(Get-StudioVersionLabel)" "Gray"
        return $true
    }
    $npm = Get-NpmCmd
    if (-not $npm) {
        Write-Log "npm not found - run button 1" "DarkRed"
        return $false
    }
    if (-not (Invoke-Cmd "npm install + build (web/out)" {
        Push-Location (Join-Path $Root "web")
        & $npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        & $npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        Pop-Location
        if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
            throw "web/out/index.html missing after build"
        }
    })) {
        return $false
    }
    Write-Log "UI built: $(Get-StudioVersionLabel) (web/out)" "DarkGreen"
    return $true
}

function Show-StudioVersionFromApi {
    try {
        $sv = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 5
        Write-Log "API studio-version: $($sv.label) backend=$($sv.backend_attach)" "DarkGreen"
    } catch {
        Write-Log "API studio-version недоступен (бэкенд ещё стартует?)" "DarkOrange"
    }
    Write-Log "Файл web/STUDIO_VERSION: $(Get-StudioVersionLabel)" "Gray"
}

function Do-FullUpdate {
    if (-not (Sync-ProjectFromGit)) {
        Write-Log "Update aborted: git" "DarkRed"
        return
    }
    if (-not (Sync-PythonAndWeb -AlwaysBuildUi)) {
        Write-Log "Update aborted: pip/npm" "DarkRed"
        return
    }
    Update-StatusLabel
    Write-Log "Next: * Update + Start or 2 Start Studio" "DarkGreen"
}

function Do-UpdateAndRun {
    Write-Log "=== Update + Start (git, backend, UI, browser) ===" "DarkBlue"
    if (-not (Test-Installed)) {
        $ok = Invoke-ExternalLog "Install" "powershell.exe" @(
            "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
        )
        if (-not $ok) { return }
    }
    if (-not (Sync-ProjectFromGit)) {
        Write-Log "Stopped: fix git (commit/stash) and retry" "DarkRed"
        return
    }
    if (-not (Sync-PythonAndWeb -AlwaysBuildUi)) {
        Write-Log "Stopped: pip or npm build failed" "DarkRed"
        return
    }
    Update-StatusLabel
    Do-Stop
    Start-Sleep -Seconds 2
    Start-BackendWindow
    Write-Log "Waiting for backend http://127.0.0.1:8765 ..." "Gray"
    if (Test-BackendReady) {
        Show-StudioVersionFromApi
        Start-Process "http://127.0.0.1:8765"
        Write-Log "Ready. Backend window must stay open. Browser: Ctrl+F5 if old UI." "DarkGreen"
    } else {
        Write-Log "Backend did not respond in 90s - see backend PowerShell window" "DarkRed"
        Write-Log "Manual: .\run-backend.ps1" "DarkOrange"
    }
}

function Do-QuickStart {
    Do-UpdateAndRun
}

function Do-Install {
    Invoke-ExternalLog "Install" "powershell.exe" @(
        "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
    )
    Update-StatusLabel
}

function Do-StartStudio {
    if (-not (Get-VenvPython)) { throw "Run install first (button 1)" }
    if (-not (Ensure-WebBuilt)) { throw "Web UI build failed" }
    if (-not (Test-WebUiBuilt)) {
        Write-Log "Web UI still missing after build attempt" "DarkRed"
        return
    }
    Do-Stop
    Start-Sleep -Seconds 1
    Start-BackendWindow
    Write-Log "Waiting for backend http://127.0.0.1:8765 ..." "Gray"
    if (Test-BackendReady) {
        Write-Log "Studio ready at http://127.0.0.1:8765 (backend window must stay open)" "DarkGreen"
        Start-Process "http://127.0.0.1:8765"
    }     else {
        Write-Log "Backend did not respond in 90s - see errors in the backend PowerShell window" "DarkRed"
        Write-Log "Tip: run .\run-backend.ps1 manually in this folder to see the error" "DarkOrange"
        Write-Log "If log says 45s here — git pull (launcher is outdated)" "DarkOrange"
    }
}

function Do-StartTelegram {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start.ps1")
    ) -WorkingDirectory $Root
    Write-Log "Telegram + Studio in new window" "DarkGreen"
}

function Do-Stop {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Root*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Log "Processes stopped" "DarkGreen"
}

function Do-BuildWeb {
    $npm = Get-NpmCmd
    if (-not $npm) { throw "npm not found - run button 1 Full install" }
    Invoke-Cmd "Build Web UI" {
        Push-Location (Join-Path $Root "web")
        & $npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        & $npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        Pop-Location
    }
    Update-StatusLabel
}

function Do-DevUi {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command", "Set-Location '$Root\web'; npm run dev"
    )
}

function Do-Tests {
    $py = Get-VenvPython
    if (-not $py) { throw "Run install first" }
    & $py -m pytest (Join-Path $Root "tests") -q --tb=short 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-Lint {
    ruff check $Root 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-Seed {
    $py = Get-VenvPython
    if (-not $py) { throw "Run install first" }
    & $py -m app.seed_pilot 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-OpenEnv {
    $envPath = Join-Path $Root ".env"
    if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $Root ".env.example") $envPath }
    Start-Process notepad $envPath
}

function Do-OpenData {
    $d = Join-Path $Root "data"
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
    Start-Process explorer $d
}

function Do-ResetDb {
    $db = Join-Path $Root "data\state.db"
    if (Test-Path $db) {
        $ans = [System.Windows.Forms.MessageBox]::Show(
            "Delete state.db?", "Confirm", "YesNo", "Warning"
        )
        if ($ans -eq "Yes") { Remove-Item $db -Force; Write-Log "state.db deleted" "DarkOrange" }
    } else {
        Write-Log "state.db not found" "Gray"
    }
}

function Do-ChromeHint {
    Write-Log "chrome.exe --remote-debugging-port=29229 --user-data-dir=%TEMP%\vp-chrome" "DarkMagenta"
}

function Do-OpenBrowser {
    if (-not (Test-BackendReady -TimeoutSec 3)) {
        Write-Log "Backend not running - click 2 Start Studio first" "DarkOrange"
        return
    }
    Start-Process "http://127.0.0.1:8765"
}

$commands = @(
    @{ Label = "* Update + Start"; Tip = "git pull + pip + UI build + backend + browser"; Fn = { Do-UpdateAndRun } }
    @{ Label = "1. Full install"; Tip = "Python, venv, FFmpeg, Node, .env"; Fn = { Do-Install } }
    @{ Label = "2. Start Studio"; Tip = "http://127.0.0.1:8765"; Fn = { Do-StartStudio } }
    @{ Label = "3. Telegram mode"; Tip = "Bot + API (token in .env)"; Fn = { Do-StartTelegram } }
    @{ Label = "4. Stop"; Tip = "Stop python for this project"; Fn = { Do-Stop } }
    @{ Label = "5. Update all"; Tip = "git pull + pip + npm build"; Fn = { Do-FullUpdate } }
    @{ Label = "6. Build Web UI"; Tip = "npm run build"; Fn = { Do-BuildWeb } }
    @{ Label = "7. Dev UI :3000"; Tip = "npm run dev"; Fn = { Do-DevUi } }
    @{ Label = "8. Tests"; Tip = "pytest"; Fn = { Do-Tests } }
    @{ Label = "9. Lint"; Tip = "ruff check"; Fn = { Do-Lint } }
    @{ Label = "10. Seed demo"; Tip = "Demo project"; Fn = { Do-Seed } }
    @{ Label = "11. Open .env"; Tip = "Settings file"; Fn = { Do-OpenEnv } }
    @{ Label = "12. Open data/"; Tip = "Database folder"; Fn = { Do-OpenData } }
    @{ Label = "13. Chrome CDP"; Tip = "Port 29229 hint"; Fn = { Do-ChromeHint } }
    @{ Label = "14. Reset DB"; Tip = "Delete state.db"; Fn = { Do-ResetDb } }
    @{ Label = "15. Open browser"; Tip = "Open Studio URL"; Fn = { Do-OpenBrowser } }
)

$form = New-Object System.Windows.Forms.Form
$form.Text = "Video Pipeline Studio $(Get-StudioVersionLabel) git:$(Get-GitHead)"
$form.Size = New-Object System.Drawing.Size(740, 720)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Video Pipeline - install, update, run"
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(16, 12)
$title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$pathLbl = New-Object System.Windows.Forms.Label
$pathLbl.Text = "Folder: $Root"
$pathLbl.AutoSize = $true
$pathLbl.Location = New-Object System.Drawing.Point(16, 36)
$pathLbl.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($pathLbl)

$StatusLbl = New-Object System.Windows.Forms.Label
$StatusLbl.AutoSize = $true
$StatusLbl.Location = New-Object System.Drawing.Point(16, 54)
$form.Controls.Add($StatusLbl)
$script:StatusLbl = $StatusLbl

$hintLbl = New-Object System.Windows.Forms.Label
$hintLbl.Text = "Every day: * Update + Start (v in log). URL http://127.0.0.1:8765 — keep backend window open."
$hintLbl.AutoSize = $true
$hintLbl.Location = New-Object System.Drawing.Point(16, 72)
$hintLbl.ForeColor = [System.Drawing.Color]::DimGray
$hintLbl.MaximumSize = New-Object System.Drawing.Size(700, 0)
$form.Controls.Add($hintLbl)

$LogBox = New-Object System.Windows.Forms.RichTextBox
$LogBox.Location = New-Object System.Drawing.Point(16, 418)
$LogBox.Size = New-Object System.Drawing.Size(700, 212)
$LogBox.ReadOnly = $true
$LogBox.ShortcutsEnabled = $true
$LogBox.HideSelection = $false
$LogBox.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 252)
$logMenu = New-Object System.Windows.Forms.ContextMenuStrip
$copyMenuItem = $logMenu.Items.Add("Copy (Ctrl+C)")
$copyMenuItem.Add_Click({ Copy-LauncherLogs })
$LogBox.ContextMenuStrip = $logMenu
$LogBox.Add_KeyDown({
    param($sender, $e)
    if ($e.Control -and $e.KeyCode -eq [System.Windows.Forms.Keys]::C) {
        Copy-LauncherLogs
        $e.Handled = $true
    }
})
$form.Controls.Add($LogBox)
$script:LogBox = $LogBox

$copyLogsBtn = New-Object System.Windows.Forms.Button
$copyLogsBtn.Text = "Copy logs"
$copyLogsBtn.Size = New-Object System.Drawing.Size(88, 26)
$copyLogsBtn.Location = New-Object System.Drawing.Point(16, 392)
$copyLogsBtn.Add_Click({ Copy-LauncherLogs })
$form.Controls.Add($copyLogsBtn)

$openLogBtn = New-Object System.Windows.Forms.Button
$openLogBtn.Text = "Open log file"
$openLogBtn.Size = New-Object System.Drawing.Size(88, 26)
$openLogBtn.Location = New-Object System.Drawing.Point(110, 392)
$openLogBtn.Add_Click({ Open-LauncherLogFile })
$form.Controls.Add($openLogBtn)

$logHintLbl = New-Object System.Windows.Forms.Label
$logHintLbl.Text = "Ctrl+C / Copy logs / data\launcher.log — backend must stay open on :8765"
$logHintLbl.AutoSize = $true
$logHintLbl.Location = New-Object System.Drawing.Point(206, 396)
$logHintLbl.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($logHintLbl)

$tip = New-Object System.Windows.Forms.ToolTip
$script:WriteLogCmd = Get-Command -Name Write-Log -CommandType Function
$y = 98
$col = 0
for ($i = 0; $i -lt $commands.Count; $i++) {
    $cmd = $commands[$i]
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $cmd.Label
    $btn.Size = New-Object System.Drawing.Size(228, 36)
    $btn.Location = New-Object System.Drawing.Point((16 + $col * 234), $y)
    $fn = $cmd.Fn
    $writeLogCmd = $script:WriteLogCmd
    $btn.Add_Click({
        param($sender, $e)
        try {
            & $fn
            Update-StatusLabel
        } catch {
            $errMsg = $_.Exception.Message
            if ($writeLogCmd) {
                & $writeLogCmd $errMsg "DarkRed"
            } else {
                [System.Windows.Forms.MessageBox]::Show($errMsg, "Error") | Out-Null
            }
        }
    }.GetNewClosure())
    $tip.SetToolTip($btn, $cmd.Tip)
    $form.Controls.Add($btn)
    $col++
    if ($col -ge 3) { $col = 0; $y += 44 }
}

Update-StatusLabel
Write-Log "Ready. Double-click VideoPipelineStudio.cmd to reopen." "DarkGreen"
[void]$form.ShowDialog()
