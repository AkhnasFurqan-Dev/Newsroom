param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$Probe,
    [switch]$Validate
)
# Read-only service health. Without -Probe, Telegram, Ollama and the database
# are reported as "configured" rather than "connected and tested".
$ErrorActionPreference = "Stop"
Set-Location $Root
$python = Join-Path $Root ".venv\Scripts\python.exe"
if ($Validate) {
    & $python -m channel_press.operations validate
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $python -m channel_press.operations health
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Probe) {
    & $python -m channel_press.operations probe
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
exit 0
