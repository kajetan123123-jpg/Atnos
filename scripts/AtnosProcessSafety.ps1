# Shared by the desktop launcher and explicit stop; no model termination API.
function Invoke-AtnosProcessSafety {
    param([string[]]$SafetyArguments)
    $safetyPython = Join-Path $appRoot 'runtime-recovered\api\.venv\Scripts\python.exe'
    $safetyScript = Join-Path $appRoot 'scripts\runtime_process_safety.py'
    $result = & $safetyPython -B $safetyScript @SafetyArguments
    if ($LASTEXITCODE -ne 0) { throw "Process safety refused: $result" }
    return ($result | ConvertFrom-Json)
}

function Get-AtnosOwnedProcesses {
    return @(Invoke-AtnosProcessSafety -SafetyArguments @('list'))
}

function Stop-AtnosOwnedProcess {
    param([int]$ProcessId, [switch]$AllowApiStop)
    if ($ProcessId -eq $PID) { throw 'Cannot stop the calling process' }
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
    $identity = Invoke-AtnosProcessSafety -SafetyArguments @('inspect', '--pid', "$ProcessId")
    if (-not $identity.role) { throw "Foreign or unverifiable process PID=$ProcessId" }
    $createdText = [string]::Format([Globalization.CultureInfo]::InvariantCulture, '{0:R}', [double]$identity.created)
    $argsForSafety = @('stop', '--pid', "$ProcessId", '--created', $createdText)
    if ($AllowApiStop) { $argsForSafety += '--allow-api-stop' }
    return Invoke-AtnosProcessSafety -SafetyArguments $argsForSafety
}
