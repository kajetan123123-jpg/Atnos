param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$auditRoot = 'E:\Atnos\App\reports\readiness-20260909'
$auditState = Join-Path $auditRoot 'isolated-runtime'
New-Item -ItemType Directory -Force -Path $auditState | Out-Null
$env:APP_ENV = 'test'
$env:APP_ROOT = 'E:\Atnos\App'
$env:ATNOS_ROOT = $env:APP_ROOT
$env:ATNOS_ENV_FILE = Join-Path $auditState 'empty.env'
if (!(Test-Path -LiteralPath $env:ATNOS_ENV_FILE)) { Set-Content -LiteralPath $env:ATNOS_ENV_FILE -Value '# Isolated local runtime, no external credentials.' }
$env:DATA_DIR = Join-Path $auditState 'data'
$env:ATNOS_DATA_DIR = $env:DATA_DIR
$env:ATNOS_STATE_DIR = $env:DATA_DIR
$env:SQLITE_FILE = Join-Path $env:DATA_DIR 'atnos.db'
$env:RUNTIME_DIR = Join-Path $auditState 'runtime'
$env:ACCOUNT_SECRET_VAULT_DIR = Join-Path $env:DATA_DIR 'account-secrets'
$env:TEMP = 'E:\Atnos\Temp'
$env:TMP = $env:TEMP
$env:ATNOS_TEMP_DIR = $env:TEMP
$env:ATNOS_CACHE_DIR = Join-Path $auditState 'cache'
$env:XDG_CACHE_HOME = $env:ATNOS_CACHE_DIR
$env:HF_HOME = 'E:\Atnos\ToolCaches\huggingface'
$env:TORCH_HOME = 'E:\Atnos\ToolCaches\torch'
$env:PYTHONIOENCODING = 'utf-8'
$env:SHORTS_AUTOPILOT_ENABLED = 'false'
$env:SHORTS_QUALITY_REVIEW_SCHEDULER_ENABLED = 'false'
$env:SHORTS_QUALITY_AUTO_REMEDIATION_ENABLED = 'false'
$env:INVESTMENT_PAPER_AUTOPILOT_ENABLED = 'false'
$env:SCHEDULED_TASKS_ENABLED = 'false'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:OLLAMA_MODELS = 'E:\Atnos\Models\ollama'
$env:Path = 'E:\Atnos\Tools\FFmpeg\bin;E:\Atnos\Tools\NodeJS;' + $env:Path
& 'E:\Atnos\App\runtime-recovered\api\.venv\Scripts\python.exe' 'E:\Atnos\App\runtime-recovered\api\scripts\run_atnos_api.py' --port $Port
