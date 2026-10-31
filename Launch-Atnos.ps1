param(
    [switch]$NoDesktopShell,
    [switch]$NoErrorDialog,
    [switch]$ServicesOnly,
    [switch]$ModelsOnly,
    [switch]$StartModels,
    [switch]$RestartBackend
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Security

$appRoot = (Resolve-Path $PSScriptRoot).Path
. (Join-Path $appRoot 'scripts\AtnosProcessSafety.ps1')
. (Join-Path $appRoot 'scripts\AtnosOllamaRuntime.ps1')
if ($ServicesOnly) { $NoDesktopShell = $true }
if ($ServicesOnly -and $StartModels) { throw 'ServicesOnly cannot start models' }
if ($ModelsOnly -and ($ServicesOnly -or $RestartBackend)) { throw 'ModelsOnly cannot start or restart application services' }
if ($ModelsOnly) { $NoDesktopShell = $true }
$dataRoot = if ($env:ATNOS_STATE_DIR) {
    [IO.Path]::GetFullPath($env:ATNOS_STATE_DIR)
} elseif ($env:DATA_DIR) {
    [IO.Path]::GetFullPath($env:DATA_DIR)
} else {
    "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data"
}
$configRoot = if ($env:ATNOS_ENV_FILE) {
    Split-Path -Parent ([IO.Path]::GetFullPath($env:ATNOS_ENV_FILE))
} else {
    Join-Path (Split-Path -Parent $dataRoot) "config"
}
$runtimeLogRoot = Join-Path (Split-Path -Parent $dataRoot) "runtime"
$quarantineRoot = Join-Path (Split-Path -Parent $dataRoot) "quarantine"
$runtimeEnvFile = Join-Path $configRoot ".env"
foreach ($requiredEPath in @($appRoot, $dataRoot, $configRoot, $runtimeLogRoot)) {
    if ([IO.Path]::GetPathRoot($requiredEPath) -ine 'E:\') { throw "Atnos paths must remain on E: ($requiredEPath)" }
}
$atnosDataRoot = $dataRoot
$toolCacheRoot = Join-Path $atnosDataRoot "tool-caches"
$atnosTempDir = Join-Path $atnosDataRoot "temp"
$atnosCacheDir = Join-Path $atnosDataRoot "cache"
$pipCacheDir = Join-Path $toolCacheRoot "pip-cache"
$pipConfigFile = Join-Path $appRoot "pip.ini"
$npmCacheDir = Join-Path $toolCacheRoot "npm-cache"
$xdgCacheDir = Join-Path $toolCacheRoot "xdg-cache"
$hfCacheDir = Join-Path $toolCacheRoot "huggingface"
$transformersCacheDir = Join-Path $hfCacheDir "transformers"
$torchCacheDir = Join-Path $toolCacheRoot "torch"
$uvCacheDir = Join-Path $toolCacheRoot "uv"
$ollamaModelsDir = "E:\Atnos\Models\ollama"
$gitRoot = "E:\Atnos\Tools\Git"
$gitCmdDir = Join-Path $gitRoot "cmd"
$gitBinDir = Join-Path $gitRoot "bin"
$gitExecutable = Join-Path $gitCmdDir "git.exe"
$nodeBinDir = "E:\Atnos\Storage\ToolCaches\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$browserOperatorUserDataDir = Join-Path $atnosDataRoot "browser_profiles\default"
$browserOperatorDownloadsDir = Join-Path $atnosDataRoot "browser_downloads"
$browserOperatorScreenshotsDir = Join-Path $atnosDataRoot "browser_screenshots"
$browserOperatorLogPath = Join-Path $atnosDataRoot "logs\browser_actions.jsonl"
$browserProfileDir = Join-Path $dataRoot "browser-profile"
$browserDownloadsDir = Join-Path $dataRoot "browser-downloads"
$browserTraceDir = Join-Path $dataRoot "browser-traces"
$playwrightBrowsersDir = Join-Path $toolCacheRoot "ms-playwright"
$workspaceStagingDir = Join-Path $dataRoot "workspace-staging"
$webView2ProfileDir = Join-Path $dataRoot "webview2-profile"
$managedShellSource = Join-Path $appRoot "desktop-shell\AtnosShell.cs"
$managedShellExe = Join-Path $appRoot "Atnos Shell.exe"
$managedShellCompiler = Join-Path $env:SystemRoot "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$webView2SdkDir = "E:\Atnos\Tools\WebView2SDK"
$webView2Assemblies = @(
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.WinForms.dll",
    "WebView2Loader.dll"
)
$desktopShell = Join-Path $appRoot "Atnos Desktop.exe"
$portableShell = Join-Path $appRoot "Atnos Portable.exe"

$preferredApiPython = Join-Path $appRoot "runtime-recovered\api\.venv\Scripts\python.exe"
$legacyApiPython = Join-Path $appRoot "runtime-recovered\api-venv\Scripts\python.exe"
$apiPython = if (Test-Path $preferredApiPython) { $preferredApiPython } else { $legacyApiPython }
$staticPython = $apiPython
$apiRoot = Join-Path $appRoot "runtime-recovered\api"
$spaServerScript = Join-Path $appRoot "frontend\serve_spa.py"
$webRoot = Join-Path $appRoot "desktop-web"
$frontendRoot = Join-Path $appRoot "frontend"
$frontendDistRoot = Join-Path $frontendRoot "dist"
$frontendDeployScript = Join-Path $appRoot "scripts\deploy_frontend_to_desktop_web.ps1"

$apiOutLog = Join-Path $runtimeLogRoot "fallback-api.out.log"
$apiErrLog = Join-Path $runtimeLogRoot "fallback-api.err.log"
$apiStartupStatusFile = Join-Path $runtimeLogRoot "api-startup-status.json"
$apiStartupEventsFile = Join-Path $runtimeLogRoot "api-startup-events.jsonl"
$webOutLog = Join-Path $runtimeLogRoot "fallback-web.out.log"
$webErrLog = Join-Path $runtimeLogRoot "fallback-web.err.log"
$launcherLog = Join-Path $runtimeLogRoot "fallback-launcher.log"
$launcherStateFile = Join-Path $runtimeLogRoot "fallback-launcher.state.json"
$localCapabilitySessionFile = Join-Path $runtimeLogRoot "local-capability-session.json"

New-Item -ItemType Directory -Force -Path $runtimeLogRoot | Out-Null
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
New-Item -ItemType Directory -Force -Path $quarantineRoot | Out-Null
New-Item -ItemType Directory -Force -Path $configRoot | Out-Null
New-Item -ItemType Directory -Force -Path $atnosDataRoot | Out-Null
New-Item -ItemType Directory -Force -Path $toolCacheRoot | Out-Null
New-Item -ItemType Directory -Force -Path $atnosTempDir | Out-Null
New-Item -ItemType Directory -Force -Path $atnosCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $pipCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $npmCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $xdgCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $hfCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $transformersCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $torchCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $uvCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $ollamaModelsDir | Out-Null
New-Item -ItemType Directory -Force -Path $browserOperatorUserDataDir | Out-Null
New-Item -ItemType Directory -Force -Path $browserOperatorDownloadsDir | Out-Null
New-Item -ItemType Directory -Force -Path $browserOperatorScreenshotsDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $browserOperatorLogPath) | Out-Null
New-Item -ItemType Directory -Force -Path $browserProfileDir | Out-Null
New-Item -ItemType Directory -Force -Path $browserDownloadsDir | Out-Null
New-Item -ItemType Directory -Force -Path $browserTraceDir | Out-Null
New-Item -ItemType Directory -Force -Path $playwrightBrowsersDir | Out-Null
New-Item -ItemType Directory -Force -Path $workspaceStagingDir | Out-Null
New-Item -ItemType Directory -Force -Path $webView2ProfileDir | Out-Null

$uploadsDir = Join-Path $dataRoot "uploads"
$accountSecretVaultDir = Join-Path $dataRoot "account-secrets"
$sqliteFile = Join-Path $dataRoot "atnos.db"
$env:APP_ROOT = $appRoot
$env:DATA_DIR = $dataRoot
$env:ATNOS_ENV_FILE = $runtimeEnvFile
$env:ATNOS_ROOT = $appRoot
$env:ATNOS_DATA_DIR = $atnosDataRoot
$env:ATNOS_STATE_DIR = $atnosDataRoot
$env:ATNOS_CACHE_DIR = $atnosCacheDir
$env:ATNOS_TEMP_DIR = $atnosTempDir
$env:TEMP = $atnosTempDir
$env:TMP = $atnosTempDir
$env:PIP_CACHE_DIR = $pipCacheDir
$env:PIP_CONFIG_FILE = $pipConfigFile
$env:npm_config_cache = $npmCacheDir
$env:XDG_CACHE_HOME = $xdgCacheDir
$env:HF_HOME = $hfCacheDir
$env:TRANSFORMERS_CACHE = $transformersCacheDir
$env:TORCH_HOME = $torchCacheDir
$env:UV_CACHE_DIR = $uvCacheDir
$env:OLLAMA_MODELS = $ollamaModelsDir
$ollamaEndpoint = Get-AtnosOllamaEndpoint -EnvFile $runtimeEnvFile
$env:OLLAMA_HOST = $ollamaEndpoint.Host
$env:OLLAMA_BASE_URL = $ollamaEndpoint.BaseUrl
# Keep one model warm for chat latency; MAX_LOADED=1 protects 16GB VRAM when shorts/vision swap in.
$env:OLLAMA_KEEP_ALIVE = "15m"
$env:OLLAMA_NUM_PARALLEL = "1"
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_VULKAN = "1"
# Preserve configured role names across desktop/service startup. Frozen orders
# still choose their own production models independently of this environment.
$ollamaRoles = Get-AtnosOllamaModelRoles -EnvFile $runtimeEnvFile
foreach ($roleEntry in $ollamaRoles.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($roleEntry.Key, $roleEntry.Value, 'Process')
}
$env:UPLOADS_DIR = $uploadsDir
$env:ACCOUNT_SECRET_VAULT_DIR = $accountSecretVaultDir
$env:SQLITE_FILE = $sqliteFile
$env:RUNTIME_DIR = $runtimeLogRoot
$env:QUARANTINE_DIR = $quarantineRoot
$env:BROWSER_PROFILE_DIR = $browserProfileDir
$env:BROWSER_DOWNLOADS_DIR = $browserDownloadsDir
$env:BROWSER_TRACE_DIR = $browserTraceDir
$env:PLAYWRIGHT_BROWSERS_PATH = $playwrightBrowsersDir
$env:WORKSPACE_STAGING_DIR = $workspaceStagingDir
$env:BROWSER_USER_DATA_DIR = $browserOperatorUserDataDir
$env:BROWSER_DOWNLOAD_DIR = $browserOperatorDownloadsDir
$env:BROWSER_SCREENSHOT_DIR = $browserOperatorScreenshotsDir
$env:BROWSER_LOG_PATH = $browserOperatorLogPath
$env:ATNOS_LOCAL_CAPABILITY_SESSION_FILE = $localCapabilitySessionFile
if (Test-Path -LiteralPath $gitExecutable) {
    $env:ATNOS_GIT_EXECUTABLE = $gitExecutable
    $env:Path = "$gitCmdDir;$gitBinDir;$env:Path"
}
if (Test-Path -LiteralPath (Join-Path $nodeBinDir "node.exe")) {
    $env:Path = "$nodeBinDir;$env:Path"
}

$launcherMutex = New-Object System.Threading.Mutex($false, "Local\AtnosLauncherV2")
$launcherMutexAcquired = $false
$launcherStartedAt = Get-Date
$launcherStatePhase = "created"

function Write-LauncherLog {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    Add-Content -Path $launcherLog -Value "[$timestamp] $Message"
}

function ConvertFrom-ProtectedLocalCapability {
    param([string]$ProtectedValue)

    $protectedBytes = [Convert]::FromBase64String($ProtectedValue)
    $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $protectedBytes,
        $null,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Text.Encoding]::UTF8.GetString($plainBytes)
}

