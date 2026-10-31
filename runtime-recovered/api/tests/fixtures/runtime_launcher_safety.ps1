param([string]$appRoot = 'E:\Atnos\App')
$ErrorActionPreference = 'Stop'
function Import-FunctionFromScript {
    param([string]$Path,[string]$Name)
    $tokens=$null; $errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw "Parse failure in $Path" }
    $fn=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name},$true)
    if (-not $fn) { throw "Missing function $Name" }
    Set-Item -Path "Function:script:$Name" -Value ([scriptblock]::Create($fn.Body.Extent.Text.TrimStart('{').TrimEnd('}')))
}
function Assert-True { param($Condition,[string]$Message); if(-not $Condition){throw $Message} }
function Write-LauncherLog {param($Message)}
function Write-LauncherState {param($Phase)}
function Write-WatchdogLog {param($Message)}
function Write-StartupLog {param($Message)}
function Start-Process {throw 'Unexpected process launch'}
function Stop-Process {throw 'Unexpected process kill'}
function Stop-ManagedServiceProcesses {throw 'Unexpected managed stop'}
function Stop-ProcessByIdSafe {param($ProcessId,$ServiceName);throw "Unexpected identity-checked service stop: $ServiceName PID=$ProcessId"}
function Get-ListeningProcessId {param($Port); return $script:listener}
function Get-ManagedServiceProcesses {param($CommandPattern);return $script:managed}
# Windows venv's redirector remains alive while its base-Python child serves the
# socket. Both command lines are owned API processes; the child is not a duplicate.
# Never look up synthetic PIDs in the host's real process table.
function Get-CimInstance {
    param($ClassName,$Filter,$ErrorAction)
    if ($ClassName -ne 'Win32_Process') {throw "Unexpected CIM class: $ClassName"}
    if ($Filter) {
        if ($Filter -notmatch '^ProcessId\s*=\s*(\d+)$') {throw "Unexpected CIM filter: $Filter"}
        $requestedProcessId=[int]$Matches[1]
        $result=@($script:processTable | Where-Object {$_.ProcessId -eq $requestedProcessId})
        if (-not $result.Count) {throw "Synthetic process is unavailable: PID=$requestedProcessId"}
        return $result
    }
    return $script:processTable
}
function Get-Process {param($Name,$ErrorAction);return @([pscustomobject]@{Id=999;Path='C:\Foreign\ollama.exe'})}
Import-FunctionFromScript "$appRoot\scripts\AtnosOllamaRuntime.ps1" 'Get-AtnosOllamaEndpoint'
Import-FunctionFromScript "$appRoot\Launch-Atnos.ps1" 'Ensure-OllamaRuntime'
$script:listener=999
Ensure-OllamaRuntime
Write-Output 'PASS: existing foreign/model process is preserved, no start/stop.'

Import-FunctionFromScript "$appRoot\Launch-Atnos.ps1" 'Cleanup-OrphanedManagedServiceProcesses'
$script:managed=@([pscustomobject]@{ProcessId=10},[pscustomobject]@{ProcessId=11})
$script:processTable=@(
    [pscustomobject]@{ProcessId=10;ParentProcessId=5;Name='python.exe';ExecutablePath="$appRoot\runtime-recovered\api\.venv\Scripts\python.exe"},
    [pscustomobject]@{ProcessId=11;ParentProcessId=10;Name='python.exe';ExecutablePath='E:\Atnos\Tools\Python314\python.exe'}
)
$script:listener=11
Assert-True ((Cleanup-OrphanedManagedServiceProcesses -CommandPattern 'api' -ServiceName 'API' -Port 19001) -eq 11) 'Venv family not retained'
$script:listener=999
$refused=$false
try { Cleanup-OrphanedManagedServiceProcesses -CommandPattern 'api' -ServiceName 'API' -Port 19001 } catch { $refused=$_.Exception.Message -match 'foreign or unverifiable' }
Assert-True $refused 'Foreign port owner was not refused'
$script:listener=$null
Assert-True ((Cleanup-OrphanedManagedServiceProcesses -CommandPattern 'api' -ServiceName 'API' -Port 19001) -eq 10) 'Starting service duplicated'
$script:managed=@([pscustomobject]@{ProcessId=11},[pscustomobject]@{ProcessId=10})
Assert-True ((Cleanup-OrphanedManagedServiceProcesses -CommandPattern 'api' -ServiceName 'API' -Port 19001) -eq 11) 'Child-first process inventory was not reused safely'
$script:managed=@([pscustomobject]@{ProcessId=10},[pscustomobject]@{ProcessId=11})
Write-Output 'PASS: venv family preserved; foreign listener rejected; existing startup reused.'

Import-FunctionFromScript "$appRoot\Launch-Atnos.ps1" 'Ensure-Api'
function Test-ServiceStale {param($Port,$WatchPaths);return $true}
function Test-ServiceHealthyFast {param($Url,$ServiceName,$CommandPattern,$Port);return $true}
function Test-LocalCapabilitySession {return $true}
$apiRoot=Join-Path $appRoot 'runtime-recovered\api'
$RestartBackend=$false
$script:listener=11
Ensure-Api
Write-Output 'PASS: source modification does not restart a healthy API.'

Import-FunctionFromScript "$appRoot\AtnosRuntimeWatchdog.ps1" 'Clear-StuckApiListener'
Assert-True (-not (Clear-StuckApiListener)) 'Watchdog attempted listener cleanup'
Import-FunctionFromScript "$appRoot\Launch-Atnos.ps1" 'Try-Recover-StaleLauncher'
Assert-True (-not (Try-Recover-StaleLauncher)) 'Old heartbeat authorized stop'
Import-FunctionFromScript "$appRoot\Start-ComfyUI.ps1" 'Stop-KnownComfyOwner'
Assert-True (-not (Stop-KnownComfyOwner ([pscustomobject]@{Pid=999;Kind='comfy_python_other'}))) 'Foreign Comfy stop accepted'
Write-Output 'PASS: stale heartbeat/status and Comfy names never authorize killing.'

Import-FunctionFromScript "$appRoot\AtnosRuntimeWatchdog.ps1" 'Start-AtnosServiceRecovery'
$launcherScript=Join-Path $appRoot 'Launch-Atnos.ps1'
function Start-Process {param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle); $script:calledArgs=$ArgumentList; Assert-True ($WindowStyle -eq 'Hidden') 'Visible recovery console'}
Assert-True (Start-AtnosServiceRecovery) 'Recovery dispatch failed'
Assert-True ($script:calledArgs -contains '-ServicesOnly') 'Recovery may start models'
Write-Output 'PASS: watchdog dispatches only service recovery, hidden.'

Import-FunctionFromScript "$appRoot\scripts\Start-AtnosRuntime.ps1" 'Start-Ollama'
$SkipOllama=$false
function Get-NetTCPConnection {param($LocalPort,$State,$ErrorAction);return $null}
function Start-LoggedProcess {throw 'Unexpected model launch'}
Start-Ollama
Write-Output 'PASS: development launcher preserves a foreign Ollama owner too.'
