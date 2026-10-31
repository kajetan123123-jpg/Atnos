param(
    [string]$RepoRoot = "",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [int]$ComfyPort = 8188,
    [int]$F5TtsPort = 7860,
    [switch]$SkipFrontend,
    [switch]$SkipOllama,
    [switch]$SkipComfyUI,
    [switch]$SkipF5Tts,
    [switch]$RestartBackend,
    [switch]$StartModels
)

# DEV / HARNESS ONLY.
# Canonical personal desktop app start is: E:\Atnos\Start-Atnos.cmd → Launch-Atnos.ps1
# (API :8000 + SPA :3000 + WebView2 shell). Do not use this script for daily PC use —
# it starts Vite :5173 and can leave orphan processes next to the real app.

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = if ($env:APP_ROOT) {
        $env:APP_ROOT
    } else {
        (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    }
}

$appRoot = $RepoRoot
. (Join-Path $RepoRoot 'scripts\AtnosProcessSafety.ps1')
$ApiRoot = Join-Path $RepoRoot "runtime-recovered\api"
$FrontendRoot = Join-Path $RepoRoot "frontend"
$LogRoot = Join-Path $ApiRoot "logs\runtime-launcher"
$PythonExe = Join-Path $ApiRoot ".venv\Scripts\python.exe"
$NodeExe = "E:\Atnos\Tools\NodeJS\node.exe"
$PnpmCmd = Join-Path $FrontendRoot "node_modules\.bin\pnpm.cmd"
$OllamaModelsDir = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { "E:\Atnos\Models\ollama" }

$env:OLLAMA_MODELS = $OllamaModelsDir
$env:OLLAMA_HOST = "127.0.0.1:11434"
$env:OLLAMA_KEEP_ALIVE = "0"
$env:OLLAMA_NUM_PARALLEL = "1"
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_VULKAN = "1"
if (Test-Path -LiteralPath $NodeExe) {
    # Keep the Atnos-owned Node runtime ahead of system and Codex caches.
    $nodeDirectory = Split-Path -Parent $NodeExe
    if ($env:PATH -notlike "*$nodeDirectory*") {
        $env:PATH = "$nodeDirectory;$env:PATH"
    }
}
New-Item -ItemType Directory -Force -Path $OllamaModelsDir | Out-Null

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

function Test-HttpReady {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return [int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 300
    } catch {
        return $false
    }
}

function Test-BackendContract {
    $openApiUrl = "http://127.0.0.1:$BackendPort/openapi.json"
    $requiredRoutes = @(
        "/api/project-command/{project_id}/execute",
        "/knowledge/refresh-index"
    )
    try {
        $openApi = Invoke-RestMethod -Uri $openApiUrl -TimeoutSec 10
        $availableRoutes = @($openApi.paths.PSObject.Properties.Name)
        foreach ($route in $requiredRoutes) {
            if ($availableRoutes -notcontains $route) {
                return $false
            }
        }
        return $true
    } catch {
        return $false
    }
}

function Stop-AtnosBackendOnPort {
    $listeners = @(Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($processId in $listeners) {
        try { [void](Stop-AtnosOwnedProcess -ProcessId $processId -AllowApiStop) }
        catch { Write-Host "[backend] restart deferred: $($_.Exception.Message)"; return $false }
    }
    return $true
}

function Wait-BackendContract {
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendContract) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-LoggedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string]$Arguments,
        [string]$WorkingDirectory
    )
    if (-not (Test-Path -LiteralPath $FilePath)) {
        Write-Host "[$Name] missing executable: $FilePath" -ForegroundColor Yellow
        return $false
    }

    $stdout = Join-Path $LogRoot "$Name.out.log"
    $stderr = Join-Path $LogRoot "$Name.err.log"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath (Join-Path $LogRoot "$Name.pid") -Value $process.Id
    Write-Host "[$Name] started pid=$($process.Id)" -ForegroundColor Green
    return $true
}

function Start-Backend {
    $url = "http://127.0.0.1:$BackendPort/health"
    if (Test-HttpReady $url) {
        if ($RestartBackend) {
            Write-Host "[backend] explicit restart requested; restarting only the recognized Atnos backend" -ForegroundColor Yellow
            if (-not (Stop-AtnosBackendOnPort)) {
                return
            }
        } elseif (Test-BackendContract) {
            Write-Host "[backend] ready with current Project Command routes at $url" -ForegroundColor Green
            return
        } else {
            Write-Host '[backend] contract update pending; use explicit idle restart.'
            return
        }
    }
    if (Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue) {
        Write-Host '[backend] port occupied; existing process preserved.'
        return
    }
    if (Get-AtnosOwnedProcesses | Where-Object { $_.role -eq 'api' }) {
        Write-Host '[backend] existing API startup preserved.'
        return
    }
    $started = Start-LoggedProcess `
        -Name "backend" `
        -FilePath $PythonExe `
        -Arguments "scripts\run_atnos_api.py --app atnos.api.main:create_app --host 127.0.0.1 --port $BackendPort" `
        -WorkingDirectory $ApiRoot
    if (-not $started) {
        return
    }
    if (Wait-BackendContract) {
        Write-Host "[backend] current Project Command routes are ready at $url" -ForegroundColor Green
    } else {
        Write-Host "[backend] started but required Project Command routes did not become ready within 45 seconds" -ForegroundColor Yellow
    }
}

