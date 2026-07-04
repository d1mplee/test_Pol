# Registers Windows scheduled task: paper tick every 30 min (no window, log to data/tick.log).
# Run:    powershell -ExecutionPolicy Bypass -File C:\Users\davae\polymarket-research\scripts\register_task.ps1
# Remove: Unregister-ScheduledTask -TaskName PolymarketPaperTick -Confirm:$false

$pyw = "C:\Users\davae\AppData\Local\Programs\Python\Python311\pythonw.exe"
$script = "C:\Users\davae\polymarket-research\scripts\tick_task.py"

$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$script`"" `
    -WorkingDirectory "C:\Users\davae\polymarket-research"

# Task Scheduler rejects [TimeSpan]::MaxValue, so "forever" = 10 years
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 25)

Register-ScheduledTask -TaskName "PolymarketPaperTick" -Action $action `
    -Trigger $trigger -Settings $settings -Force -ErrorAction Stop

if (Get-ScheduledTask -TaskName "PolymarketPaperTick" -ErrorAction SilentlyContinue) {
    Write-Host "OK: task PolymarketPaperTick registered, tick every 30 minutes." -ForegroundColor Green
    Write-Host "Log: C:\Users\davae\polymarket-research\data\tick.log"
} else {
    Write-Host "FAILED: task not registered, see error above." -ForegroundColor Red
}
