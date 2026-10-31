param(
    [switch]$Probe
)

$ErrorActionPreference = "Stop"

$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ApiRoot = Join-Path $AppRoot "runtime-recovered\api"
$Python = Join-Path $ApiRoot ".venv\Scripts\python.exe"
$LoginScript = Join-Path $ApiRoot "examples\youtube_first_login.py"
$DataRoot = if ($env:ATNOS_STATE_DIR) {
    [IO.Path]::GetFullPath($env:ATNOS_STATE_DIR)
} elseif ($env:DATA_DIR) {
    [IO.Path]::GetFullPath($env:DATA_DIR)
} else {
    "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data"
}
$ClientSecret = if ($env:YOUTUBE_CLIENT_SECRET_FILE) {
    [IO.Path]::GetFullPath($env:YOUTUBE_CLIENT_SECRET_FILE)
} else {
    Join-Path (Split-Path -Parent $DataRoot) "config\oauth\youtube_client_secret.json"
}
$TokenPath = Join-Path $DataRoot "youtube\token.json"
$env:ATNOS_STATE_DIR = $DataRoot
$env:DATA_DIR = $DataRoot
$env:YOUTUBE_CLIENT_SECRET_FILE = $ClientSecret

Write-Host "Atnos YouTube OAuth setup"
Write-Host "Client secret: $ClientSecret"
Write-Host "Token path:    $TokenPath"
Write-Host ""

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

if (-not (Test-Path $LoginScript)) {
    throw "YouTube login helper not found: $LoginScript"
}

if (-not (Test-Path $ClientSecret)) {
    Write-Host "STATUS: missing_client_secret" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Next manual step:"
    Write-Host "1. Open Google Cloud Console."
    Write-Host "2. Enable YouTube Data API v3 and YouTube Analytics API."
    Write-Host "3. Create OAuth Client ID as Desktop App."
    Write-Host "4. Download the JSON and import it through Atnos Settings, or save it exactly as:"
    Write-Host "   $ClientSecret"
    Write-Host ""
    Write-Host "Then rerun:"
    Write-Host "   $($MyInvocation.MyCommand.Path) -Probe"
    exit 2
}

$argsList = @($LoginScript)
if ($Probe) {
    $argsList += "--probe"
}

& $Python @argsList
