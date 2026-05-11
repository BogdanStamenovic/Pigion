# PowerShell setup script: installs requirements and creates a .env with API_KEY and ABS_PATH
# Usage: .\setup.ps1

# Check for Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python is required but not found. Please install Python 3."
    exit 1
}

# Check for pip
$pipCheck = python -m pip --version 2>$null
if (-not $pipCheck) {
    Write-Error "pip for python is required but not found. Try: python -m ensurepip --upgrade or install pip."
    exit 1
}

# Ask whether to create a virtualenv and install there (default: yes)
$createVenv = Read-Host "Create a virtualenv in .venv and install requirements there? [Y/n]"
if ([string]::IsNullOrWhiteSpace($createVenv) -or $createVenv -match '^[Yy]') {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating virtual environment in .venv..."
        python -m venv .venv
    } else {
        Write-Host "Using existing .venv virtual environment."
    }
    $venvActivate = ".venv/Scripts/Activate.ps1"
    if (Test-Path $venvActivate) {
        & $venvActivate
    }
    $pipCmd = "pip"
} else {
    $pipCmd = "python -m pip"
}

# Install requirements if requirements.txt exists
if (Test-Path "requirements.txt") {
    Write-Host "Installing requirements from requirements.txt..."
    if ($pipCmd -eq "pip") {
        pip install --upgrade pip
        pip install -r requirements.txt
    } else {
        python -m pip install --upgrade pip
        python -m pip install -r requirements.txt
    }
} else {
    Write-Host "No requirements.txt found in the current directory. Skipping pip install."
}

# Prompt for API key and write to .env
# Helper functions to write UTF-8 without BOM (Windows PowerShell writes BOM by default)
function Write-TextUtf8NoBom {
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}
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

# Ask for device type
$deviceType = Read-Host "Is this device a Pi or a Laptop? [pi/laptop]"
$deviceType = $deviceType.ToLower()

# Auto-detect OS and TERMINAL for this device
if ($deviceType -eq "pi" -or $deviceType -eq "laptop") {
    try {
        $deviceOs = (Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption)
    } catch {
        $deviceOs = $env:OS
    }
    if ($deviceOs -and $deviceOs -match 'Windows') {
        $deviceTerminal = 'powershell'
    } else {
        $deviceTerminal = $env:TERM
        if (-not $deviceTerminal) { $deviceTerminal = $env:COMSPEC }
        if (-not $deviceTerminal) { $deviceTerminal = $env:SHELL }
        if (-not $deviceTerminal) { $deviceTerminal = 'bash' }
    }
}

# Populate current device's enving.txt automatically
$currentEnvPath = if ($deviceType -eq "pi") { "pi_exp/enving.txt" } else { "laptop_exp/enving.txt" }
$currentEnvDir = Split-Path $currentEnvPath -Parent
if (-not (Test-Path $currentEnvDir)) { New-Item -ItemType Directory -Path $currentEnvDir | Out-Null }
Write-TextUtf8NoBom $currentEnvPath "OS: $deviceOs`nTERMINAL: $deviceTerminal"
Write-Host "Populated $currentEnvPath with current device specs."

# Ask for other device's enving.txt fields
$otherLabel = if ($deviceType -eq "pi") { "laptop" } else { "pi" }
$otherEnvPath = if ($deviceType -eq "pi") { "laptop_exp/enving.txt" } else { "pi_exp/enving.txt" }

$otherOs = Read-Host "Enter $otherLabel OS (e.g. Windows 10):"
$otherTerminal = Read-Host "Enter $otherLabel TERMINAL (e.g. powershell):"

$otherEnvDir = Split-Path $otherEnvPath -Parent
if (-not (Test-Path $otherEnvDir)) { New-Item -ItemType Directory -Path $otherEnvDir | Out-Null }
Write-TextUtf8NoBom $otherEnvPath "OS: $otherOs`nTERMINAL: $otherTerminal"
Write-Host "Populated $otherEnvPath with $otherLabel specs."
