param(
    [switch]$Strict
)

$ErrorActionPreference = "Stop"

$root = "E:\Atnos\App"
$profileRoot = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos"
$stateRoot = Join-Path $profileRoot "data"
$allowedSystemPrefixes = @(
    "C:\Windows",
    "C:\Program Files",
    "C:\Program Files (x86)"
)

$checks = @(
    @{ Name = "APP_ROOT"; ExpectedPrefix = "E:\Atnos\App" },
    @{ Name = "ATNOS_DATA_DIR"; ExpectedPrefix = $stateRoot },
    @{ Name = "ATNOS_CACHE_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "ATNOS_TEMP_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "PLAYWRIGHT_BROWSERS_PATH"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "PIP_CACHE_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "npm_config_cache"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "OLLAMA_MODELS"; ExpectedPrefix = "E:\Atnos\Models\ollama" },
    @{ Name = "SHORTS_OUTPUT_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "BROWSER_USER_DATA_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "BROWSER_DOWNLOAD_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "BROWSER_SCREENSHOT_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "BROWSER_LOG_PATH"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "ATNOS_VISUAL_QA_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "ATNOS_VISUAL_QA_LOG_PATH"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "ATNOS_SELF_IMPROVEMENT_HANDOFF_DIR"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "ATNOS_SELF_IMPROVEMENT_LOG_PATH"; ExpectedPrefix = "E:\Atnos\App\data" },
    @{ Name = "ATNOS_UBUNTU_KEY"; ExpectedPrefix = "E:\Atnos\App\data" }
)

$failures = @()

foreach ($check in $checks) {
    $value = [Environment]::GetEnvironmentVariable($check.Name, "User")
    if (-not $value) {
        $failures += "$($check.Name) is not set in User environment."
        continue
    }

    if (-not $value.StartsWith($check.ExpectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $failures += "$($check.Name) points outside expected storage: $value"
    }
}

$envFiles = @(
    (Join-Path $root "runtime-recovered\api\.env"),
    (Join-Path $root "runtime-recovered\api\.env.example"),
    (Join-Path $root "data\jarvis\watch_folders.json")
)

foreach ($file in $envFiles) {
    if (-not (Test-Path -LiteralPath $file)) {
        continue
    }

    $matches = Select-String -LiteralPath $file -Pattern "C:\\Users\\localuser|C:/Users/localuser" -ErrorAction SilentlyContinue
    foreach ($match in $matches) {
        $failures += "Config still references C user path: ${file}:$($match.LineNumber): $($match.Line.Trim())"
    }
}

Write-Host "Atnos storage assertion"
Write-Host "Checked root: $root"
Write-Host ""

if ($failures.Count -eq 0) {
    Write-Host "PASSED: Atnos runtime configuration points to E: storage."
    exit 0
}

Write-Host "FAILED:"
foreach ($failure in $failures) {
    Write-Host "- $failure"
}

if ($Strict) {
    exit 1
}

exit 0