function ConvertTo-ProtectedLocalCapability {
    param([string]$PlainValue)

    $plainBytes = [Text.Encoding]::UTF8.GetBytes($PlainValue)
    $protectedBytes = [Security.Cryptography.ProtectedData]::Protect(
        $plainBytes,
        $null,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Convert]::ToBase64String($protectedBytes)
}

function New-LocalCapabilityToken {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Initialize-LocalCapabilitySession {
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $token = $null
    $expiresAt = 0

    if (Test-Path -LiteralPath $localCapabilitySessionFile) {
        try {
            $saved = Get-Content -LiteralPath $localCapabilitySessionFile -Raw | ConvertFrom-Json
            $candidateExpiry = [long]$saved.expires_at
            if ($candidateExpiry -gt ($now + 60) -and $saved.protected_token) {
                $token = ConvertFrom-ProtectedLocalCapability -ProtectedValue ([string]$saved.protected_token)
                $expiresAt = $candidateExpiry
            }
        }
        catch {
            $token = $null
            $expiresAt = 0
        }
    }

    if (-not $token) {
        $token = New-LocalCapabilityToken
        $expiresAt = $now + (8 * 60 * 60)
        $protectedToken = ConvertTo-ProtectedLocalCapability -PlainValue $token
        $payload = @{
            protected_token = $protectedToken
            expires_at = $expiresAt
            created_at = [DateTimeOffset]::UtcNow.ToString("o")
        } | ConvertTo-Json
        # The launcher mutex already serializes writers. FileMode.Create replaces an
        # existing session in one operation and avoids the delete/move race that made
        # rapid double-clicks fail with "file already exists".
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $sessionStream = New-Object System.IO.FileStream(
            $localCapabilitySessionFile,
            [System.IO.FileMode]::Create,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        try {
            $sessionBytes = $utf8.GetBytes($payload)
            $sessionStream.Write($sessionBytes, 0, $sessionBytes.Length)
            $sessionStream.Flush($true)
        }
        finally {
            $sessionStream.Dispose()
        }
        # Modify is required so the next idempotent launch can replace this
        # short-lived capability file. Read/write alone blocks deletion on NTFS.
        & icacls.exe $localCapabilitySessionFile /inheritance:r /grant:r "$env:USERNAME`:(M)" "SYSTEM:(F)" | Out-Null
    }

    $env:ATNOS_LOCAL_CAPABILITY_TOKEN = $token
    $env:ATNOS_LOCAL_CAPABILITY_EXPIRES_AT = [string]$expiresAt
}

function Test-LocalCapabilitySession {
    if (-not $env:ATNOS_LOCAL_CAPABILITY_TOKEN) {
        return $false
    }
    try {
        $headers = @{
            "X-Atnos-Local-Capability" = $env:ATNOS_LOCAL_CAPABILITY_TOKEN
        }
        $result = Invoke-RestMethod `
            -Uri "http://127.0.0.1:8000/local-host/capability-session" `
            -Headers $headers `
            -TimeoutSec 3
        return $result.status -eq "authorized"
    }
    catch {
        return $false
    }
}

function Bind-LocalCapabilitySession {
    if (-not $env:ATNOS_LOCAL_CAPABILITY_TOKEN -or -not $env:ATNOS_LOCAL_CAPABILITY_EXPIRES_AT) {
        return $false
    }
    try {
        $body = @{
            token = $env:ATNOS_LOCAL_CAPABILITY_TOKEN
            expires_at = [int64]$env:ATNOS_LOCAL_CAPABILITY_EXPIRES_AT
        } | ConvertTo-Json -Compress
        $result = Invoke-RestMethod `
            -Method Post `
            -Uri "http://127.0.0.1:8000/local-host/capability-session/bind" `
            -ContentType "application/json" `
            -Body $body `
            -TimeoutSec 5
        if ($result.status -ne "authorized") {
            return $false
        }
        return (Test-LocalCapabilitySession)
    }
    catch {
        return $false
    }
}

function Get-LauncherState {
    if (-not (Test-Path $launcherStateFile)) {
        return $null
    }

    try {
        return Get-Content -LiteralPath $launcherStateFile -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Write-LauncherState {
    param([string]$Phase)

    $script:launcherStatePhase = $Phase
    $payload = [ordered]@{
        pid = $PID
        phase = $Phase
        started_at = $launcherStartedAt.ToString("o")
        heartbeat_at = (Get-Date).ToString("o")
    }
    $payload | ConvertTo-Json -Compress | Set-Content -LiteralPath $launcherStateFile -Encoding UTF8
}

function Update-LauncherHeartbeat {
    if (-not $launcherMutexAcquired) {
        return
    }

    Write-LauncherState -Phase $launcherStatePhase
}

function Clear-LauncherState {
    $state = Get-LauncherState
    if ($state -and [int]$state.pid -ne $PID) {
        return
    }
    Remove-Item -LiteralPath $launcherStateFile -Force -ErrorAction SilentlyContinue
}

function Test-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSec = 4
    )

    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-ApiStartupStatus {
    if (-not (Test-Path $apiStartupStatusFile)) {
        return $null
    }

    try {
        return Get-Content -LiteralPath $apiStartupStatusFile -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-ApiStartupLogTail {
    $tailLines = @()
    if (Test-Path $apiErrLog) {
        $tailLines += Get-Content -LiteralPath $apiErrLog -Tail 6 -ErrorAction SilentlyContinue
    }
    if ($tailLines.Count -eq 0 -and (Test-Path $apiOutLog)) {
        $tailLines += Get-Content -LiteralPath $apiOutLog -Tail 6 -ErrorAction SilentlyContinue
    }
    $tailLines = $tailLines | Where-Object { $_ -and $_.Trim() }
    if ($tailLines.Count -eq 0) {
        return $null
    }
    return ($tailLines -join " | ")
}

function Get-ApiStartupSummary {
    $status = Get-ApiStartupStatus
    if ($status) {
        $phase = if ($status.startup_phase) { [string]$status.startup_phase } else { "unknown" }
        $checkpoint = if ($status.startup_last_checkpoint) { [string]$status.startup_last_checkpoint } else { "n/a" }
        $elapsed = if ($status.startup_elapsed_ms -ne $null) { [int]$status.startup_elapsed_ms } else { 0 }
        $state = if ($status.status) { [string]$status.status } else { "unknown" }
        $error = if ($status.startup_error) { " | error=$([string]$status.startup_error)" } else { "" }
        return "state=$state | phase=$phase | checkpoint=$checkpoint | elapsed_ms=$elapsed$error"
    }

    $logTail = Get-ApiStartupLogTail
    if ($logTail) {
        return "no startup status file yet | log_tail=$logTail"
    }

    return "no startup status file or API log context yet"
}

function Get-ListeningProcessId {
    param([int]$Port)

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        return $listener.OwningProcess
    }

    return $null
}

function Get-ProcessStartTimeSafe {
    param([int]$ProcessId)

    try {
        return (Get-Process -Id $ProcessId -ErrorAction Stop).StartTime
    }
    catch {
        return $null
    }
}

function Test-ProcessAlive {
    param([int]$ProcessId)

    try {
        Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-LauncherProcesses {
    return @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -ieq "powershell.exe" -and
                $_.CommandLine -match "Launch-Atnos\.ps1"
            } |
            ForEach-Object {
                [pscustomobject]@{
                    ProcessId = $_.ProcessId
                    StartTime = Get-ProcessStartTimeSafe -ProcessId $_.ProcessId
                    CommandLine = $_.CommandLine
                }
            }
    )
}

function Get-ManagedServiceProcesses {
    param([string]$CommandPattern)
    $role = if ($CommandPattern -match 'serve_spa') { 'web' } else { 'api' }
    return @(Get-AtnosOwnedProcesses | Where-Object { $_.role -eq $role } | ForEach-Object {
        [pscustomobject]@{ ProcessId = $_.pid; StartTime = Get-ProcessStartTimeSafe -ProcessId $_.pid }
    })
}

function Get-LatestWriteTime {
    param([string[]]$Paths)

    $latest = $null
    foreach ($path in $Paths) {
        if (-not (Test-Path $path)) {
            continue
        }

        $item = Get-Item -LiteralPath $path
        if ($item.PSIsContainer) {
            Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue |
                ForEach-Object {
                    if ($null -eq $latest -or $_.LastWriteTime -gt $latest) {
                        $latest = $_.LastWriteTime
                    }
                }
        }
        elseif ($null -eq $latest -or $item.LastWriteTime -gt $latest) {
            $latest = $item.LastWriteTime
        }
    }

    return $latest
}

function Test-LauncherDevRefreshEnabled {
    $truthy = @("1", "true", "yes", "on")
    foreach ($value in @($env:ATNOS_DEV_REFRESH, $env:ATNOS_LAUNCHER_STALE_CHECK)) {
        if ($value -and $truthy -contains $value.Trim().ToLowerInvariant()) {
            return $true
        }
    }

    return $false
}

function Test-ServiceHealthyFast {
    param(
        [string]$Url,
        [string]$ServiceName,
        [string]$CommandPattern,
        [int]$Port
    )

    if ((-not (Test-LauncherDevRefreshEnabled)) -and (Test-HttpReady -Url $Url -TimeoutSec 1)) {
        Write-LauncherLog "$ServiceName already healthy. Skipping stale source scan."
        [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $CommandPattern -ServiceName $ServiceName -Port $Port)
        return $true
    }

    if (Test-LauncherDevRefreshEnabled) {
        Write-LauncherLog "$ServiceName dev refresh enabled. Running stale source scan before reuse."
    }

    return $false
}

function Copy-LegacyPathToQuarantine {
    param(
        [string]$SourcePath,
        [string]$Label
    )

    if (-not (Test-Path $SourcePath)) {
        return
    }

    $target = Join-Path $quarantineRoot $Label
    if (Test-Path $target) {
        return
    }

    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Write-LauncherLog "Quarantining legacy ATNOS data from $SourcePath to $target."
    & robocopy $SourcePath $target /E /XD "quarantine" /R:0 /W:0 /NFL /NDL /NJH /NJS /NP | Out-Null
    $exitCode = $LASTEXITCODE
    if ($exitCode -ge 8) {
        Write-LauncherLog "Legacy quarantine warning for $SourcePath (robocopy exit code $exitCode). Continuing startup."
        try {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
    }
}

function Resolve-BrowserRuntimeExecutable {
    $canonicalBrowser = 'E:\Atnos\Storage\Tools\playwright-browsers\chromium-1217\chrome-win64\chrome.exe'
    if (Test-Path -LiteralPath $canonicalBrowser -PathType Leaf) {
        return $canonicalBrowser
    }
    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    $operaCandidates = @(
        (Join-Path $localAppData "Programs\\Opera GX\\opera.exe"),
        (Join-Path $localAppData "Programs\\Opera\\opera.exe")
    )
    foreach ($candidate in $operaCandidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    $candidates = @()
    $candidates += Get-ChildItem -Path $playwrightBrowsersDir -Recurse -Filter "chrome.exe" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    $candidates += Get-ChildItem -Path $playwrightBrowsersDir -Recurse -Filter "msedge.exe" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    $resolved = $candidates | Where-Object { $_ -and (Test-Path $_) }
    return $resolved | Select-Object -First 1
}

function Get-ProcessPathSafe {
    param([System.Diagnostics.Process]$Process)

    try {
        return $Process.Path
    }
    catch {
        return $null
    }
}

function Stop-StaleDesktopShellProcesses {
    # Only recycle *our* managed shell when it is headless (no HWND).
    # Unknown/other installs are left alone.
    $zombies = Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $path = Get-ProcessPathSafe -Process $_
            if (-not $path -or ($path -ine $managedShellExe)) {
                return $false
            }
            try {
                return $_.MainWindowHandle -eq [IntPtr]::Zero
            }
            catch {
                return $true
            }
        }

    foreach ($shell in $zombies) {
        Write-LauncherLog "Recycling headless managed desktop shell zombie (PID=$($shell.Id), no MainWindowHandle)."
        try {
            [void](Stop-AtnosOwnedProcess -ProcessId $shell.Id)
        }
        catch {
            Write-LauncherLog "Could not recycle zombie shell PID=$($shell.Id): $($_.Exception.Message)"
        }
    }
    if (-not $zombies) {
        Write-LauncherLog 'No headless managed shell zombies to recycle.'
    }
}

function Stop-ManagedDesktopShellProcesses {
    $managedShells = Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $path = Get-ProcessPathSafe -Process $_
            $path -and ($path -ieq $managedShellExe)
        }

    foreach ($shell in $managedShells) {
        Write-LauncherLog "Refreshing managed desktop shell (PID=$($shell.Id))."
        try {
            [void](Stop-AtnosOwnedProcess -ProcessId $shell.Id)
        }
        catch {
            Write-LauncherLog "Could not refresh managed desktop shell PID=$($shell.Id): $($_.Exception.Message)"
        }
    }
}

function Ensure-ManagedDesktopShell {
    if (-not (Test-Path $managedShellSource)) {
        throw "Brakuje zrodla shella desktopowego: $managedShellSource"
    }

    if (-not (Test-Path $managedShellCompiler)) {
        throw "Brakuje kompilatora C# dla shella desktopowego: $managedShellCompiler"
    }

    if (-not (Test-Path $webView2SdkDir)) {
        throw "Brakuje bibliotek WebView2: $webView2SdkDir"
    }

    $sourceItem = Get-Item -LiteralPath $managedShellSource
    $needsBuild = -not (Test-Path $managedShellExe)
    if (-not $needsBuild) {
        $exeItem = Get-Item -LiteralPath $managedShellExe
        if ($exeItem.LastWriteTime -lt $sourceItem.LastWriteTime) {
            $needsBuild = $true
        }

        foreach ($assemblyName in $webView2Assemblies) {
            $assemblyTarget = Join-Path $appRoot $assemblyName
            $assemblySource = Join-Path $webView2SdkDir $assemblyName
            if (-not (Test-Path $assemblyTarget)) {
                $needsBuild = $true
                break
            }
            if ((Get-Item -LiteralPath $assemblyTarget).LastWriteTime -lt (Get-Item -LiteralPath $assemblySource).LastWriteTime) {
                $needsBuild = $true
                break
            }
        }
    }

    if (-not $needsBuild) {
        return
    }

    # The executable cannot be replaced while the current shell is running.
    # Stop it only for a real source/SDK update; normal launches merely activate
    # the existing single instance.
    Stop-ManagedDesktopShellProcesses
    Start-Sleep -Milliseconds 500

    foreach ($assemblyName in $webView2Assemblies) {
        $assemblySource = Join-Path $webView2SdkDir $assemblyName
        if (-not (Test-Path $assemblySource)) {
            throw "Brakuje biblioteki WebView2: $assemblySource"
        }
        Copy-Item -LiteralPath $assemblySource -Destination (Join-Path $appRoot $assemblyName) -Force
    }

    $compilerArgs = @(
        "/nologo",
        "/target:winexe",
        ("/out:" + $managedShellExe),
        "/reference:System.dll",
        "/reference:System.Core.dll",
        "/reference:System.Drawing.dll",
        "/reference:System.Security.dll",
        "/reference:System.Windows.Forms.dll",
        ("/reference:" + (Join-Path $appRoot "Microsoft.Web.WebView2.Core.dll")),
        ("/reference:" + (Join-Path $appRoot "Microsoft.Web.WebView2.WinForms.dll")),
        $managedShellSource
    )

    Write-LauncherLog "Building managed desktop shell."
    & $managedShellCompiler @compilerArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $managedShellExe)) {
        throw "Nie udalo sie zbudowac shella desktopowego."
    }

    $signingCertificate = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq "CN=Atnos Local Build" -and $_.HasPrivateKey } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1
    if ($signingCertificate) {
        $signature = Set-AuthenticodeSignature `
            -FilePath $managedShellExe `
            -Certificate $signingCertificate `
            -HashAlgorithm SHA256
        if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
            Write-LauncherLog "Managed shell signing failed: $($signature.StatusMessage)"
        }
    }
    else {
        Write-LauncherLog "Managed shell signing certificate is unavailable; portable shell will be used."
    }
}

function Test-ManagedDesktopShellTrusted {
    if (-not (Test-Path -LiteralPath $managedShellExe)) {
        return $false
    }

    # Solo local install: prefer the WebView2 desktop shell even when the self-signed
    # "Atnos Local Build" certificate is not in the machine trust store. Require a
    # Valid Authenticode signature only when ATNOS_REQUIRE_SIGNED_DESKTOP_SHELL=1.
    $requireSigned = $env:ATNOS_REQUIRE_SIGNED_DESKTOP_SHELL -and @(
        "1", "true", "yes", "on"
    ) -contains $env:ATNOS_REQUIRE_SIGNED_DESKTOP_SHELL.Trim().ToLowerInvariant()
    if (-not $requireSigned) {
        return $true
    }

    try {
        $signature = Get-AuthenticodeSignature -FilePath $managedShellExe
        return $signature.Status -eq [System.Management.Automation.SignatureStatus]::Valid
    }
    catch {
        return $false
    }
}

function Stop-ListeningProcess {
    param([int]$Port, [string]$ServiceName)
    $processId = Get-ListeningProcessId -Port $Port
    if ($processId) { Stop-ProcessByIdSafe -ProcessId $processId -ServiceName $ServiceName }
}

function Stop-ProcessByIdSafe {
    param([int]$ProcessId, [string]$ServiceName)
    if (-not $ProcessId) { return }
    Write-LauncherLog "Requesting identity-checked stop of $ServiceName PID=$ProcessId."
    [void](Stop-AtnosOwnedProcess -ProcessId $ProcessId -AllowApiStop:$RestartBackend)
}

function Stop-ManagedServiceProcesses {
    param(
        [string]$CommandPattern,
        [string]$ServiceName,
        [int]$ExcludeProcessId = 0
    )

    $managedProcesses = Get-ManagedServiceProcesses -CommandPattern $CommandPattern
    foreach ($process in $managedProcesses) {
        $processId = [int]$process.ProcessId
        if ($ExcludeProcessId -gt 0 -and $processId -eq $ExcludeProcessId) {
            continue
        }
        Stop-ProcessByIdSafe -ProcessId $processId -ServiceName $ServiceName
    }
}

function Cleanup-OrphanedManagedServiceProcesses {
    param([string]$CommandPattern, [string]$ServiceName, [int]$Port, [int]$MinimumOrphanAgeSec = 30)
    # Keep the process that owns the listen socket; stop other Atnos-managed
    # duplicates of the same role (common after overlapping Launch/Watchdog runs).
    $managed = @(Get-ManagedServiceProcesses -CommandPattern $CommandPattern)
    $listener = Get-ListeningProcessId -Port $Port
    if ($listener) {
        if ($listener -notin @($managed | ForEach-Object { $_.ProcessId })) {
            throw "Port $Port occupied by foreign or unverifiable process PID=$listener; left running."
        }
        $keep = New-Object 'System.Collections.Generic.HashSet[int]'
        [void]$keep.Add([int]$listener)
        try {
            $listenerProc = Get-CimInstance Win32_Process -Filter "ProcessId=$listener" -ErrorAction Stop
            if ($listenerProc.ParentProcessId) {
                [void]$keep.Add([int]$listenerProc.ParentProcessId)
            }
        } catch {}
        foreach ($process in $managed) {
            $processId = [int]$process.ProcessId
            if ($keep.Contains($processId)) {
                continue
            }
            Write-LauncherLog "Stopping duplicate $ServiceName PID=$processId (listener PID=$listener kept)."
            Stop-ProcessByIdSafe -ProcessId $processId -ServiceName $ServiceName
        }
        return $listener
    }
    if ($managed.Count -gt 1) {
        $keeper = [int]$managed[0].ProcessId
        # Windows venv starts a base-Python child before either process owns a
        # listener. Matching command lines do not prove a duplicate startup.
        # Preserve the existing candidates and wait for readiness; never kill
        # the interpreter child or launch another family during this window.
        Write-LauncherLog "Preserving $($managed.Count) owned $ServiceName startup processes without a listener (keeper=$keeper); awaiting readiness."
        return $keeper
    }
    if ($managed.Count -gt 0) { return $managed[0].ProcessId }
    return $null
}

function Test-ServiceStale {
    param(
        [int]$Port,
        [string[]]$WatchPaths
    )

    $processId = Get-ListeningProcessId -Port $Port
    if (-not $processId) {
        return $false
    }

    $startedAt = Get-ProcessStartTimeSafe -ProcessId $processId
    $latestWrite = Get-LatestWriteTime -Paths $WatchPaths

    if ($startedAt -and $latestWrite -and $startedAt -lt $latestWrite) {
        return $true
    }

    return $false
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$Attempts = 20,
        [int]$DelayMs = 500
    )

    for ($attempt = 0; $attempt -lt $Attempts; $attempt += 1) {
        Update-LauncherHeartbeat
        if (Test-HttpReady -Url $Url) {
            return $true
        }
        Start-Sleep -Milliseconds $DelayMs
    }

    return $false
}

function Wait-ServiceReady {
    param(
        [string]$Url,
        [string]$ServiceName,
        [int]$TimeoutSec = 60,
        [int]$DelayMs = 500,
        [int]$ProcessId = 0
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Update-LauncherHeartbeat
        if (Test-HttpReady -Url $Url) {
            return $true
        }

        if ($ProcessId -gt 0) {
            try {
                Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
            }
            catch {
                Write-LauncherLog "$ServiceName process PID=$ProcessId exited before readiness probe succeeded."
                return $false
            }
        }

        Start-Sleep -Milliseconds $DelayMs
    }

    Write-LauncherLog "$ServiceName did not become ready within ${TimeoutSec}s."
    return $false
}

function Wait-ApiReady {
    param(
        [string]$Url,
        [int]$TimeoutSec = 180,
        [int]$DelayMs = 500,
        [int]$ProcessId = 0
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastSummary = $null
    while ((Get-Date) -lt $deadline) {
        Update-LauncherHeartbeat
        if (Test-HttpReady -Url $Url) {
            return $true
        }

        $summary = Get-ApiStartupSummary
        if ($summary -and $summary -ne $lastSummary) {
            Write-LauncherLog "API warmup: $summary"
            $lastSummary = $summary
        }

        if ($ProcessId -gt 0) {
            try {
                Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
            }
            catch {
                Write-LauncherLog "API process PID=$ProcessId exited before readiness probe succeeded. Last known state: $summary"
                return $false
            }
        }

        Start-Sleep -Milliseconds $DelayMs
    }

    Write-LauncherLog "API did not become ready within ${TimeoutSec}s. Last known state: $lastSummary"
    return $false
}

function Try-Recover-StaleLauncher {
    param([int]$HeartbeatTimeoutSec = 90, [int]$ProcessAgeTimeoutSec = 300)
    # Mutex abandonment releases ownership after a crash. A timestamp/PID file
    # never authorizes killing another launcher (nor a recycled PID).
    return $false
}

function Wait-For-ExistingStartup {
    param(
        [int]$MaxWaitSec = 60
    )

    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Get-Date) -lt $deadline) {
        if ((Test-HttpReady -Url "http://127.0.0.1:8000/health/ready") -and (Test-HttpReady -Url "http://127.0.0.1:3000/")) {
            return "services_ready"
        }

        if (Try-Recover-StaleLauncher) {
            return "recovered_stale"
        }

        Start-Sleep -Milliseconds 1000
    }

    return "timed_out"
}

function Acquire-LauncherMutex {
    param([int]$TimeoutMs)

    try {
        return $launcherMutex.WaitOne($TimeoutMs)
    }
    catch [System.Threading.AbandonedMutexException] {
        Write-LauncherLog "Recovered abandoned launcher mutex."
        return $true
    }
}

function Ensure-Api {
    Write-LauncherState -Phase "starting-api"
    $apiWatchPaths = @(
        (Join-Path $apiRoot "src\\atnos"),
        (Join-Path $apiRoot "alembic")
    )
    $apiCommandPattern = "(uvicorn|run_atnos_api\.py).*atnos\.api\.main:create_app"
    $apiHealthUrl = "http://127.0.0.1:8000/health/ready"
    $apiWarmupTimeoutSec = 180
    $existingApiPid = Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000

    if ($RestartBackend -and $existingApiPid) {
        Write-LauncherLog "Explicit backend reload: checking work and requesting graceful drain."
        Stop-ManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API"
        $existingApiPid = $null
    }
    elseif (Test-ServiceStale -Port 8000 -WatchPaths $apiWatchPaths) {
        Write-LauncherLog "restart_pending: API source changed; ordinary startup preserves the running API."
    }
    if (Test-ServiceHealthyFast -Url $apiHealthUrl -ServiceName "API" -CommandPattern $apiCommandPattern -Port 8000) {
        if (Test-LocalCapabilitySession) {
            return
        }
        if (Bind-LocalCapabilitySession) {
            Write-LauncherLog "Bound current local capability session onto healthy API without restart."
            return
        }
        # Personal PC mode: never kill a healthy API just because capability bind failed.
        Write-LauncherLog "API healthy; capability session not bound yet - keeping process (no restart)."
        return
    }

    if (Test-HttpReady -Url $apiHealthUrl) {
        if (Test-LocalCapabilitySession) {
            Write-LauncherLog "API already healthy with an authorized local capability session."
            [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000)
            return
        }
        if (Bind-LocalCapabilitySession) {
            Write-LauncherLog "Hot-bound capability session onto already-healthy API."
            [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000)
            return
        }
        Write-LauncherLog "API already healthy; keeping it without capability restart."
        [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000)
        return
    }

    if ($existingApiPid) {
        Write-LauncherLog "API port 8000 already claimed by PID=$existingApiPid. Waiting for current startup."
        if (Wait-ApiReady -Url $apiHealthUrl -TimeoutSec $apiWarmupTimeoutSec -ProcessId $existingApiPid) {
            if (Test-LocalCapabilitySession) {
                Write-LauncherLog "Existing API process became healthy with the current capability session."
                [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000)
                return
            }
            if (Bind-LocalCapabilitySession) {
                Write-LauncherLog "Hot-bound capability session onto existing healthy API process."
                [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000)
                return
            }
            Write-LauncherLog "Existing API became healthy; keeping it without capability restart."
            [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000)
            return
        }

        throw "Existing API did not become ready; left running. Explicit idle reload is required."
    }

    if (-not (Test-Path $apiPython)) {
        throw "Brakuje runtime Python dla API: $apiPython"
    }

    $env:APP_ROOT = $appRoot
    $env:OBSIDIAN_VAULT_PATH = "E:/Atnos/Knowledge/Obsidian/Atnos-Brain"
    $env:DATA_DIR = ($dataRoot -replace "\\","/")
    $env:ATNOS_ENV_FILE = ($runtimeEnvFile -replace "\\","/")
    $env:ATNOS_ROOT = ($appRoot -replace "\\","/")
    $env:ATNOS_DATA_DIR = ($atnosDataRoot -replace "\\","/")
    $env:ATNOS_STATE_DIR = ($atnosDataRoot -replace "\\","/")
    $env:ATNOS_CACHE_DIR = ($atnosCacheDir -replace "\\","/")
    $env:ATNOS_TEMP_DIR = ($atnosTempDir -replace "\\","/")
    $env:TEMP = $atnosTempDir
    $env:TMP = $atnosTempDir
    $env:PIP_CACHE_DIR = ($pipCacheDir -replace "\\","/")
    $env:PIP_CONFIG_FILE = ($pipConfigFile -replace "\\","/")
    $env:npm_config_cache = ($npmCacheDir -replace "\\","/")
    $env:XDG_CACHE_HOME = ($xdgCacheDir -replace "\\","/")
    $env:HF_HOME = ($hfCacheDir -replace "\\","/")
    $env:TRANSFORMERS_CACHE = ($transformersCacheDir -replace "\\","/")
    $env:TORCH_HOME = ($torchCacheDir -replace "\\","/")
    $env:UV_CACHE_DIR = ($uvCacheDir -replace "\\","/")
    $env:OLLAMA_MODELS = ($ollamaModelsDir -replace "\\","/")
    $env:UPLOADS_DIR = ($uploadsDir -replace "\\","/")
    $env:ACCOUNT_SECRET_VAULT_DIR = ($accountSecretVaultDir -replace "\\","/")
    $env:SQLITE_FILE = ($sqliteFile -replace "\\","/")
    $env:RUNTIME_DIR = ($runtimeLogRoot -replace "\\","/")
    $env:QUARANTINE_DIR = ($quarantineRoot -replace "\\","/")
    $env:BROWSER_PROFILE_DIR = ($browserProfileDir -replace "\\","/")
    $env:BROWSER_DOWNLOADS_DIR = ($browserDownloadsDir -replace "\\","/")
    $env:BROWSER_TRACE_DIR = ($browserTraceDir -replace "\\","/")
    $env:PLAYWRIGHT_BROWSERS_PATH = ($playwrightBrowsersDir -replace "\\","/")
    $env:WORKSPACE_STAGING_DIR = ($workspaceStagingDir -replace "\\","/")
    $env:BROWSER_USER_DATA_DIR = ($browserOperatorUserDataDir -replace "\\","/")
    $env:BROWSER_DOWNLOAD_DIR = ($browserOperatorDownloadsDir -replace "\\","/")
    $env:BROWSER_SCREENSHOT_DIR = ($browserOperatorScreenshotsDir -replace "\\","/")
    $env:BROWSER_LOG_PATH = ($browserOperatorLogPath -replace "\\","/")
    $env:ATNOS_DESKTOP_SHELL = ($managedShellExe -replace "\\","/")
    $browserRuntimeExecutable = Resolve-BrowserRuntimeExecutable
    if ($browserRuntimeExecutable) {
        $env:BROWSER_RUNTIME_EXECUTABLE = ($browserRuntimeExecutable -replace "\\","/")
    } else {
        Remove-Item Env:\BROWSER_RUNTIME_EXECUTABLE -ErrorAction SilentlyContinue
    }

    Remove-Item -LiteralPath $apiStartupStatusFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $apiStartupEventsFile -Force -ErrorAction SilentlyContinue

    Write-LauncherLog "Starting fallback API runtime."
    $apiProcess = Start-Process `
        -FilePath $apiPython `
        -ArgumentList (Join-Path $apiRoot "scripts\run_atnos_api.py"), "--app", "atnos.api.main:create_app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory $apiRoot `
        -RedirectStandardOutput $apiOutLog `
        -RedirectStandardError $apiErrLog `
        -WindowStyle Hidden `
        -PassThru

    if (-not (Wait-ApiReady -Url $apiHealthUrl -TimeoutSec $apiWarmupTimeoutSec -ProcessId $apiProcess.Id)) {
        $startupSummary = Get-ApiStartupSummary
        # Preserve a slow startup; timeout is not permission to kill active work.
        throw "API nie wstalo. Startup stuck at: $startupSummary. Zobacz log: $apiErrLog"
    }
    [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $apiCommandPattern -ServiceName "API" -Port 8000)
}

function Test-OllamaModelsOnE {
    param([string]$ModelsDir, [string]$BaseUrl)

    try {
        if (-not $BaseUrl) { $BaseUrl = (Get-AtnosOllamaEndpoint -EnvFile $runtimeEnvFile).BaseUrl }
        $modelsRoot = [IO.Path]::GetFullPath($ModelsDir)
        if ([IO.Path]::GetPathRoot($modelsRoot) -ine 'E:\') { return $false }
        $manifestRoot = Join-Path $modelsRoot 'manifests'
        $blobRoot = Join-Path $modelsRoot 'blobs'
        foreach ($path in @($modelsRoot, $manifestRoot, $blobRoot)) {
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
        }

        # The API exposes digests but not its model root. Match every live digest to
        # a complete, non-linked manifest and its referenced blobs on E:.
        $manifestDigests = @{}
        $entries = @(Get-ChildItem -LiteralPath $manifestRoot -Recurse -Force -ErrorAction Stop)
        foreach ($entry in $entries) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
            if (-not $entry.PSIsContainer) {
                $digest = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
                $manifestDigests[$digest] = $entry.FullName
            }
        }
        if ($manifestDigests.Count -eq 0) { return $false }
        $response = Invoke-RestMethod -Uri ($BaseUrl.TrimEnd('/') + '/api/tags') -TimeoutSec 5 -ErrorAction Stop
        $models = @($response.models)
        if ($models.Count -eq 0) { return $false }
        foreach ($model in $models) {
            $digest = [string]$model.digest
            if ($digest -notmatch '^[0-9a-fA-F]{64}$' -or -not $manifestDigests.ContainsKey($digest.ToLowerInvariant())) { return $false }
            $manifest = Get-Content -LiteralPath $manifestDigests[$digest.ToLowerInvariant()] -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            if (-not $manifest.config) { return $false }
            foreach ($blob in @($manifest.config) + @($manifest.layers)) {
                if (-not $blob -or [string]$blob.digest -notmatch '^sha256:([0-9a-fA-F]{64})$') { return $false }
                $blobPath = Join-Path $blobRoot ('sha256-' + $Matches[1].ToLowerInvariant())
                $blobItem = Get-Item -LiteralPath $blobPath -Force -ErrorAction Stop
                if ($blobItem.PSIsContainer -or ($blobItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $blobItem.Length -ne [int64]$blob.size) { return $false }
            }
        }
        return $true
    }
    catch {
        Write-LauncherLog "Ollama model verification failed: $($_.Exception.Message)"
        return $false
    }
}

function Ensure-OllamaRuntime {
    $canonicalOllamaExe = 'E:\Atnos\Storage\Tools\Ollama\ollama.exe'
    $configuredEndpoint = Get-AtnosOllamaEndpoint -EnvFile $runtimeEnvFile
    $ollamaPort = $configuredEndpoint.Port
    $listener = Get-ListeningProcessId -Port $ollamaPort
    if ($listener) {
        $listenerIds = @($listener)
        $listenerIds += @(Get-NetTCPConnection -LocalPort $ollamaPort -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess })
        foreach ($listenerPid in @($listenerIds | Sort-Object -Unique)) {
            try {
                $ownerPath = [IO.Path]::GetFullPath([string](Get-Process -Id $listenerPid -ErrorAction Stop).Path)
            }
            catch {
                $ownerPath = '<unverifiable>'
            }
            if ($ownerPath -ine $canonicalOllamaExe) {
                $message = "Ollama conflict on port $($ollamaPort): PID=$listenerPid, executable=$ownerPath; expected E: executable. No process was stopped or started."
                Write-LauncherLog $message
                Write-Warning $message
                return
            }
        }
        if (-not (Test-OllamaModelsOnE -ModelsDir $ollamaModelsDir -BaseUrl $configuredEndpoint.BaseUrl)) {
            $message = "Ollama conflict on port $($ollamaPort): live model digests/blobs do not verify against E:. No process was stopped or started."
            Write-LauncherLog $message
            Write-Warning $message
            return
        }
        Write-LauncherLog "Reusing verified E: Ollama on port $ollamaPort (PID=$listener)."
        return
    }

    $ownerStateFile = Join-Path $runtimeLogRoot "ollama-owner-$ollamaPort.json"
    if (Test-Path -LiteralPath $ownerStateFile -PathType Leaf) {
        try {
            $ownerState = Get-Content -LiteralPath $ownerStateFile -Raw | ConvertFrom-Json
            $ownerProcess = Get-Process -Id ([int]$ownerState.pid) -ErrorAction Stop
            if ($ownerState.endpoint -eq $configuredEndpoint.BaseUrl -and
                [IO.Path]::GetFullPath([string]$ownerProcess.Path) -ieq $canonicalOllamaExe -and
                $ownerProcess.StartTime.ToUniversalTime().ToString('o') -eq $ownerState.created_utc) {
                Write-LauncherLog "Preserving owned Ollama startup on port $ollamaPort (PID=$($ownerProcess.Id))."
                return
            }
        } catch { Write-LauncherLog "No live verified Ollama startup for port $ollamaPort." }
    }
    if (-not (Test-Path -LiteralPath $canonicalOllamaExe -PathType Leaf)) {
        Write-LauncherLog 'Canonical E: Ollama missing; model startup skipped.'
        return
    }

    $profileRoot = 'E:\Atnos\Profiles\AtnosSystem'
    $appDataRoot = Join-Path $profileRoot 'AppData\Roaming'
    $localAppDataRoot = Join-Path $profileRoot 'AppData\Local'
    foreach ($path in @($profileRoot, $appDataRoot, $localAppDataRoot, $atnosTempDir, $atnosCacheDir, $ollamaModelsDir, $runtimeLogRoot)) {
        if ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path)) -ine 'E:\') { throw "Ollama path outside E: $path" }
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
    $childEnvironment = @{
        USERPROFILE = $profileRoot
        HOME = $profileRoot
        HOMEDRIVE = 'E:'
        HOMEPATH = '\Atnos\Profiles\AtnosSystem'
        APPDATA = $appDataRoot
        LOCALAPPDATA = $localAppDataRoot
        XDG_CACHE_HOME = $atnosCacheDir
        TEMP = $atnosTempDir
        TMP = $atnosTempDir
        OLLAMA_MODELS = $ollamaModelsDir
        OLLAMA_HOST = $configuredEndpoint.Host
        OLLAMA_BASE_URL = $configuredEndpoint.BaseUrl
    }
    $previousEnvironment = @{}
    foreach ($name in $childEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        foreach ($name in $childEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $childEnvironment[$name], 'Process')
        }
        $started = Start-Process -FilePath $canonicalOllamaExe -ArgumentList 'serve' -WorkingDirectory $appRoot `
            -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeLogRoot 'ollama-serve.log') `
            -RedirectStandardError (Join-Path $runtimeLogRoot 'ollama-serve.err.log') -PassThru
        @{
            pid = $started.Id
            created_utc = $started.StartTime.ToUniversalTime().ToString('o')
            executable = $canonicalOllamaExe
            endpoint = $configuredEndpoint.BaseUrl
            models_dir = $ollamaModelsDir
        } | ConvertTo-Json | Set-Content -LiteralPath $ownerStateFile -Encoding UTF8
        Write-LauncherLog "Requested start of E: Ollama (PID=$($started.Id)); API/UI do not wait for model warmup."
    }
    finally {
        foreach ($name in $previousEnvironment.Keys) {
            if ($null -eq $previousEnvironment[$name]) {
                Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
            }
            else {
                [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
            }
        }
    }
}

function Get-AtnosFileSha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    # Avoid depending on the caller's PowerShell module auto-loading environment.
    $stream = [IO.File]::OpenRead($LiteralPath)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace("-", "")
    }
    finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

function Ensure-FrontendDeployment {
    Write-LauncherState -Phase "syncing-frontend"

    if (-not (Test-Path -LiteralPath $frontendDeployScript -PathType Leaf)) {
        throw "Brakuje skryptu wdrozenia frontendu: $frontendDeployScript"
    }

    $distIndex = Join-Path $frontendDistRoot "index.html"
    $desktopIndex = Join-Path $webRoot "index.html"
    $sourceCandidates = @(
        (Join-Path $frontendRoot "src"),
        (Join-Path $frontendRoot "index.html"),
        (Join-Path $frontendRoot "package.json"),
        (Join-Path $frontendRoot "vite.config.ts"),
        (Join-Path $frontendRoot "tailwind.config.ts"),
        (Join-Path $frontendRoot "tsconfig.json")
    )
    $latestSourceWrite = Get-Date "2000-01-01"
    foreach ($candidate in $sourceCandidates) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        $candidateItem = Get-Item -LiteralPath $candidate
        if ($candidateItem.PSIsContainer) {
            $latestCandidate = Get-ChildItem -LiteralPath $candidate -Recurse -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTimeUtc -Descending |
                Select-Object -First 1
            if ($latestCandidate -and $latestCandidate.LastWriteTimeUtc -gt $latestSourceWrite.ToUniversalTime()) {
                $latestSourceWrite = $latestCandidate.LastWriteTime
            }
        }
        elseif ($candidateItem.LastWriteTimeUtc -gt $latestSourceWrite.ToUniversalTime()) {
            $latestSourceWrite = $candidateItem.LastWriteTime
        }
    }

    $needsBuild = -not (Test-Path -LiteralPath $distIndex -PathType Leaf)
    if (-not $needsBuild) {
        $needsBuild = $latestSourceWrite.ToUniversalTime() -gt (Get-Item -LiteralPath $distIndex).LastWriteTimeUtc
    }

    if ($needsBuild) {
        Write-LauncherLog "Frontend source is newer than dist; rebuilding and deploying desktop UI."
        & $frontendDeployScript -RepoRoot $appRoot -Build
        if ($LASTEXITCODE -ne 0) {
            throw "Budowanie i wdrozenie frontendu nie powiodlo sie (exit code $LASTEXITCODE)."
        }
        Stop-ManagedServiceProcesses -CommandPattern "serve_spa\.py" -ServiceName "web shell"
        return $true
    }

    $needsDeploy = -not (Test-Path -LiteralPath $desktopIndex -PathType Leaf)
    if (-not $needsDeploy) {
        $distHash = Get-AtnosFileSha256 -LiteralPath $distIndex
        $desktopHash = Get-AtnosFileSha256 -LiteralPath $desktopIndex
        $needsDeploy = -not [string]::Equals($distHash, $desktopHash, [StringComparison]::OrdinalIgnoreCase)
    }

    if ($needsDeploy) {
        Write-LauncherLog "Desktop UI bundle differs from frontend dist; deploying current build."
        & $frontendDeployScript -RepoRoot $appRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Wdrozenie frontendu do desktop-web nie powiodlo sie (exit code $LASTEXITCODE)."
        }
        Stop-ManagedServiceProcesses -CommandPattern "serve_spa\.py" -ServiceName "web shell"
        return $true
    }

    Write-LauncherLog "Desktop UI bundle matches current frontend dist."
    return $false
}

function Ensure-Web {
    Write-LauncherState -Phase "starting-web"
    $webWatchPaths = @(
        $spaServerScript,
        $webRoot
    )
    $webCommandPattern = "serve_spa\.py"
    $webUrl = "http://127.0.0.1:3000/"
    $existingWebPid = Cleanup-OrphanedManagedServiceProcesses -CommandPattern $webCommandPattern -ServiceName "web shell" -Port 3000

    if (Test-ServiceStale -Port 3000 -WatchPaths $webWatchPaths) {
        Write-LauncherLog "Web shell source is newer than the running process; restarting SPA server."
        Stop-ManagedServiceProcesses -CommandPattern $webCommandPattern -ServiceName "web shell"
        $existingWebPid = $null
    }
    elseif (Test-ServiceHealthyFast -Url $webUrl -ServiceName "Web shell" -CommandPattern $webCommandPattern -Port 3000) {
        return
    }

    if (Test-HttpReady -Url $webUrl) {
        Write-LauncherLog "Web shell already healthy."
        [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $webCommandPattern -ServiceName "web shell" -Port 3000)
        return
    }

    if ($existingWebPid) {
        Write-LauncherLog "Web shell port 3000 already claimed by PID=$existingWebPid. Waiting for current startup."
        if (Wait-ServiceReady -Url $webUrl -ServiceName "Web shell" -TimeoutSec 20 -ProcessId $existingWebPid) {
            Write-LauncherLog "Existing web shell became healthy."
            [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $webCommandPattern -ServiceName "web shell" -Port 3000)
            return
        }

        Stop-ManagedServiceProcesses -CommandPattern $webCommandPattern -ServiceName "web shell"
        if (Test-HttpReady -Url $webUrl) {
            Write-LauncherLog "Web shell became healthy during restart window."
            return
        }
    }

    if (-not (Test-Path $staticPython)) {
        throw "Brakuje Pythona do statycznego shellu: $staticPython"
    }
    if (-not (Test-Path $spaServerScript)) {
        throw "Brakuje serwera SPA: $spaServerScript"
    }
    if (-not (Test-Path (Join-Path $webRoot 'index.html'))) {
        throw "Brakuje zbudowanego frontendu w: $webRoot"
    }

    Write-LauncherLog "Starting ATNOS SPA shell."
    $webProcess = Start-Process `
        -FilePath $staticPython `
        -ArgumentList $spaServerScript, "--host", "127.0.0.1", "--port", "3000", "--root", $webRoot `
        -WorkingDirectory (Split-Path $spaServerScript -Parent) `
        -RedirectStandardOutput $webOutLog `
        -RedirectStandardError $webErrLog `
        -WindowStyle Hidden `
        -PassThru

    if (-not (Wait-ServiceReady -Url $webUrl -ServiceName "Web shell" -TimeoutSec 20 -ProcessId $webProcess.Id)) {
        Stop-ManagedServiceProcesses -CommandPattern $webCommandPattern -ServiceName "web shell"
        throw "Web shell nie wstal. Zobacz log: $webErrLog"
    }
    [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern $webCommandPattern -ServiceName "web shell" -Port 3000)
}

function Ensure-UiBootstrap {
    Write-LauncherState -Phase "waiting-ui-bootstrap"
    $bootstrapUrl = "http://127.0.0.1:8000/ui/bootstrap"
    $timeoutSec = 3
    $delayMs = 500
    if ($env:ATNOS_WAIT_UI_BOOTSTRAP -and @("1", "true", "yes", "on") -contains $env:ATNOS_WAIT_UI_BOOTSTRAP.Trim().ToLowerInvariant()) {
        $timeoutSec = 60
        $delayMs = 1000
    }
    Write-LauncherLog "Warming UI bootstrap payload (timeout=${timeoutSec}s)."
    if (-not (Wait-ServiceReady -Url $bootstrapUrl -ServiceName "UI bootstrap" -TimeoutSec $timeoutSec -DelayMs $delayMs)) {
        Write-LauncherLog "UI bootstrap is still warming up. Opening shell; frontend will retry."
        return $false
    }
    return $true
}

function Open-AtnosWindow {
    Write-LauncherState -Phase "opening-shell"
    Stop-StaleDesktopShellProcesses
    Ensure-ManagedDesktopShell
    $shellLaunchUrl = "http://127.0.0.1:3000/?shell_ts=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

    function Start-DesktopShellProcess {
        param(
            [Parameter(Mandatory = $true)][string]$FilePath,
            [Parameter(Mandatory = $true)][string]$Label,
            [int]$WindowWaitSeconds = 25
        )

        function Get-VisibleShellWindow {
            return Get-Process -ErrorAction SilentlyContinue |
                Where-Object {
                    $path = Get-ProcessPathSafe -Process $_
                    $path -and ($path -ieq $FilePath) -and $_.MainWindowHandle -ne [IntPtr]::Zero
                } |
                Select-Object -First 1
        }

        function Stop-HeadlessShellOwners {
            $owners = Get-Process -ErrorAction SilentlyContinue |
                Where-Object {
                    $path = Get-ProcessPathSafe -Process $_
                    if (-not $path -or ($path -ine $FilePath)) {
                        return $false
                    }
                    try {
                        return $_.MainWindowHandle -eq [IntPtr]::Zero
                    }
                    catch {
                        return $true
                    }
                }
            foreach ($owner in $owners) {
                Write-LauncherLog "Stopping headless $Label owner before relaunch (PID=$($owner.Id))."
                try {
                    [void](Stop-AtnosOwnedProcess -ProcessId $owner.Id)
                }
                catch {
                    Write-LauncherLog "Could not stop headless $Label PID=$($owner.Id): $($_.Exception.Message)"
                }
            }
            if ($owners) {
                Start-Sleep -Milliseconds 700
            }
        }

        Stop-HeadlessShellOwners

        Write-LauncherLog "Opening $Label from $FilePath."
        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $shellLaunchUrl `
            -WorkingDirectory $appRoot `
            -WindowStyle Normal `
            -ErrorAction Stop `
            -PassThru
        $deadline = [DateTime]::UtcNow.AddSeconds($WindowWaitSeconds)
        while ([DateTime]::UtcNow -lt $deadline) {
            $process.Refresh()
            if ($process.HasExited) {
                # Single-instance shells exit 0 after activating an already-running window.
                if ([int]$process.ExitCode -eq 0) {
                    $existingWindow = Get-VisibleShellWindow
                    if ($existingWindow) {
                        Write-LauncherLog "$Label already running - activated existing window (PID=$($existingWindow.Id))."
                        return $true
                    }
                    # Exit 0 but no HWND ⇒ zombie single-instance owner. Kill and retry once.
                    Write-LauncherLog "$Label exited 0 without a visible window; recycling headless owners and retrying once."
                    Stop-HeadlessShellOwners
                    $retry = Start-Process `
                        -FilePath $FilePath `
                        -ArgumentList $shellLaunchUrl `
                        -WorkingDirectory $appRoot `
                        -WindowStyle Normal `
                        -ErrorAction Stop `
                        -PassThru
                    $retryDeadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(12, [int]($WindowWaitSeconds / 2)))
                    while ([DateTime]::UtcNow -lt $retryDeadline) {
                        $retry.Refresh()
                        if ($retry.HasExited) {
                            $existingWindow = Get-VisibleShellWindow
                            if ($existingWindow) {
                                Write-LauncherLog "$Label retry activated visible window (PID=$($existingWindow.Id))."
                                return $true
                            }
                            throw "$Label retry exited before showing a window (exit code $($retry.ExitCode))."
                        }
                        if ($retry.MainWindowHandle -ne [IntPtr]::Zero) {
                            Write-LauncherLog "$Label window is ready after retry (PID=$($retry.Id))."
                            return $true
                        }
                        Start-Sleep -Milliseconds 250
                    }
                    if (-not $retry.HasExited) {
                        try { [void](Stop-AtnosOwnedProcess -ProcessId $retry.Id) } catch { }
                    }
                    throw "$Label did not expose a desktop window after zombie recycle."
                }
                throw "$Label exited before showing a window (exit code $($process.ExitCode))."
            }
            if ($process.MainWindowHandle -ne [IntPtr]::Zero) {
                Write-LauncherLog "$Label window is ready (PID=$($process.Id))."
                return $true
            }
            Start-Sleep -Milliseconds 250
        }
        $process.Refresh()
        if (-not $process.HasExited -and $process.MainWindowHandle -ne [IntPtr]::Zero) {
            return $true
        }
        # Last chance: another single-instance owner may own the visible window.
        $existingWindow = Get-VisibleShellWindow
        if ($existingWindow) {
            Write-LauncherLog "$Label window already visible (PID=$($existingWindow.Id))."
            return $true
        }
        if (-not $process.HasExited) {
            try { [void](Stop-AtnosOwnedProcess -ProcessId $process.Id) } catch { }
        }
        throw "$Label did not expose a desktop window within ${WindowWaitSeconds}s."
    }

    $capabilityToken = $env:ATNOS_LOCAL_CAPABILITY_TOKEN
    $capabilityExpiry = $env:ATNOS_LOCAL_CAPABILITY_EXPIRES_AT
    try {
        # The desktop shell reads the DPAPI-protected session file. Do not leak
        # the capability through the child process environment or command line.
        Remove-Item Env:ATNOS_LOCAL_CAPABILITY_TOKEN -ErrorAction SilentlyContinue
        Remove-Item Env:ATNOS_LOCAL_CAPABILITY_EXPIRES_AT -ErrorAction SilentlyContinue
        $env:ATNOS_WEBVIEW2_USER_DATA_DIR = $webView2ProfileDir

        if (Test-ManagedDesktopShellTrusted) {
            try {
                [void](Start-DesktopShellProcess -FilePath $managedShellExe -Label "managed desktop shell (Atnos Shell)")
                return
            }
            catch {
                Write-LauncherLog "Managed desktop shell failed: $($_.Exception.Message)"
            }
        }
        elseif (Test-Path $managedShellExe) {
            Write-LauncherLog "Managed desktop shell is present but blocked by ATNOS_REQUIRE_SIGNED_DESKTOP_SHELL."
        }

        if (Test-Path $desktopShell) {
            try {
                [void](Start-DesktopShellProcess -FilePath $desktopShell -Label "Atnos Desktop" -WindowWaitSeconds 35)
                return
            }
            catch {
                Write-LauncherLog "Atnos Desktop failed: $($_.Exception.Message)"
            }
        }

        if (Test-Path $portableShell) {
            try {
                [void](Start-DesktopShellProcess -FilePath $portableShell -Label "Atnos Portable" -WindowWaitSeconds 35)
                return
            }
            catch {
                Write-LauncherLog "Atnos Portable failed: $($_.Exception.Message)"
            }
        }
    }
    finally {
        $env:ATNOS_LOCAL_CAPABILITY_TOKEN = $capabilityToken
        $env:ATNOS_LOCAL_CAPABILITY_EXPIRES_AT = $capabilityExpiry
    }

    throw @"
Nie udało się otworzyć Atnosa jako aplikacji desktopowej (Atnos Shell / Desktop / Portable).
Sprawdź WebView2 Runtime oraz log: $launcherLog
Celowo nie otwieram przeglądarki - Atnos ma działać jako aplikacja, nie jako strona WWW.
"@
}

