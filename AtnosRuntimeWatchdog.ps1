param(
    [ValidateRange(10, 300)]
    [int]$IntervalSeconds = 30,
    [switch]$Once
)

$ErrorActionPreference = "Continue"

$appRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$runtimeLogRoot = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\runtime"
$watchdogLog = Join-Path $runtimeLogRoot "runtime-watchdog.log"
$watchdogStateFile = Join-Path $runtimeLogRoot "runtime-watchdog.state.json"
$launcherScript = Join-Path $appRoot "Launch-Atnos.ps1"
$minimumRecoveryIntervalSeconds = 120

New-Item -ItemType Directory -Force -Path $runtimeLogRoot | Out-Null

function Write-WatchdogLog {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    Add-Content -LiteralPath $watchdogLog -Value "[$timestamp] $Message"
}

function Write-WatchdogState {
    param(
        [string]$Status,
        [bool]$ApiReady,
        [bool]$WebReady,
        [datetime]$LastRecoveryAt
    )

    $payload = [ordered]@{
        pid = $PID
        status = $Status
        heartbeat_at = (Get-Date).ToUniversalTime().ToString("o")
        api_ready = $ApiReady
        web_ready = $WebReady
        last_recovery_at = if ($LastRecoveryAt -eq [datetime]::MinValue) { $null } else { $LastRecoveryAt.ToUniversalTime().ToString("o") }
    }
    $payload | ConvertTo-Json -Compress | Set-Content -LiteralPath $watchdogStateFile -Encoding UTF8
}

function Test-LocalHttpEndpoint {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Test-AtnosRuntimeReady {
    $apiReady = Test-LocalHttpEndpoint -Url "http://127.0.0.1:8000/health/ready"
    $webReady = Test-LocalHttpEndpoint -Url "http://127.0.0.1:3000/"
    return [pscustomobject]@{
        ApiReady = $apiReady
        WebReady = $webReady
        Ready = $apiReady -and $webReady
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
        return $null
    }
    return $null
}

function Test-ProcessExists {
    param([int]$ProcessId)

    if (-not $ProcessId) {
        return $false
    }
    try {
        Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-ApiStartupPhase {
    $statusPath = Join-Path $runtimeLogRoot "api-startup-status.json"
    if (-not (Test-Path -LiteralPath $statusPath)) {
        return $null
    }
    try {
        $status = Get-Content -LiteralPath $statusPath -Raw -ErrorAction Stop | ConvertFrom-Json
        return [string]$status.startup_phase
    }
    catch {
        return $null
    }
}

function Clear-StuckApiListener {
    # Readiness failure and startup_phase never authorize a process kill.
    return $false
}

function Start-AtnosServiceRecovery {
    if (-not (Test-Path $launcherScript)) {
        Write-WatchdogLog "Recovery skipped because launcher is missing: $launcherScript"
        return $false
    }


    try {
        Start-Process `
            -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $launcherScript), "-NoDesktopShell", "-NoErrorDialog", "-ServicesOnly" `
            -WorkingDirectory $appRoot `
            -WindowStyle Hidden | Out-Null
        Write-WatchdogLog "Started controlled API/web recovery without opening a desktop window."
        return $true
    }
    catch {
        Write-WatchdogLog "Recovery start failed: $($_.Exception.Message)"
        return $false
    }
}

# This watchdog only restores local API and SPA processes. It never launches GPU
# work, signs in, approves OAuth, purchases, uploads, or trades.
$watchdogMutex = New-Object System.Threading.Mutex($false, "Global\AtnosRuntimeWatchdog")
$mutexAcquired = $false
$lastRecoveryAt = [datetime]::MinValue

try {
    try {
        $mutexAcquired = $watchdogMutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $mutexAcquired = $true
    }

    if (-not $mutexAcquired) {
        exit 0
    }

    Write-WatchdogLog "Watchdog started. interval_seconds=$IntervalSeconds"
    do {
        $status = Test-AtnosRuntimeReady
        Write-WatchdogState -Status $(if ($status.Ready) { "ready" } else { "unavailable" }) -ApiReady $status.ApiReady -WebReady $status.WebReady -LastRecoveryAt $lastRecoveryAt
        if (-not $status.Ready) {
            $elapsed = ((Get-Date) - $lastRecoveryAt).TotalSeconds
            if ($elapsed -ge $minimumRecoveryIntervalSeconds) {
                Write-WatchdogLog "Runtime unavailable. api_ready=$($status.ApiReady) web_ready=$($status.WebReady)"
                if (Start-AtnosServiceRecovery) {
                    $lastRecoveryAt = Get-Date
                }
            }
        }

        if (-not $Once) {
            Start-Sleep -Seconds $IntervalSeconds
        }
    } while (-not $Once)
}
finally {
    if ($mutexAcquired) { Remove-Item -LiteralPath $watchdogStateFile -Force -ErrorAction SilentlyContinue }
    if ($mutexAcquired) {
        $watchdogMutex.ReleaseMutex()
    }
    $watchdogMutex.Dispose()
}
