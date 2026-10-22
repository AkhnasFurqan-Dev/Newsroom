# Register a per-user Windows Task Scheduler job that runs one worker pass
# every 15 minutes. No permanently active process or model is required.
param(
    [string]$TaskName = "ChannelPressWorker",
    [int]$EveryMinutes = 15
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$worker = Join-Path $root "scripts\run-worker.ps1"
if (-not (Test-Path $worker)) { throw "run-worker.ps1 not found." }

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$worker`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered scheduled task '$TaskName' (every $EveryMinutes minutes)."
Write-Host "Run once now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Check status:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "Logs:          $root\data\logs"
