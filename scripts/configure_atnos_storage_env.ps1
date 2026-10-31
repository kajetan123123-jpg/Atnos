param(
    [switch]$Apply,
    [switch]$SetUserTempForAllApps
)

$ErrorActionPreference = "Stop"

$root = "E:\Atnos\App"
$data = Join-Path $root "data"
$toolCaches = Join-Path $data "tool-caches"
$reportDir = Join-Path $data "reports"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $reportDir "atnos_storage_env_config_$timestamp.md"

$directories = @(
    $data,
    (Join-Path $data "temp"),
    (Join-Path $data "cache"),
    (Join-Path $data "logs"),
    (Join-Path $data "reports"),
    (Join-Path $data "user-inputs"),
    (Join-Path $data "codex-artifacts"),
    (Join-Path $data "visual-qa\captures"),
    (Join-Path $data "visual-qa\recordings"),
    (Join-Path $data "visual-qa\reviews"),
    (Join-Path $data "visual-qa\codex-handoff"),
    (Join-Path $data "self-improvement\codex-handoff"),
    (Join-Path $data "self-improvement\education-quality-loop\reports"),
    (Join-Path $data "self-improvement\education-quality-loop\learning-briefs"),
    (Join-Path $data "browser_profiles\default"),
    (Join-Path $data "browser_downloads"),
    (Join-Path $data "browser_screenshots"),
    (Join-Path $data "models\ollama"),
    (Join-Path $data "secrets\ssh"),
    (Join-Path $toolCaches "codex-runtimes"),
    (Join-Path $toolCaches "codex\sessions"),
    (Join-Path $toolCaches "codex\archived_sessions"),
    (Join-Path $toolCaches "codex\tmp"),
    (Join-Path $toolCaches "npm-cache"),
    (Join-Path $toolCaches "pip-cache"),
    (Join-Path $toolCaches "ms-playwright"),
    (Join-Path $toolCaches "xdg-cache"),
    (Join-Path $toolCaches "huggingface\transformers"),
    (Join-Path $toolCaches "torch"),
    (Join-Path $toolCaches "uv")
)

$envVars = [ordered]@{
    APP_ROOT = $root
    ATNOS_ROOT = $root
    ATNOS_DATA_DIR = $data
    ATNOS_CACHE_DIR = (Join-Path $data "cache")
    ATNOS_TEMP_DIR = (Join-Path $data "temp")
    PLAYWRIGHT_BROWSERS_PATH = (Join-Path $toolCaches "ms-playwright")
    PIP_CACHE_DIR = (Join-Path $toolCaches "pip-cache")
    PIP_CONFIG_FILE = (Join-Path $root "pip.ini")
    npm_config_cache = (Join-Path $toolCaches "npm-cache")
    XDG_CACHE_HOME = (Join-Path $toolCaches "xdg-cache")
    HF_HOME = (Join-Path $toolCaches "huggingface")
    TRANSFORMERS_CACHE = (Join-Path $toolCaches "huggingface\transformers")
    TORCH_HOME = (Join-Path $toolCaches "torch")
    UV_CACHE_DIR = (Join-Path $toolCaches "uv")
    OLLAMA_MODELS = "E:\Atnos\Models\ollama"
    SHORTS_OUTPUT_DIR = (Join-Path $data "shorts")
    BROWSER_USER_DATA_DIR = (Join-Path $data "browser_profiles\default")
    BROWSER_DOWNLOAD_DIR = (Join-Path $data "browser_downloads")
    BROWSER_SCREENSHOT_DIR = (Join-Path $data "browser_screenshots")
    BROWSER_LOG_PATH = (Join-Path $data "logs\browser_actions.jsonl")
    ATNOS_VISUAL_QA_DIR = (Join-Path $data "visual-qa")
    ATNOS_VISUAL_QA_LOG_PATH = (Join-Path $data "logs\visual_qa.jsonl")
    ATNOS_VISUAL_QA_ALLOW_CLOUD = "false"
    ATNOS_SELF_IMPROVEMENT_ALLOW_SELF_APPLY = "false"
    ATNOS_SELF_IMPROVEMENT_FINAL_REVIEWER = "codex"
    ATNOS_SELF_IMPROVEMENT_HANDOFF_DIR = (Join-Path $data "self-improvement\codex-handoff")
    ATNOS_SELF_IMPROVEMENT_LOG_PATH = (Join-Path $data "logs\self_improvement_governance.jsonl")
    ATNOS_UBUNTU_KEY = (Join-Path $data "secrets\ssh\atnos_lab")
}

if ($SetUserTempForAllApps) {
    $envVars["TEMP"] = (Join-Path $data "temp")
    $envVars["TMP"] = (Join-Path $data "temp")
}

Write-Host "Atnos storage environment"
Write-Host "Mode: $(if ($Apply) { 'APPLY' } else { 'DRY RUN' })"
Write-Host ""

if ($Apply) {
    foreach ($dir in $directories) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    foreach ($entry in $envVars.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "User")
    }
} else {
    Write-Host "No changes made. Add -Apply to create folders and set User environment variables."
}

New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

$lines = @(
    "# Atnos Storage Environment",
    "",
    "- generated_at: $(Get-Date -Format o)",
    "- mode: $(if ($Apply) { 'APPLY' } else { 'DRY_RUN' })",
    "- set_user_temp_for_all_apps: $SetUserTempForAllApps",
    "",
    "## Directories",
    ""
)

foreach ($dir in $directories) {
    $exists = Test-Path -LiteralPath $dir
    $lines += "- [$exists] $dir"
}

$lines += @(
    "",
    "## User Environment Variables",
    ""
)

foreach ($entry in $envVars.GetEnumerator()) {
    $current = [Environment]::GetEnvironmentVariable($entry.Key, "User")
    $status = if ($current -eq $entry.Value) { "OK" } else { "PENDING_OR_DIFFERENT" }
    $lines += "- $($entry.Key)=$($entry.Value) [$status]"
}

$lines += @(
    "",
    "## Notes",
    "",
    "- Launcher process-level TEMP/TMP is always forced to E:\\Atnos\\App\\data\\temp.",
    "- This script does not move Windows system files, pagefile, hiberfil, or unrelated Downloads.",
    "- Restart shells/dev servers after applying User environment changes."
)

$lines | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host ""
Write-Host "Report: $reportPath"
