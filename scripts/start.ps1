# Start the local newsroom server and open the browser.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
Set-Location $Root
$python = Join-Path $Root ".venv\Scripts\python.exe"
Start-Process "http://127.0.0.1:$Port/"
& $python -m channel_press.cli serve --port $Port
