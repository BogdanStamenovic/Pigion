# PowerShell setup script: installs requirements and creates a .env with API_KEY and ABS_PATH
# Usage: .\deploy\setup.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

# Find Python (the official Windows installer commonly exposes only py.exe).
$pythonCommand = $null
foreach ($candidate in @("py", "python", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $pythonCommand = $candidate
        break
    }
}
if (-not $pythonCommand) {
    Write-Error "Python is required but not found. Please install Python 3."
    exit 1
}

# Check for pip
$pipCheck = & $pythonCommand -m pip --version 2>$null
if (-not $pipCheck) {
    & $pythonCommand -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip for Python is required but could not be installed with ensurepip."
        exit 1
    }
}

# Ask whether to create a virtualenv and install there (default: yes)
$createVenv = Read-Host "Create a virtualenv in .venv and install requirements there? [Y/n]"
if ([string]::IsNullOrWhiteSpace($createVenv) -or $createVenv -match '^[Yy]') {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating virtual environment in .venv..."
        & $pythonCommand -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
    } else {
        Write-Host "Using existing .venv virtual environment."
    }
    $installPython = (Resolve-Path ".venv\Scripts\python.exe").Path
} else {
    $installPython = $pythonCommand
}

# Install requirements if requirements.txt exists
if (Test-Path "requirements.txt") {
    Write-Host "Installing requirements from requirements.txt..."
    & $installPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    & $installPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "requirements installation failed" }
} else {
    Write-Host "No requirements.txt found in the current directory. Skipping pip install."
}

# Prompt for API key and write to .env
# Helper functions to write UTF-8 without BOM (Windows PowerShell writes BOM by default)
function Write-LinesUtf8NoBom {
    param([string]$Path, [string[]]$Lines)
    [System.IO.File]::WriteAllLines($Path, $Lines, (New-Object System.Text.UTF8Encoding($false)))
}

$apiKey = Read-Host "Enter API_KEY (leave empty to set blank)"
$absPath = (Get-Location).Path

# Prepare .env content
$envContent = @()
$envContent += "API_KEY=`"$apiKey`""
$envContent += "ABS_PATH=`"$absPath`""

if (Test-Path ".env") {
    $overwrite = Read-Host ".env already exists. Overwrite it? [y/N]"
        if ($overwrite -match '^[yY]$') {
        Write-LinesUtf8NoBom ".env" $envContent
    } else {
        # Update or add API_KEY
        $lines = Get-Content .env
        $foundApi = $false
        $foundAbs = $false
        $newLines = @()
        foreach ($line in $lines) {
            if ($line -match '^API_KEY=') {
                $newLines += "API_KEY=`"$apiKey`""
                $foundApi = $true
            } elseif ($line -match '^ABS_PATH=') {
                $newLines += "ABS_PATH=`"$absPath`""
                $foundAbs = $true
            } else {
                $newLines += $line
            }
        }
        if (-not $foundApi) { $newLines += "API_KEY=`"$apiKey`"" }
        if (-not $foundAbs) { $newLines += "ABS_PATH=`"$absPath`"" }
        Write-LinesUtf8NoBom ".env" $newLines
    }
} else {
    Write-LinesUtf8NoBom ".env" $envContent
}

Write-Host "Done. .env created/updated."
if ([string]::IsNullOrWhiteSpace($createVenv) -or $createVenv -match '^[Yy]') {
    Write-Host "To activate the virtualenv: .venv\\Scripts\\Activate.ps1"
}

$pythonExe = if (Test-Path ".venv\Scripts\python.exe") { (Resolve-Path ".venv\Scripts\python.exe").Path } else { (Get-Command $pythonCommand).Source }
$serviceDir = Join-Path $ProjectRoot ".pigion-services"
New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null

$webScript = @"
`$ErrorActionPreference = "Continue"
Set-Location '$($ProjectRoot.Replace("'", "''"))'
Get-Content '.env' | ForEach-Object { if (`$_ -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { [Environment]::SetEnvironmentVariable(`$Matches[1], `$Matches[2].Trim('"'), 'Process') } }
while (`$true) {
    & '$($pythonExe.Replace("'", "''"))' -m uvicorn server.server:app --host 0.0.0.0 --port 8000
    Start-Sleep -Seconds 5
}
"@
$orchestratorScript = @"
`$ErrorActionPreference = "Continue"
Set-Location '$($ProjectRoot.Replace("'", "''"))'
Get-Content '.env' | ForEach-Object { if (`$_ -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { [Environment]::SetEnvironmentVariable(`$Matches[1], `$Matches[2].Trim('"'), 'Process') } }
while (`$true) {
    & '$($pythonExe.Replace("'", "''"))' -m server.orchestrator_client --server 'http://127.0.0.1:8000' --python '$($pythonExe.Replace("'", "''"))' --project-root '$($ProjectRoot.Replace("'", "''"))'
    Start-Sleep -Seconds 5
}
"@
Write-LinesUtf8NoBom (Join-Path $serviceDir "web.ps1") @($webScript)
Write-LinesUtf8NoBom (Join-Path $serviceDir "orchestrator.ps1") @($orchestratorScript)

foreach ($task in @(
    @{ Name = "Pigion_Web"; Script = (Join-Path $serviceDir "web.ps1") },
    @{ Name = "Pigion_Orchestrator"; Script = (Join-Path $serviceDir "orchestrator.ps1") }
)) {
    $action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$($task.Script)`""
    schtasks.exe /Create /TN $task.Name /TR $action /SC ONLOGON /F | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Failed to register scheduled task $($task.Name)" }
    schtasks.exe /Run /TN $task.Name | Out-Host
}
Write-Host "Installed and started Scheduled Tasks: Pigion_Web, Pigion_Orchestrator"
