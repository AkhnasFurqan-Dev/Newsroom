# Start all local newsroom components, recover queued work, and open Chrome.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 0,
    [switch]$ProbeTelegram
)

$ErrorActionPreference = "Stop"
Set-Location $Root
$python = Join-Path $Root ".venv\Scripts\python.exe"

if ($Port -gt 0) {
    $env:CHANNEL_PRESS_PORT = "$Port"
}

$restart = Join-Path $Root "scripts\restart-services.ps1"
$restartArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $restart, "-Root", $Root)
if ($ProbeTelegram) { $restartArgs += "-ProbeTelegram" }
& powershell.exe @restartArgs
if ($LASTEXITCODE -ne 0) {
    throw "Channel Press could not start. Inspect data\logs\service-*.stderr."
}

$url = (& $python -c "from channel_press.config import settings; host = settings.host if settings.host not in ('0.0.0.0', '') else '127.0.0.1'; print(f'http://{host}:{settings.port}/')" | Out-String).Trim()
$worker = Join-Path $Root "scripts\run-worker.ps1"
Start-Process powershell.exe -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $worker, "-Root", $Root
) -WorkingDirectory $Root -WindowStyle Hidden

$chrome = @(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList $url
} else {
    Start-Process $url
}
Write-Output "Channel Press is ready at $url"
Write-Output "A recovery worker pass is running in the background."