function Ensure-RuntimeWatchdog {
    if ($NoDesktopShell) {
        return
    }

    $watchdogScript = Join-Path $appRoot "AtnosRuntimeWatchdog.ps1"
    if (-not (Test-Path $watchdogScript)) {
        Write-LauncherLog "Runtime watchdog script is missing: $watchdogScript"
        return
    }

    $existingWatchdog = Get-AtnosOwnedProcesses | Where-Object { $_.role -eq 'watchdog' } | Select-Object -First 1
    if ($existingWatchdog) {
        Write-LauncherLog "Runtime watchdog already active (PID=$($existingWatchdog.ProcessId))."
        return
    }

    try {
        Start-Process `
            -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $watchdogScript) `
            -WorkingDirectory $appRoot `
            -WindowStyle Hidden | Out-Null
        Write-LauncherLog "Started runtime watchdog (health=/health/ready)."
    }
    catch {
        Write-LauncherLog "Could not start runtime watchdog: $($_.Exception.Message)"
    }
}

try {
    $launcherMutexAcquired = Acquire-LauncherMutex -TimeoutMs 0
    if (-not $launcherMutexAcquired) {
        Write-LauncherLog "Another launcher instance is already starting ATNOS. Waiting for existing startup."
        $startupWaitResult = Wait-For-ExistingStartup -MaxWaitSec 90
        if ($startupWaitResult -eq "services_ready") {
            $launcherMutexAcquired = Acquire-LauncherMutex -TimeoutMs 30000
            if (-not $launcherMutexAcquired) {
                throw "Runtime dziala, ale launcher nie zwolnil blokady w wymaganym czasie."
            }
            if (-not $NoDesktopShell) {
                Initialize-LocalCapabilitySession
                Open-AtnosWindow
            }
            if ($ModelsOnly) { Ensure-OllamaRuntime }
            exit 0
        }

        $launcherMutexAcquired = Acquire-LauncherMutex -TimeoutMs 30000
        if (-not $launcherMutexAcquired) {
            throw "ATNOS jest juz uruchamiany przez inny proces launchera."
        }
    }

    Write-LauncherLog "Launcher started."
    Write-LauncherState -Phase "launcher-started"
    if ($ModelsOnly) {
        Ensure-OllamaRuntime
        Write-LauncherLog "Model-only startup finished; application services and active production were not restarted."
        exit 0
    }
    Initialize-LocalCapabilitySession
    [void](Cleanup-OrphanedManagedServiceProcesses -CommandPattern "serve_spa\.py" -ServiceName "web shell" -Port 3000)
    Ensure-Api
    if (-not $ServicesOnly) { [void](Ensure-FrontendDeployment) }
    Ensure-Web
    if ($StartModels) { Ensure-OllamaRuntime }
    if (-not $NoDesktopShell) {
        [void](Ensure-UiBootstrap)
        Open-AtnosWindow
        Ensure-RuntimeWatchdog
    }
    else {
        Write-LauncherLog "Runtime services ready without opening desktop shell."
    }
}
catch {
    if ($launcherMutexAcquired) {
        Write-LauncherState -Phase "failed"
    }
    $startupSummary = Get-ApiStartupSummary
    $message = "ATNOS nie wystartowal.`n`n$($_.Exception.Message)`n`nAPI startup: $startupSummary`n`nLog launchera:`n$launcherLog"
    Write-LauncherLog "Launcher failed: $($_.Exception.Message) | startup=$startupSummary | line=$($_.InvocationInfo.ScriptLineNumber) | command=$($_.InvocationInfo.Line.Trim()) | stack=$($_.ScriptStackTrace)"
    if (-not $NoErrorDialog) {
        [System.Windows.MessageBox]::Show($message, "ATNOS start error", "OK", "Error") | Out-Null
    }
    exit 1
}
finally {
    if ($launcherMutexAcquired) {
        Clear-LauncherState
    }
    if ($launcherMutexAcquired) {
        $launcherMutex.ReleaseMutex()
    }
    $launcherMutex.Dispose()
}
