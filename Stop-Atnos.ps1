param(
    [int]$SkipPid = 0,
    [switch]$KeepShell
)

$ErrorActionPreference = "Continue"

$appRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
. (Join-Path $appRoot 'scripts\AtnosProcessSafety.ps1')
$runtimeLogRoot = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\runtime"
$stopLog = Join-Path $runtimeLogRoot "stop-atnos.log"
$launcherStateFile = Join-Path $runtimeLogRoot "fallback-launcher.state.json"
$managedShellExe = Join-Path $appRoot "Atnos Shell.exe"

New-Item -ItemType Directory -Force -Path $runtimeLogRoot | Out-Null

function Write-StopLog {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    Add-Content -Path $stopLog -Value "[$timestamp] $Message"
}

function Get-ProcessPathSafe {
    param([System.Diagnostics.Process]$Process)

    try {
        return $Process.Path
    }
    catch {
        try {
            return $Process.MainModule.FileName
        }
        catch {
            return $null
        }
    }
}

function Get-ProcessCommandLineSafe {
    param([int]$ProcessId)

    try {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if ($processInfo) {
            return [string]$processInfo.CommandLine
        }
    }
    catch {
    }

    return $null
}

function Test-PathOwnedByAtnos {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    try {
        $resolvedPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
        $resolvedRoot = [System.IO.Path]::GetFullPath($appRoot).TrimEnd('\')
        return $resolvedPath.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    }
    catch {
        return $false
    }
}

function Test-CommandLineOwnedByAtnos {
    param([string]$CommandLine)
    # Raw command text without cwd/executable/identity is insufficient.
    return $false
}

function Stop-ProcessByIdSafe {
    param([int]$ProcessId, [string]$Label)
    if (-not $ProcessId -or $ProcessId -eq $PID -or $ProcessId -eq $SkipPid) { return }
    try {
        [void](Stop-AtnosOwnedProcess -ProcessId $ProcessId -AllowApiStop)
        Write-StopLog "Stopped owned $Label PID=$ProcessId."
    } catch { Write-StopLog "Preserved $Label PID=${ProcessId}: $($_.Exception.Message)" }
}

function Get-ManagedServiceProcesses {
    param([string]$CommandPattern)
    $role = if ($CommandPattern -match 'serve_spa') { 'web' } else { 'api' }
    return @(Get-AtnosOwnedProcesses | Where-Object { $_.role -eq $role } | ForEach-Object {
        [pscustomobject]@{ ProcessId = $_.pid }
    })
}

function Stop-ManagedServiceProcesses {
    param(
        [string]$CommandPattern,
        [string]$Label
    )

    foreach ($process in (Get-ManagedServiceProcesses -CommandPattern $CommandPattern)) {
        Stop-ProcessByIdSafe -ProcessId ([int]$process.ProcessId) -Label $Label
    }
}

function Get-ListeningProcessId {
    param([int]$Port)

    try {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($listener) {
            return [int]$listener.OwningProcess
        }
    }
    catch {
    }

    return $null
}

function Stop-ListeningProcessIfAtnos {
    param(
        [int]$Port,
        [string]$Label
    )

    $processId = Get-ListeningProcessId -Port $Port
    if (-not $processId) {
        return
    }

    $process = $null
    try {
        $process = Get-Process -Id $processId -ErrorAction Stop
    }
    catch {
        return
    }

    $path = Get-ProcessPathSafe -Process $process
    $commandLine = Get-ProcessCommandLineSafe -ProcessId $processId
    if ((Test-PathOwnedByAtnos -Path $path) -or (Test-CommandLineOwnedByAtnos -CommandLine $commandLine)) {
        Stop-ProcessByIdSafe -ProcessId $processId -Label $Label
    }
    else {
        Write-StopLog "Skipping $Label listener on port $Port PID=$processId because it is not owned by ATNOS."
    }
}

function Stop-LauncherProcesses {
    $launchers = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Name -ieq "powershell.exe" -or $_.Name -ieq "pwsh.exe") -and
                $_.CommandLine -match "Launch-Atnos\.ps1"
            }
    )

    foreach ($launcher in $launchers) {
        Stop-ProcessByIdSafe -ProcessId ([int]$launcher.ProcessId) -Label "launcher"
    }
}

function Stop-RuntimeWatchdogProcesses {
    $watchdogs = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Name -ieq "powershell.exe" -or $_.Name -ieq "pwsh.exe") -and
                $_.CommandLine -match "AtnosRuntimeWatchdog\.ps1"
            }
    )

    foreach ($watchdog in $watchdogs) {
        Stop-ProcessByIdSafe -ProcessId ([int]$watchdog.ProcessId) -Label "runtime watchdog"
    }
}

function Stop-DesktopShellProcesses {
    if ($KeepShell) {
        return
    }

    $shellNames = @("Atnos Shell", "Atnos Desktop", "Atnos Portable", "Atnos")
    $shells = Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $path = Get-ProcessPathSafe -Process $_
            ($_.ProcessName -in $shellNames) -and (
                ($path -and $path -ieq $managedShellExe) -or
                (Test-PathOwnedByAtnos -Path $path)
            )
        }

    foreach ($shell in $shells) {
        Stop-ProcessByIdSafe -ProcessId $shell.Id -Label "desktop shell"
    }
}

Write-StopLog "Stop requested. SkipPid=$SkipPid KeepShell=$KeepShell"

Stop-LauncherProcesses
Stop-RuntimeWatchdogProcesses
Stop-ManagedServiceProcesses -CommandPattern "(uvicorn|run_atnos_api\.py).*atnos\.api\.main:create_app" -Label "API"
Stop-ManagedServiceProcesses -CommandPattern "serve_spa\.py" -Label "web shell"
Stop-ListeningProcessIfAtnos -Port 8000 -Label "API listener"
Stop-ListeningProcessIfAtnos -Port 3000 -Label "web listener"
Stop-DesktopShellProcesses

# Launcher owns its state; do not remove another process state file.
Write-StopLog "Stop completed."