function Start-Frontend {
    if ($SkipFrontend) {
        Write-Host "[frontend] skipped" -ForegroundColor DarkGray
        return
    }
    $url = "http://127.0.0.1:$FrontendPort"
    if (Test-HttpReady $url) {
        Write-Host "[frontend] ready at $url" -ForegroundColor Green
        return
    }
    if (Test-Path -LiteralPath $PnpmCmd) {
        Start-LoggedProcess `
            -Name "frontend" `
            -FilePath $PnpmCmd `
            -Arguments "dev --host 127.0.0.1 --port $FrontendPort" `
            -WorkingDirectory $FrontendRoot | Out-Null
        return
    }
    if (Test-Path -LiteralPath $NodeExe) {
        Start-LoggedProcess `
            -Name "frontend" `
            -FilePath $NodeExe `
            -Arguments ".\node_modules\vite\bin\vite.js --host 127.0.0.1 --port $FrontendPort" `
            -WorkingDirectory $FrontendRoot | Out-Null
        return
    }
    Write-Host "[frontend] missing Node/pnpm runtime" -ForegroundColor Yellow
}

function Start-Ollama {
    if ($SkipOllama) { return }
    $canonicalOllamaExe = 'E:\Atnos\Storage\Tools\Ollama\ollama.exe'
    if ((Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue) -or
        (Get-Process -Name 'ollama', 'ollama app' -ErrorAction SilentlyContinue)) {
        Write-Host '[ollama] existing runtime preserved; missing models never trigger a restart.'
        return
    }
    [void](Start-LoggedProcess -Name 'ollama' -FilePath $canonicalOllamaExe -Arguments 'serve' -WorkingDirectory $RepoRoot)
}

function Start-ComfyUI {
    if ($SkipComfyUI) {
        Write-Host "[comfyui] skipped" -ForegroundColor DarkGray
        return
    }
    $url = "http://127.0.0.1:$ComfyPort/system_stats"
    if (Test-HttpReady $url) {
        # Port HTTP alone is not ownership proof - require managed marker argv
        Write-Host "[comfyui] HTTP ready at $url - verifying via Start-ComfyUI.ps1 (managed ownership)" -ForegroundColor DarkGray
    }
    $desktopLauncher = Join-Path $RepoRoot "Start-ComfyUI.ps1"
    if (-not (Test-Path -LiteralPath $desktopLauncher)) {
        Write-Host "[comfyui] missing managed launcher: $desktopLauncher" -ForegroundColor Yellow
        return
    }

    & powershell.exe `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $desktopLauncher `
        -Port $ComfyPort
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[comfyui] managed Comfy not ready on port $ComfyPort (exit $LASTEXITCODE)" -ForegroundColor Yellow
    } else {
        Write-Host "[comfyui] managed Comfy ready at $url" -ForegroundColor Green
    }
}

function Start-F5Tts {
    if ($SkipF5Tts) {
        Write-Host "[f5-tts] skipped" -ForegroundColor DarkGray
        return
    }
    $url = "http://127.0.0.1:$F5TtsPort"
    if (Test-HttpReady $url) {
        Write-Host "[f5-tts] ready at $url" -ForegroundColor Green
        return
    }
    if (-not $env:F5_TTS_LAUNCH_COMMAND) {
        Write-Host "[f5-tts] missing F5_TTS_LAUNCH_COMMAND env value" -ForegroundColor Yellow
        return
    }
    Start-LoggedProcess `
        -Name "f5-tts" `
        -FilePath "powershell.exe" `
        -Arguments "-NoProfile -ExecutionPolicy Bypass -Command $env:F5_TTS_LAUNCH_COMMAND" `
        -WorkingDirectory $RepoRoot | Out-Null
}

Write-Host "Starting Atnos runtime from $RepoRoot" -ForegroundColor Cyan
$startupMutex = New-Object System.Threading.Mutex($false, 'Local\AtnosLauncherV2')
$startupAcquired = $false
try {
    try { $startupAcquired = $startupMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $startupAcquired = $true }
    if (-not $startupAcquired) { throw 'Atnos startup already in progress.' }
    Start-Backend
    Start-Frontend
    if ($StartModels) {
        Start-Ollama
        Start-ComfyUI
        Start-F5Tts
    }
} finally {
    if ($startupAcquired) { $startupMutex.ReleaseMutex() }
    $startupMutex.Dispose()
}
Write-Host "Runtime launcher finished. Logs: $LogRoot" -ForegroundColor Cyan
