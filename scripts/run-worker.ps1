# One short-lived worker pass. Safe to schedule every 5–15 minutes.
# A file lock guarantees only one worker processes jobs at a time.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Continue"
Set-Location $Root
$python = Join-Path $Root ".venv\Scripts\python.exe"
$logDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("worker-" + (Get-Date -Format "yyyyMMdd") + ".log")

"[{0}] worker pass start" -f (Get-Date -Format "s") | Out-File -FilePath $log -Append -Encoding utf8
& $python -m channel_press.cli worker --once *>> $log
$code = $LASTEXITCODE
"[{0}] worker pass exit {1}" -f (Get-Date -Format "s"), $code | Out-File -FilePath $log -Append -Encoding utf8
exit $code
