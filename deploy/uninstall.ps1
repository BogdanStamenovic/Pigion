$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

foreach ($taskName in @("Pigion_Orchestrator", "Pigion_Web")) {
    Write-Host "Stopping and removing $taskName..."
    schtasks.exe /End /TN $taskName 2>$null | Out-Null
    schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
}

$serviceDir = Join-Path $ProjectRoot ".pigion-services"
if (Test-Path $serviceDir) {
    Remove-Item -Recurse -Force $serviceDir
}
Write-Host "Uninstalled Pigion webserver/orchestrator Scheduled Tasks. Repository data was kept."
