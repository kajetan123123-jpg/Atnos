param(
    [switch]$Pull
)

$ErrorActionPreference = "Stop"
$composeDir = "E:\Atnos\App\infra\n8n"
$envFile = Join-Path $composeDir ".env"
$example = Join-Path $composeDir ".env.example"

if (-not (Test-Path $envFile)) {
    Copy-Item -LiteralPath $example -Destination $envFile
    Write-Host "Created $envFile from .env.example"
}

$virtualizationEnabled = $null
try {
    $virtualizationEnabled = (Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1 -ExpandProperty VirtualizationFirmwareEnabled)
} catch {
    $virtualizationEnabled = $null
}

if ($virtualizationEnabled -eq $false) {
    Write-Warning "Docker unavailable: Windows reports VirtualizationFirmwareEnabled=False."
    Write-Warning "Enable SVM Mode in BIOS first. Fallback without Docker:"
    Write-Warning "E:\Atnos\App\scripts\Run-YoutubeDailyOps.ps1"
    throw "docker_unavailable_virtualization_disabled"
}

docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Docker daemon is not available. Start Docker Desktop or use fallback:"
    Write-Warning "E:\Atnos\App\scripts\Run-YoutubeDailyOps.ps1"
    throw "docker_daemon_unavailable"
}

New-Item -ItemType Directory -Force -Path "E:\Atnos\App\data\n8n\home" | Out-Null
New-Item -ItemType Directory -Force -Path "E:\Atnos\App\data\n8n\youtube\approvals\pending" | Out-Null
New-Item -ItemType Directory -Force -Path "E:\Atnos\App\data\n8n\youtube\approvals\approved" | Out-Null
New-Item -ItemType Directory -Force -Path "E:\Atnos\App\data\n8n\youtube\results" | Out-Null

Push-Location $composeDir
try {
    if ($Pull) {
        docker-compose --env-file .env pull
        if ($LASTEXITCODE -ne 0) {
            throw "docker-compose pull failed. Docker Desktop/daemon is probably not running."
        }
    }
    docker-compose --env-file .env up -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker-compose up failed. Start Docker Desktop/daemon and rerun this script."
    }
    Write-Host "n8n starting at http://127.0.0.1:5678"
    Write-Host "Import templates with: E:\Atnos\App\scripts\Import-N8nWorkflows.ps1"
} finally {
    Pop-Location
}
