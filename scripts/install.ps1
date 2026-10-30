# Channel Press — local Windows installation
# Usage:  powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Channel Press installation" -ForegroundColor Cyan
Write-Host "Workspace: $root"

if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
    Write-Host "Creating Python virtual environment (.venv)..."
    $python = (Get-Command py -ErrorAction SilentlyContinue)
    if ($python) {
        py -3.12 -m venv .venv
    } else {
        python -m venv .venv
    }
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[test,video,desktop,build]"

if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "Created .env from .env.example (edit settings there)."
}

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) {
    Write-Warning "Google Chrome was not found. The print/PDF pipeline launches the installed Chrome channel. Install Chrome or set CHANNEL_PRESS_* accordingly."
} else {
    Write-Host "Chrome found: $chrome"
}

& $venvPython -m channel_press.cli init-db
if ($LASTEXITCODE -ne 0) { throw "Database initialisation failed." }

Write-Host ""
Write-Host "Installation finished." -ForegroundColor Green
Write-Host "1. Create the owner account:  .\.venv\Scripts\python.exe -m channel_press.cli setup --demo-channel"
Write-Host "2. Build the demo edition:    .\.venv\Scripts\python.exe -m channel_press.cli demo --posts sample --reset"
Write-Host "3. Open the desktop preview:  .\.venv\Scripts\python.exe -m channel_press.desktop"
Write-Host "4. Optional worker task:      powershell -ExecutionPolicy Bypass -File .\scripts\install-task.ps1"
Write-Host ""
Write-Host "The baseline is fully local: no Telegram credentials, cloud account or paid model is required for the fixture demo."
