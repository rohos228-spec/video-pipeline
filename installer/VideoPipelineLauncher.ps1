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

$script:LogBox = $null
$script:StatusLbl = $null

function Write-Log([string]$Text, [string]$Color = "Black") {
    if (-not $script:LogBox) { return }
    $script:LogBox.SelectionColor = $Color
    $script:LogBox.AppendText("$(Get-Date -Format 'HH:mm:ss')  $Text`r`n")
    $script:LogBox.ScrollToCaret()
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

function Update-StatusLabel {
    if (-not $script:StatusLbl) { return }
    $installed = Test-Installed
    $branch = Get-GitBranch
    $webBuilt = Test-Path (Join-Path $Root "web\out\index.html")
    $status = if ($installed) { "Installed" } else { "Need install" }
    $web = if ($webBuilt) { "UI built" } else { "UI not built" }
    $script:StatusLbl.Text = "Status: $status | Git: $branch | $web"
    $script:StatusLbl.ForeColor = if ($installed) {
        [System.Drawing.Color]::DarkGreen
    } else {
        [System.Drawing.Color]::DarkOrange
    }
}

function Do-FullUpdate {
    $branch = Get-GitBranch
    if ($branch -eq "?") {
        Write-Log "Git not found - skip pull" "DarkOrange"
    } else {
        Invoke-Cmd "git pull origin $branch" {
            git -C $Root pull origin $branch 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        }
    }
    $py = Get-VenvPython
    if (-not $py) {
        Write-Log "No venv - run button 1 first" "DarkOrange"
        return
    }
    Invoke-Cmd "pip install -e .[dev]" {
        & $py -m pip install -e ".[dev]" 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    }
    Invoke-Cmd "npm install + build" {
        Push-Location (Join-Path $Root "web")
        npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        Pop-Location
    }
    Update-StatusLabel
    Write-Log "Done. Restart: 4 Stop, then 2 Start Studio" "DarkGreen"
}

function Do-QuickStart {
    if (-not (Test-Installed)) {
        $ok = Invoke-ExternalLog "Install" "powershell.exe" @(
            "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
        )
        if (-not $ok) { return }
    }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Invoke-Cmd "Build UI" {
            Push-Location (Join-Path $Root "web")
            npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            Pop-Location
        }
    }
    Update-StatusLabel
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8765"
    Write-Log "Studio started. Keep this menu open." "DarkGreen"
}

function Do-Install {
    Invoke-ExternalLog "Install" "powershell.exe" @(
        "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
    )
    Update-StatusLabel
}

function Do-StartStudio {
    if (-not (Get-VenvPython)) { throw "Run install first (button 1)" }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-Log "web/out missing - run button 6 Build Web UI first" "DarkOrange"
        throw "UI not built. Click 6. Build Web UI, then try again."
    }
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Write-Log "Studio started in new window" "DarkGreen"
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
    Push-Location (Join-Path $Root "web")
    npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    Pop-Location
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
    Start-Process "http://127.0.0.1:8765"
}

$commands = @(
    @{ Label = "* Quick start"; Tip = "Install + UI + Studio + browser"; Fn = { Do-QuickStart } }
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
$form.Text = "Video Pipeline Studio"
$form.Size = New-Object System.Drawing.Size(740, 680)
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
$hintLbl.Text = "First time: * Quick start. Updates: 5 Update all, then 4 Stop, 2 Start."
$hintLbl.AutoSize = $true
$hintLbl.Location = New-Object System.Drawing.Point(16, 72)
$hintLbl.ForeColor = [System.Drawing.Color]::DimGray
$hintLbl.MaximumSize = New-Object System.Drawing.Size(700, 0)
$form.Controls.Add($hintLbl)

$LogBox = New-Object System.Windows.Forms.RichTextBox
$LogBox.Location = New-Object System.Drawing.Point(16, 400)
$LogBox.Size = New-Object System.Drawing.Size(700, 230)
$LogBox.ReadOnly = $true
$LogBox.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 252)
$form.Controls.Add($LogBox)
$script:LogBox = $LogBox

$tip = New-Object System.Windows.Forms.ToolTip
$y = 98
$col = 0
for ($i = 0; $i -lt $commands.Count; $i++) {
    $cmd = $commands[$i]
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $cmd.Label
    $btn.Size = New-Object System.Drawing.Size(228, 36)
    $btn.Location = New-Object System.Drawing.Point((16 + $col * 234), $y)
    $fn = $cmd.Fn
    $btn.Add_Click({
        param($sender, $e)
        try {
            & $fn
            Update-StatusLabel
        } catch {
            Write-Log $_.Exception.Message "DarkRed"
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
