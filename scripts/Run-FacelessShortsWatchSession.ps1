param(
    [switch]$WriteDefaultConfig,
    [switch]$OverwriteConfig,
    [switch]$PromptOnly,
    [switch]$NoRenderVideo,
    [string]$ConfigJson = "",
    [string]$BenchmarkJson = "",
    [string]$ReferenceObservationJson = "",
    [string]$VisualPlanJson = "",
    [string]$SourceShortDir = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$ApiRoot = "E:\Atnos\App\runtime-recovered\api"
$Python = Join-Path $ApiRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $ApiRoot "scripts\run_faceless_watch_session.py"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}
if (-not (Test-Path $Runner)) {
    throw "Watch session runner not found: $Runner"
}

$argsList = @($Runner)
if ($WriteDefaultConfig) { $argsList += "--write-default-config" }
if ($OverwriteConfig) { $argsList += "--overwrite-config" }
if ($PromptOnly) { $argsList += "--prompt-only" }
if ($NoRenderVideo) { $argsList += "--no-render-video" }
if ($ConfigJson.Trim()) { $argsList += @("--config-json", $ConfigJson) }
if ($BenchmarkJson.Trim()) { $argsList += @("--benchmark-json", $BenchmarkJson) }
if ($ReferenceObservationJson.Trim()) { $argsList += @("--reference-observation-json", $ReferenceObservationJson) }
if ($VisualPlanJson.Trim()) { $argsList += @("--visual-plan-json", $VisualPlanJson) }
if ($SourceShortDir.Trim()) { $argsList += @("--source-short-dir", $SourceShortDir) }
if ($OutputDir.Trim()) { $argsList += @("--output-dir", $OutputDir) }

Push-Location $ApiRoot
try {
    $env:PYTHONIOENCODING = "utf-8"
    & $Python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
