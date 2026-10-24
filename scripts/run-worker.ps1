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
$stdout = Join-Path $logDir "worker-$stamp.stdout"
$stderr = Join-Path $logDir "worker-$stamp.stderr"
# Keep the worker attached to its console so stage updates are visible while
# the job runs. The Python logger still writes the durable JSON log.
$process = Start-Process -FilePath $Python -ArgumentList $WorkerArgs -WorkingDirectory $Root -Wait -PassThru -NoNewWindow
$code = $process.ExitCode
Remove-Item $stdout, $stderr -ErrorAction SilentlyContinue
"[{0}] worker pass exit {1}" -f (Get-Date -Format "s"), $code | Out-File -FilePath $log -Append -Encoding utf8
exit $code
