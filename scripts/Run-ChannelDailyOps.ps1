param(
    [int]$MaxPerChannel = 1,
    [switch]$SeedExample,
    [switch]$YoutubeProbe,
    [switch]$ForceRepair,
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

$Runner = Join-Path $ApiDir "examples\run_daily_channel_ops.py"
if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Daily channel runner not found: $Runner"
}

$argsList = @($Runner, "--max-per-channel", [string]$MaxPerChannel)
if ($SeedExample) {
    $argsList += "--seed-example"
}
if ($YoutubeProbe) {
    $argsList += "--youtube-probe"
}
if ($ForceRepair) {
    $argsList += "--force-repair"
}

Push-Location $ApiDir
$previousPythonIoEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = "utf-8"
try {
    & $PythonExe @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "Channel daily ops failed with exit code $LASTEXITCODE"
    }
}
finally {
    if ($null -eq $previousPythonIoEncoding) {
        Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONIOENCODING = $previousPythonIoEncoding
    }
    Pop-Location
}
