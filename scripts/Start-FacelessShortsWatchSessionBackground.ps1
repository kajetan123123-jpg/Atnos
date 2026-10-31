param(
    [string]$ConfigJson = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\learning\faceless-ai-shorts\watch_session_memory_guided_next.json",
    [switch]$Status,
    [switch]$PromptOnly,
    [switch]$NoRenderVideo
)

$ErrorActionPreference = "Stop"

$ApiRoot = "E:\Atnos\App\runtime-recovered\api"
$Python = Join-Path $ApiRoot ".venv\Scripts\python.exe"
$Worker = Join-Path $ApiRoot "scripts\run_faceless_watch_session_background_worker.py"
$RuntimeDir = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\runtime"
$LogsDir = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\logs"
$LearningDir = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\learning\faceless-ai-shorts"
$StatusPath = Join-Path $RuntimeDir "faceless_watch_background_status.json"
$LockPath = Join-Path $RuntimeDir "faceless_watch_background.lock"

function Read-JsonFile($Path) {
    if (-not (Test-Path $Path)) { return $null }
    try {
        return Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-ProcessAlive($PidValue) {
    if (-not $PidValue) { return $false }
    return [bool](Get-Process -Id ([int]$PidValue) -ErrorAction SilentlyContinue)
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
New-Item -ItemType Directory -Path $LearningDir -Force | Out-Null

if ($Status) {
    $payload = Read-JsonFile $StatusPath
    if ($payload -eq $null) {
        [pscustomobject]@{
            status = "not_started"
            status_json = $StatusPath
            lock_path = $LockPath
        } | ConvertTo-Json -Depth 5
        exit 0
    }
    $alive = Test-ProcessAlive $payload.pid
    [pscustomobject]@{
        status = $payload.status
        pid = $payload.pid
        process_alive = $alive
        output_dir = $payload.output_dir
        config_json = $payload.config_json
        stdout_log = $payload.stdout_log
        stderr_log = $payload.stderr_log
        phase = $payload.phase
        message = $payload.message
        updated_at = $payload.updated_at
        completed_at = $payload.completed_at
        heartbeat_count = $payload.heartbeat_count
        progress = $payload.progress
        result = $payload.result
        error = $payload.error
        status_json = $StatusPath
        lock_path = $LockPath
    } | ConvertTo-Json -Depth 8
    exit 0
}

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}
if (-not (Test-Path $Worker)) {
    throw "Background worker not found: $Worker"
}
if (-not (Test-Path $ConfigJson)) {
    throw "Config JSON not found: $ConfigJson"
}

$current = Read-JsonFile $StatusPath
if ($current -ne $null -and $current.status -eq "running" -and (Test-ProcessAlive $current.pid)) {
    [pscustomobject]@{
        status = "already_running"
        pid = $current.pid
        output_dir = $current.output_dir
        status_json = $StatusPath
    } | ConvertTo-Json -Depth 5
    exit 0
}

$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$OutputDir = Join-Path $LearningDir "$($Timestamp)_faceless_watch_background_session"
$StdoutLog = Join-Path $LogsDir "faceless_watch_background_$Timestamp.out.log"
$StderrLog = Join-Path $LogsDir "faceless_watch_background_$Timestamp.err.log"

$argList = @(
    "`"$Worker`"",
    "--config-json", "`"$ConfigJson`"",
    "--status-json", "`"$StatusPath`"",
    "--output-dir", "`"$OutputDir`"",
    "--stdout-log", "`"$StdoutLog`"",
    "--stderr-log", "`"$StderrLog`""
)
if ($PromptOnly) { $argList += "--prompt-only" }
if ($NoRenderVideo) { $argList += "--no-render-video" }

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList $argList `
    -WorkingDirectory $ApiRoot `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -WindowStyle Hidden `
    -PassThru

$lock = [pscustomobject]@{
    pid = $process.Id
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    config_json = (Resolve-Path $ConfigJson).Path
    output_dir = $OutputDir
    stdout_log = $StdoutLog
    stderr_log = $StderrLog
    status_json = $StatusPath
}
$lock | ConvertTo-Json -Depth 5 | Set-Content -Path $LockPath -Encoding UTF8

[pscustomobject]@{
    status = "started"
    pid = $process.Id
    output_dir = $OutputDir
    stdout_log = $StdoutLog
    stderr_log = $StderrLog
    status_json = $StatusPath
    lock_path = $LockPath
    status_command = "E:\Atnos\App\scripts\Start-FacelessShortsWatchSessionBackground.ps1 -Status"
} | ConvertTo-Json -Depth 5
