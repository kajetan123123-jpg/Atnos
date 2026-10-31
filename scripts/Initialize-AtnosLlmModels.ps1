param(
    [switch]$Probe,
    [switch]$PullMissing
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiEnvPath = Join-Path $repoRoot "runtime-recovered\api\.env"
$dataDir = Join-Path $repoRoot "data"
if (Test-Path $apiEnvPath) {
    $dataDirLine = Get-Content -Path $apiEnvPath | Where-Object { $_ -match "^DATA_DIR=" } | Select-Object -First 1
    if ($dataDirLine) {
        $candidate = ($dataDirLine -replace "^DATA_DIR=", "").Trim()
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            $dataDir = $candidate
        }
    }
}
$statusDir = Join-Path $dataDir "runtime"
$statusPath = Join-Path $statusDir "llm_models_status.json"

$models = @(
    @{ name = "qwen3:14b"; role = "main_text"; required = $true; size = "~9.3GB" },
    @{ name = "qwen3:8b"; role = "fast_text"; required = $false; size = "~5.2GB" },
    @{ name = "qwen3-vl:8b"; role = "vision"; required = $true; size = "~6.1GB" }
)

function Get-OllamaModelNames {
    try {
        $raw = & ollama list 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or $null -eq $raw) {
        return $null
    }
    $names = @()
    foreach ($line in $raw | Select-Object -Skip 1) {
        $trimmed = [string]$line
        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }
        $first = ($trimmed -split "\s+")[0]
        if (-not [string]::IsNullOrWhiteSpace($first)) {
            $names += $first
        }
    }
    return $names
}

if (-not $Probe -and -not $PullMissing) {
    $Probe = $true
}

Write-Host "Atnos LLM model initialization"
Write-Host "Status path: $statusPath"

$available = Get-OllamaModelNames
if ($null -eq $available) {
    New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
    $payload = [ordered]@{
        status = "unreachable"
        ready = $false
        reason = "Ollama CLI is unavailable or Ollama is not running."
        checked_at = (Get-Date).ToUniversalTime().ToString("o")
        installed_models = @()
        missing_models = $models.name
        suggestions = @("Start Ollama and rerun this script.", "ollama pull qwen3:14b")
    }
    $payload | ConvertTo-Json -Depth 6 | Set-Content -Path $statusPath -Encoding UTF8
    Write-Host "STATUS: unreachable" -ForegroundColor Red
    Write-Host "Run: ollama serve"
    exit 2
}

$missing = @()
foreach ($model in $models) {
    $present = $available -contains $model.name
    if ($present) {
        Write-Host "[OK] $($model.name) ($($model.role)) is installed" -ForegroundColor Green
    } else {
        Write-Host "[MISSING] $($model.name) ($($model.role), $($model.size))" -ForegroundColor Yellow
        $missing += $model.name
        if ($PullMissing) {
            Write-Host "Pulling $($model.name)..." -ForegroundColor Cyan
            & ollama pull $model.name
            if ($LASTEXITCODE -ne 0) {
                throw "ollama pull failed for $($model.name)"
            }
        }
    }
}

if ($PullMissing) {
    $available = Get-OllamaModelNames
    if ($null -eq $available) {
        $available = @()
    }
    $missing = @($models | Where-Object { $available -notcontains $_.name } | ForEach-Object { $_.name })
}

$hasMain = $available -contains "qwen3:14b"
$hasFast = $available -contains "qwen3:8b"
$hasVision = $available -contains "qwen3-vl:8b"

if (-not $hasVision) {
    $status = "missing_vision_model"
    $ready = $false
    $reason = "Missing qwen3-vl:8b. Vision tasks are disabled."
} elseif ($hasMain) {
    $status = "ready"
    $ready = $true
    $reason = "Main text and vision models are installed."
} elseif ($hasFast) {
    $status = "fallback_only"
    $ready = $false
    $reason = "Missing qwen3:14b. Atnos can use qwen3:8b as fast fallback."
} else {
    $status = "missing_main_model"
    $ready = $false
    $reason = "Missing qwen3:14b and qwen3:8b."
}

New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
$payload = [ordered]@{
    status = $status
    ready = $ready
    reason = $reason
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
    main_text_model = "qwen3:14b"
    fast_text_model = "qwen3:8b"
    vision_model = "qwen3-vl:8b"
    installed_models = @($available)
    missing_models = @($missing)
    suggestions = @($missing | ForEach-Object { "ollama pull $_" })
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $statusPath -Encoding UTF8

Write-Host "STATUS: $status"
if ($missing.Count -gt 0 -and -not $PullMissing) {
    Write-Host "To pull missing models: E:\Atnos\App\scripts\Initialize-AtnosLlmModels.ps1 -PullMissing"
}
