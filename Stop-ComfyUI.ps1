param([int]$Port = 8188, [switch]$ForceUnknown)
$ErrorActionPreference = 'Stop'
# The former PID/port/name sweep is intentionally removed, including ForceUnknown.
if ($ForceUnknown) { throw 'Stopping unknown processes is disabled.' }
$appRoot = $PSScriptRoot
$env:APP_ROOT = $appRoot
$env:DATA_DIR = 'E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data'
$env:ATNOS_ENV_FILE = 'E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\config\.env'
$env:TEMP = Join-Path $env:DATA_DIR 'temp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = Join-Path $appRoot 'runtime-recovered\api\src'
& (Join-Path $appRoot 'runtime-recovered\api\.venv\Scripts\python.exe') -B (Join-Path $appRoot 'runtime-recovered\api\scripts\stop_comfy_runtime.py') --port $Port
exit $LASTEXITCODE
