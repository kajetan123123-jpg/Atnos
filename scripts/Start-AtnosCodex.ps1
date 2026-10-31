$CheckOnly = $false
$CodexArguments = @()
foreach ($argument in $args) {
    if ([string]::Equals($argument, '-CheckOnly', [StringComparison]::OrdinalIgnoreCase)) { $CheckOnly = $true }
    else { $CodexArguments += [string]$argument }
}

$ErrorActionPreference = 'Stop'
$appRoot = 'E:\Atnos\App'
$nodePath = 'E:\Atnos\Tools\NodeJS\node.exe'
$cliPath = 'E:\Atnos\Tools\CodexCLI\node_modules\@openai\codex\bin\codex.js'
$profileRoot = 'E:\Atnos\Profiles\CodexCLI'
$tempRoot = 'E:\Atnos\Temp\atnos-codex-cli'
$childEnvironment = [ordered]@{
    CODEX_HOME = "$profileRoot\.codex"; HOME = $profileRoot; USERPROFILE = $profileRoot
    APPDATA = "$profileRoot\AppData\Roaming"; LOCALAPPDATA = "$profileRoot\AppData\Local"
    TEMP = $tempRoot; TMP = $tempRoot; XDG_CACHE_HOME = "$profileRoot\cache"; XDG_CONFIG_HOME = "$profileRoot\config"
    NPM_CONFIG_CACHE = "$profileRoot\npm-cache"; COREPACK_HOME = "$profileRoot\corepack"
    PYTHONPYCACHEPREFIX = "$tempRoot\pycache"; NODE_REPL_HISTORY = "$profileRoot\node-repl-history"
}
$savedEnvironment = @{}
$launchExitCode = 1
$normalCliStarted = $false
$locationPushed = $false
try {
    Push-Location -LiteralPath $appRoot
    $locationPushed = $true
    $workspaceJson = & (Join-Path $PSScriptRoot 'Test-AtnosCodexWorkspace.ps1')
    $workspaceExitCode = $LASTEXITCODE
    $workspace = $workspaceJson | ConvertFrom-Json
    $errors = @($workspace.errors)
    if ($workspaceExitCode -ne 0 -and $errors.Count -eq 0) { $errors += 'Preflight zwrócił błąd.' }
    foreach ($executablePath in @($nodePath, $cliPath)) {
        if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) { $errors += "Brak narzędzia na E: $executablePath" }
    }
    foreach ($argument in $CodexArguments) {
        if ($argument -cmatch '^(-C|--cd)(=|$)' -or $argument -cmatch '^-C.+') {
            $errors += 'Launcher Atnosa ustawia cwd E:\Atnos\App; usuń własny argument -C/--cd.'
        }
    }
    if ($errors.Count -gt 0 -or $CheckOnly) {
        [ordered]@{ checkedAtUtc = [DateTime]::UtcNow.ToString('o'); passed = $errors.Count -eq 0
            checkOnly = [bool]$CheckOnly; workspace = $workspace; nodePath = $nodePath; cliPath = $cliPath
            childEnvironment = $childEnvironment; normalCliStarted = $false; authenticationCopied = $false
            limitation = 'Kontrola plików nie potwierdza indeksu skills hosta ani zmiany cwd bieżącego czatu desktopowego. Oddzielny profil na E: wymaga własnego logowania CLI użytkownika.'
            errors = $errors } | ConvertTo-Json -Depth 7
        if ($errors.Count -eq 0) { $launchExitCode = 0 }
    } else {
        foreach ($key in $childEnvironment.Keys) {
            $savedEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            $directory = if ($key -eq 'NODE_REPL_HISTORY') { Split-Path -Parent $childEnvironment[$key] } else { $childEnvironment[$key] }
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
            [Environment]::SetEnvironmentVariable($key, $childEnvironment[$key], 'Process')
        }
        # Tylko jawne wywołanie użytkownika uruchamia sesję CLI; preflight nie wykonuje RPC ani logowania.
        $normalCliStarted = $true
        & $nodePath $cliPath --cd $appRoot @CodexArguments
        $launchExitCode = $LASTEXITCODE
    }
} catch {
    [ordered]@{ passed = $false; checkOnly = [bool]$CheckOnly; normalCliStarted = $normalCliStarted; errors = @($_.Exception.Message) } | ConvertTo-Json
} finally {
    foreach ($key in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], 'Process') }
    if ($locationPushed) { Pop-Location }
}
exit $launchExitCode
