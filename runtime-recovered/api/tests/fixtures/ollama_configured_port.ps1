param([string]$appRoot, [string]$testRoot)
$ErrorActionPreference = 'Stop'
function Import-FunctionFromScript {
    param([string]$Path, [string]$Name)
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw "Parse failure in $Path" }
    $fn=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name},$true)
    if (-not $fn) { throw "Missing $Name" }
    Set-Item -Path "Function:script:$Name" -Value ([scriptblock]::Create($fn.Body.Extent.Text.TrimStart('{').TrimEnd('}')))
}
function Assert-True { param($Condition, [string]$Message); if (-not $Condition) {throw $Message} }
function Write-LauncherLog {param($Message)}
function Stop-Process {throw 'No model process may be stopped'}
function Get-ListeningProcessId {param($Port); $script:checkedPort=$Port; return $script:listener}
function Get-NetTCPConnection {param($LocalPort,$State,$ErrorAction); if ($script:listener) {return [pscustomobject]@{OwningProcess=$script:listener}} }
function Get-Process {
    param($Id,$Name,$ErrorAction)
    if ($Name) {return [pscustomobject]@{Id=999;Path='C:\Foreign\ollama.exe'}}
    if ($Id -eq 4001) {return $script:ownedProcess}
    if ($Id -eq 999) {return [pscustomobject]@{Id=999;Path='C:\Foreign\ollama.exe'}}
    throw 'No such process'
}
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,$RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru)
    Assert-True ($FilePath -eq 'E:\Atnos\Storage\Tools\Ollama\ollama.exe') 'Wrong executable'
    Assert-True ($WindowStyle -eq 'Hidden') 'Model window is not hidden'
    Assert-True ($env:OLLAMA_HOST -eq '127.0.0.1:11435') 'Wrong serve port'
    Assert-True ($env:OLLAMA_MODELS -eq 'E:\Atnos\Models\ollama') 'Wrong model store'
    Assert-True ($env:USERPROFILE -like 'E:\*' -and $env:TEMP -like 'E:\*') 'Child writes outside E:'
    $script:starts++
    return $script:ownedProcess
}
Import-FunctionFromScript "$appRoot\scripts\AtnosOllamaRuntime.ps1" 'Get-AtnosOllamaEndpoint'
Import-FunctionFromScript "$appRoot\Launch-Atnos.ps1" 'Ensure-OllamaRuntime'
Import-FunctionFromScript "$appRoot\Launch-Atnos.ps1" 'Test-OllamaModelsOnE'
$runtimeEnvFile=Join-Path $testRoot '.env'
$runtimeLogRoot=Join-Path $testRoot 'runtime'
$atnosTempDir=Join-Path $testRoot 'temp'
$atnosCacheDir=Join-Path $testRoot 'cache'
$ollamaModelsDir='E:\Atnos\Models\ollama'
New-Item -ItemType Directory -Force -Path $runtimeLogRoot,$atnosTempDir,$atnosCacheDir | Out-Null
'OLLAMA_BASE_URL=http://127.0.0.1:11435' | Set-Content -LiteralPath $runtimeEnvFile
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
$endpoint=Get-AtnosOllamaEndpoint -EnvFile $runtimeEnvFile
Assert-True ($endpoint.Port -eq 11435 -and $endpoint.Host -eq '127.0.0.1:11435') 'Config not respected'
Write-Output 'PASS: canonical configuration overrides a stale caller endpoint.'
$rejected=$false
'OLLAMA_BASE_URL=http://example.com:11435' | Set-Content -LiteralPath $runtimeEnvFile
try { Get-AtnosOllamaEndpoint -EnvFile $runtimeEnvFile | Out-Null } catch {$rejected=$true}
Assert-True $rejected 'Nonlocal endpoint was accepted by model launcher'
'OLLAMA_BASE_URL=http://127.0.0.1:11435' | Set-Content -LiteralPath $runtimeEnvFile
Write-Output 'PASS: unsafe nonlocal model bind is rejected.'
$script:starts=0; $script:listener=$null
$script:ownedProcess=[pscustomobject]@{Id=4001;Path='E:\Atnos\Storage\Tools\Ollama\ollama.exe';StartTime=[datetime]'2026-09-30T10:20:30Z'}
$callerModelHost=$env:OLLAMA_HOST
$callerProfile=$env:USERPROFILE
Ensure-OllamaRuntime
Assert-True ($script:starts -eq 1 -and $script:checkedPort -eq 11435) 'Foreign runtime blocked isolated start'
Assert-True ($env:OLLAMA_HOST -eq $callerModelHost -and $env:USERPROFILE -eq $callerProfile) 'Caller environment was not restored'
$owner=Get-Content -LiteralPath (Join-Path $runtimeLogRoot 'ollama-owner-11435.json') -Raw | ConvertFrom-Json
Assert-True ($owner.endpoint -eq 'http://127.0.0.1:11435' -and $owner.pid -eq 4001) 'Owner state is inaccurate'
Write-Output 'PASS: foreign runtime on another port does not block owned E: startup.'
Ensure-OllamaRuntime
Assert-True ($script:starts -eq 1) 'Owned startup was duplicated'
Write-Output 'PASS: exact owned startup identity prevents duplicate model launch.'
$script:listener=999
Ensure-OllamaRuntime
Assert-True ($script:starts -eq 1) 'Occupied foreign configured port was changed'
Write-Output 'PASS: foreign owner on configured port is preserved without start or stop.'
$script:listener=4001; $script:modelsValid=$false
function Test-OllamaModelsOnE {param($ModelsDir,$BaseUrl); $script:checkedBaseUrl=$BaseUrl; return $script:modelsValid}
Ensure-OllamaRuntime
Assert-True ($script:starts -eq 1 -and $script:checkedBaseUrl -eq 'http://127.0.0.1:11435') 'Model verification used wrong endpoint'
Write-Output 'PASS: E: digest verification is requested from configured port.'
$script:modelsValid=$true
Ensure-OllamaRuntime
Assert-True ($script:starts -eq 1) 'Verified configured runtime was restarted'
Write-Output 'PASS: verified E: configured listener is reused.'
