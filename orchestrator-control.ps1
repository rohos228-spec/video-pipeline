# Local control window for video-pipeline.
#
# Run:
#   powershell -ExecutionPolicy Bypass -File .\orchestrator-control.ps1
#
# What it does:
#   - opens the project folder
#   - can pull the selected GitHub branch
#   - starts the local orchestrator HTTP API
#   - gives a text window for API commands

[CmdletBinding()]
param(
    [string]$ProjectPath = "C:\Users\aicreator\video-pipeline",
    [string]$Branch = "devin/1779156871-combine-A-and-C-physical-clicks",
    [int]$ApiPort = 8787,
    [switch]$SkipGitPull
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"

function Add-Log {
    param([string]$Text)
    $script:OutputBox.AppendText("[$(Get-Date -Format HH:mm:ss)] $Text`r`n")
}

function Assert-Project {
    if (-not (Test-Path -LiteralPath $ProjectPath)) {
        throw "Project folder not found: $ProjectPath"
    }
    Set-Location -LiteralPath $ProjectPath
    if (-not (Test-Path -LiteralPath "pyproject.toml")) {
        throw "This is not video-pipeline root: $ProjectPath"
    }
    if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
        throw "Python venv not found. Run .\install.ps1 first."
    }
}

function Invoke-GitSync {
    Add-Log "Git: fetch/switch/pull $Branch"
    Push-Location -LiteralPath $ProjectPath
    try {
        & git fetch origin | Out-String | ForEach-Object { if ($_.Trim()) { Add-Log $_.Trim() } }
        & git switch $Branch | Out-String | ForEach-Object { if ($_.Trim()) { Add-Log $_.Trim() } }
        & git pull --ff-only origin $Branch | Out-String | ForEach-Object { if ($_.Trim()) { Add-Log $_.Trim() } }
        Add-Log "Git: done"
    } finally {
        Pop-Location
    }
}

function Test-Api {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health" -Method Get -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

function Start-OrchestratorApi {
    if (Test-Api) {
        Add-Log "API already running: http://127.0.0.1:$ApiPort"
        return
    }

    $escapedPath = $ProjectPath.Replace("'", "''")
    $cmd = "Set-Location -LiteralPath '$escapedPath'; `$env:PYTHONUTF8='1'; & '.\.venv\Scripts\python.exe' -m uvicorn app.orchestrator_api:app --host 127.0.0.1 --port $ApiPort"
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $ProjectPath -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $cmd
    )

    Add-Log "Starting API on http://127.0.0.1:$ApiPort ..."
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Api) {
            Add-Log "API ready: http://127.0.0.1:$ApiPort"
            return
        }
    }
    Add-Log "API did not answer yet. Check the new PowerShell window."
}

function Start-MainWorker {
    Add-Log "Starting main worker: .\start.ps1"
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $ProjectPath -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-File", ".\start.ps1"
    )
}

function Invoke-OrchestratorCommand {
    param([string]$Text)
    if (-not $Text.Trim()) {
        return
    }
    if (-not (Test-Api)) {
        Start-OrchestratorApi
    }
    $body = @{ text = $Text } | ConvertTo-Json -Depth 20
    try {
        $result = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$ApiPort/command" `
            -Method Post `
            -ContentType "application/json; charset=utf-8" `
            -Body $body `
            -TimeoutSec 60
        if ($result.message) {
            Add-Log $result.message
        }
        if ($result.data) {
            Add-Log ($result.data | ConvertTo-Json -Depth 20)
        }
    } catch {
        Add-Log "API error: $($_.Exception.Message)"
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            Add-Log $_.ErrorDetails.Message
        }
    }
}

Assert-Project

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "video-pipeline orchestrator API"
$form.Size = New-Object System.Drawing.Size(960, 720)
$form.StartPosition = "CenterScreen"

$topPanel = New-Object System.Windows.Forms.FlowLayoutPanel
$topPanel.Dock = "Top"
$topPanel.Height = 42
$topPanel.Padding = New-Object System.Windows.Forms.Padding(8, 8, 8, 0)
$form.Controls.Add($topPanel)

$btnStatus = New-Object System.Windows.Forms.Button
$btnStatus.Text = "Status"
$btnStatus.Width = 90
$topPanel.Controls.Add($btnStatus)

$btnHelp = New-Object System.Windows.Forms.Button
$btnHelp.Text = "Help"
$btnHelp.Width = 90
$topPanel.Controls.Add($btnHelp)

$btnGit = New-Object System.Windows.Forms.Button
$btnGit.Text = "Git pull"
$btnGit.Width = 90
$topPanel.Controls.Add($btnGit)

$btnApi = New-Object System.Windows.Forms.Button
$btnApi.Text = "Start API"
$btnApi.Width = 90
$topPanel.Controls.Add($btnApi)

$btnWorker = New-Object System.Windows.Forms.Button
$btnWorker.Text = "Start bot"
$btnWorker.Width = 90
$topPanel.Controls.Add($btnWorker)

$btnFolder = New-Object System.Windows.Forms.Button
$btnFolder.Text = "Folder"
$btnFolder.Width = 90
$topPanel.Controls.Add($btnFolder)

$script:OutputBox = New-Object System.Windows.Forms.TextBox
$script:OutputBox.Multiline = $true
$script:OutputBox.ReadOnly = $true
$script:OutputBox.ScrollBars = "Vertical"
$script:OutputBox.Dock = "Fill"
$script:OutputBox.Font = New-Object System.Drawing.Font("Consolas", 10)
$form.Controls.Add($script:OutputBox)

$bottomPanel = New-Object System.Windows.Forms.Panel
$bottomPanel.Dock = "Bottom"
$bottomPanel.Height = 180
$bottomPanel.Padding = New-Object System.Windows.Forms.Padding(8)
$form.Controls.Add($bottomPanel)

$inputBox = New-Object System.Windows.Forms.TextBox
$inputBox.Multiline = $true
$inputBox.ScrollBars = "Vertical"
$inputBox.Font = New-Object System.Drawing.Font("Consolas", 10)
$inputBox.Dock = "Fill"
$inputBox.Text = "status"
$bottomPanel.Controls.Add($inputBox)

$btnSend = New-Object System.Windows.Forms.Button
$btnSend.Text = "Send"
$btnSend.Dock = "Right"
$btnSend.Width = 110
$bottomPanel.Controls.Add($btnSend)

$btnSend.Add_Click({
    Invoke-OrchestratorCommand -Text $inputBox.Text
})
$btnStatus.Add_Click({
    Invoke-OrchestratorCommand -Text "status"
})
$btnHelp.Add_Click({
    Invoke-OrchestratorCommand -Text "help"
})
$btnGit.Add_Click({
    Invoke-GitSync
})
$btnApi.Add_Click({
    Start-OrchestratorApi
})
$btnWorker.Add_Click({
    Start-MainWorker
})
$btnFolder.Add_Click({
    Start-Process explorer.exe $ProjectPath
})

Add-Log "Project: $ProjectPath"
Add-Log "Branch:  $Branch"
Add-Log "Checks:  $ProjectPath\prompts\check_*"
Add-Log "Text box supports normal AI text if ORCHESTRATOR_AI_BASE_URL/API_KEY/MODEL are set in .env"

if (-not $SkipGitPull) {
    try {
        Invoke-GitSync
    } catch {
        Add-Log "Git sync failed: $($_.Exception.Message)"
    }
}

Start-OrchestratorApi
Invoke-OrchestratorCommand -Text "help"

[void]$form.ShowDialog()
