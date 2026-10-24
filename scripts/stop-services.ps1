param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$TimeoutSeconds = 300
)
# Graceful stop only: ask Channel Press services to pause at a safe checkpoint,
# wait for them to exit, and never kill unrelated processes.
$ErrorActionPreference = "Stop"
Set-Location $Root
$python = Join-Path $Root ".venv\Scripts\python.exe"

Write-Output "Inventory before shutdown (no credentials are printed):"
& $python -m channel_press.operations health
if ($LASTEXITCODE -ne 0) { Write-Error "Health inventory failed; refusing to continue."; exit 1 }

& $python -m channel_press.operations stop
if ($LASTEXITCODE -ne 0) { Write-Error "Stop request failed."; exit 1 }

$until = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $until) {
    $raw = (& $python -m channel_press.operations owners | Out-String)
    $owners = $raw | ConvertFrom-Json
    $count = 0
    if ($owners -and $owners.PSObject.Properties.Name -contains "count") { $count = [int]$owners.count }
    if ($count -eq 0) {
        Write-Output "Channel Press managed services stopped cleanly. Databases, sessions and jobs were preserved."
        exit 0
    }
    Write-Output ("Waiting for {0} managed service(s) to finish a bounded operation..." -f $count)
    Start-Sleep -Seconds 1
}
Write-Error("Services did not stop within {0}s. The stop request remains active and no process was killed. Check data\logs, then stop the specific owned PID if it is truly unresponsive." -f $TimeoutSeconds)
exit 1
