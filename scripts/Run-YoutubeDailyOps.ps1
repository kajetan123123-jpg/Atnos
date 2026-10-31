param(
    [switch]$CreateApproval,
    [int]$Limit = 20,
    [string]$PythonExe = "E:\Atnos\App\runtime-recovered\api\.venv\Scripts\python.exe",
    [string]$ApiDir = "E:\Atnos\App\runtime-recovered\api"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

if (-not (Test-Path -LiteralPath $ApiDir)) {
    throw "Atnos API directory not found: $ApiDir"
}

Push-Location $ApiDir
try {
    $createApprovalValue = if ($CreateApproval) { "True" } else { "False" }
    $script = @"
from __future__ import annotations

import json

from atnos.core.config import Settings
from atnos.services.n8n_youtube_automation_service import N8nYouTubeAutomationService

settings = Settings()
service = N8nYouTubeAutomationService(settings)
result = service.run_daily_ops(create_approval=$createApprovalValue, limit=$Limit)
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
"@
    $script | & $PythonExe -
    if ($LASTEXITCODE -ne 0) {
        throw "YouTube daily ops failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
