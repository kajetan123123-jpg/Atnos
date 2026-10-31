param(
    [string]$TaskName = "Atnos Faceless Shorts Watch Session",
    [string]$DailyAt = "03:30",
    [string]$ConfigJson = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\learning\faceless-ai-shorts\watch_session_config.json",
    [switch]$Register,
    [switch]$Unregister,
    [switch]$Preview
)

$ErrorActionPreference = "Stop"

$Runner = "E:\Atnos\App\scripts\Run-FacelessShortsWatchSession.ps1"
if (-not (Test-Path $Runner)) {
    throw "Watch session runner not found: $Runner"
}

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName"
    } else {
        Write-Host "Scheduled task does not exist: $TaskName"
    }
    exit 0
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -ConfigJson `"$ConfigJson`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

if ($Preview -or -not $Register) {
    [pscustomobject]@{
        TaskName = $TaskName
        DailyAt = $DailyAt
        Runner = $Runner
        ConfigJson = $ConfigJson
        Action = "powershell.exe $arguments"
        RegisterCommand = "E:\Atnos\App\scripts\Register-FacelessShortsWatchSessionTask.ps1 -Register"
        UnregisterCommand = "E:\Atnos\App\scripts\Register-FacelessShortsWatchSessionTask.ps1 -Unregister"
    } | Format-List
    exit 0
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Runs Atnos faceless Shorts watch/learn/recreate session. No public upload." `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName"
Write-Host "Daily at: $DailyAt"
Write-Host "Runner: $Runner"
Write-Host "Config: $ConfigJson"
