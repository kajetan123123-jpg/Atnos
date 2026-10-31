$ErrorActionPreference = "Stop"

Write-Host "Checking virtualization..."
try {
    Get-CimInstance -ClassName Win32_Processor |
        Select-Object -First 1 Name, VirtualizationFirmwareEnabled |
        Format-List
} catch {
    Write-Warning "Could not read virtualization status: $($_.Exception.Message)"
}

Write-Host "Checking n8n container..."
try {
    docker ps --filter "name=atnos-n8n"
} catch {
    Write-Warning "Docker unavailable. If VirtualizationFirmwareEnabled=False, use fallback: E:\Atnos\App\scripts\Run-YoutubeDailyOps.ps1"
}

Write-Host "Checking n8n HTTP..."
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5678" -TimeoutSec 5 | Select-Object StatusCode, StatusDescription
} catch {
    Write-Warning "n8n HTTP not ready: $($_.Exception.Message)"
}

Write-Host "Checking Atnos n8n endpoint..."
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/n8n/status" -TimeoutSec 5 | Select-Object -ExpandProperty Content
} catch {
    Write-Warning "Atnos API not ready or endpoint unavailable: $($_.Exception.Message)"
}

Write-Host "Checking fallback runner..."
if (Test-Path -LiteralPath "E:\Atnos\App\scripts\Run-YoutubeDailyOps.ps1") {
    Write-Host "Fallback runner available: E:\Atnos\App\scripts\Run-YoutubeDailyOps.ps1"
} else {
    Write-Warning "Fallback runner missing."
}
