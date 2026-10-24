# One short-lived worker pass. Safe to schedule every 5–15 minutes.
# The worker module itself holds the single OS lease, so a second pass exits
# with a "worker is already running" message instead of competing for jobs.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "",
    [string]$WorkerArgs = "-m channel_press.cli worker --once"
)

$ErrorActionPreference = "Continue"
Set-Location $Root
if (-not $Python) { $Python = Join-Path $Root ".venv\Scripts\python.exe" }
$logDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("worker-" + (Get-Date -Format "yyyyMMdd") + ".log")

"[{0}] worker pass start" -f (Get-Date -Format "s") | Out-File -FilePath $log -Append -Encoding utf8
$stamp = [guid]::NewGuid().ToString("N")
$stderr = Join-Path $logDir "worker-err-$stamp.log"
# Keep stdout attached so stage updates are visible while retaining a durable
# stderr copy for startup failures and diagnostics.
$process = Start-Process -FilePath $Python -ArgumentList $WorkerArgs `
    -WorkingDirectory $Root -Wait -PassThru -NoNewWindow `
    -RedirectStandardError $stderr
$code = $process.ExitCode
"[{0}] worker pass exit {1}" -f (Get-Date -Format "s"), $code | Out-File -FilePath $log -Append -Encoding utf8
exit $code
