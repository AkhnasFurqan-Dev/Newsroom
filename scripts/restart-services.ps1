param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$ProbeTelegram,
    [int]$TimeoutSeconds = 120
)
# Controlled restart in the documented order: configuration/database, local
# model, web app, then (only when asked) the managed Telegram connection.
$ErrorActionPreference = "Stop"
Set-Location $Root
$python = Join-Path $Root ".venv\Scripts\python.exe"
$logs = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$url = (& $python -c "from channel_press.config import settings; host = settings.host if settings.host not in ('0.0.0.0', '') else '127.0.0.1'; print(f'http://{host}:{settings.port}')" | Out-String).Trim()

Write-Output "1/6 Validate configuration and database access"
& $python -m channel_press.operations validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "2/6 Check whether an unmanaged web server is already listening"
try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "$url/login" | Out-Null
    Write-Error "A web server already answers at $url but is not managed by Newsroom. Stop that process yourself, then re-run this script."
    exit 1
} catch {
    # No answer: expected, so the restart owns the port.
}

Write-Output "3/6 Graceful stop of Newsroom services"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\stop-services.ps1") -Root $Root -TimeoutSeconds $TimeoutSeconds
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m channel_press.operations resume
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "4/6 Verify the local model and service health"
& $python -m channel_press.operations health
# Detached start without inheriting this console's pipes, so a caller that
# captures this script's output still sees it exit cleanly.
& $python -m channel_press.operations start-web
if ($LASTEXITCODE -ne 0) { Write-Error "Could not start the web app."; exit 1 }

Write-Output "5/6 Wait for the web app to answer at $url"
$until = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
while ((Get-Date) -lt $until) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "$url/login" | Out-Null
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    Write-Error "Web app did not answer within $TimeoutSeconds seconds. Inspect the newest service-*.stderr in $logs."
    exit 1
}

Write-Output "6/6 Worker ownership check"
$owners = (& $python -m channel_press.operations owners | Out-String) | ConvertFrom-Json
$workers = @()
if ($owners -and $owners.services) { $workers = @($owners.services | Where-Object { $_.kind -eq "worker" }) }
if ($workers.Count -gt 0) {
    Write-Error "$($workers.Count) managed worker(s) still own the queue after restart. Wait for them or inspect Jobs."
    exit 1
}
Write-Output "Web server healthy at $url. No competing worker owns the queue. Job checkpoints are intact."

if ($ProbeTelegram) {
    Write-Output "Supervised Telegram preflight (read-only, bounded):"
    & $python -m channel_press.operations probe
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Output "Telegram was not contacted. Use Check Connections in the dashboard or re-run with -ProbeTelegram."
}
Write-Output "Paused jobs are resumed explicitly from Jobs; automatic publication remains off."
